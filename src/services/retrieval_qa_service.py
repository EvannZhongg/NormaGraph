from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
import hashlib
import json
import math
from pathlib import Path
import uuid
from typing import Any

from adapters.llm_client import EmbeddingsAPIClient, ResponseAPIError, ResponsesAPIClient
from core.config import AppConfig
from models.schemas import QuestionRequest, QuestionResponse
from prompts import (
    RAG_ANSWER_SYSTEM_PROMPT,
    RAG_SCOPE_ROUTING_SYSTEM_PROMPT,
    build_rag_answer_prompt,
    build_rag_scope_routing_prompt,
)


RAG_SCOPE_ROUTING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "selected_chapters": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "node_uid": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["node_uid", "reason"],
            },
        },
        "selected_sections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "node_uid": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["node_uid", "reason"],
            },
        },
    },
    "required": ["selected_chapters", "selected_sections"],
}


class RetrievalQAService:
    def __init__(
        self,
        config: AppConfig,
        *,
        llm_client: ResponsesAPIClient | None = None,
        embedding_client: EmbeddingsAPIClient | None = None,
    ) -> None:
        self.config = config
        self.llm_client = llm_client or ResponsesAPIClient(config)
        self.embedding_client = embedding_client or EmbeddingsAPIClient(config)
        self._space_cache: dict[str, dict[str, Any]] = {}

    def answer(self, request: QuestionRequest) -> QuestionResponse:
        standard_ids = request.standardIds or [space.name.replace("-", ":") for space in self.config.kg_spaces_dir.iterdir() if space.is_dir()]
        if not standard_ids:
            raise FileNotFoundError("No KG space was selected for QA.")
        if len(standard_ids) != 1:
            raise ValueError("QA currently supports exactly one kg-space per request.")

        standard_id = standard_ids[0]
        started_at = datetime.now(UTC)
        run_id = f"{started_at.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
        space = self._load_space(standard_id)
        self.llm_client.reset_stats()
        self.embedding_client.reset_stats()

        query_embedding = self.embedding_client.embed_texts([request.question])[0]
        routing = self._route_scope(request, space)
        retrieval_contexts = self._retrieve_contexts(
            space=space,
            query_embedding=query_embedding,
            routing=routing,
            top_k=request.topK,
            chunk_top_k=request.chunkTopK,
        )

        answer = self.llm_client.create_text_output(
            system_prompt=RAG_ANSWER_SYSTEM_PROMPT,
            user_prompt=build_rag_answer_prompt(
                question=request.question,
                standard_uid=space["standard_id"],
                retrieval_contexts=retrieval_contexts["contexts"],
                user_prompt=request.userPrompt,
            ),
        )
        completed_at = datetime.now(UTC)
        citations = retrieval_contexts["citations"]
        log_payload = {
            "run_id": run_id,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "question": request.question,
            "kg_space": {
                "standard_id": space["standard_id"],
                "space_dir": str(space["space_dir"]),
            },
            "request": request.model_dump(mode="json"),
            "routing": routing,
            "retrieval": {
                "query_mode": request.queryMode,
                "top_k": request.topK,
                "chunk_top_k": request.chunkTopK,
                "candidate_count": len(space["embedding_records"]),
                "returned_count": len(retrieval_contexts["contexts"]),
                "contexts": retrieval_contexts["contexts"],
            },
            "usage": {
                "llm": self.llm_client.snapshot_stats(),
                "embedding": self.embedding_client.snapshot_stats(),
            },
            "answer": answer,
            "citations": citations,
        }
        self._write_run_log(run_id, log_payload)
        return QuestionResponse(
            answer=answer,
            standardIds=[space["standard_id"]],
            citations=citations,
            graphHops=[
                {
                    "routing": routing,
                    "retrievedNodeIds": [item["node_uid"] for item in retrieval_contexts["citations"]],
                    "runId": run_id,
                }
            ],
        )

    def _route_scope(self, request: QuestionRequest, space: dict[str, Any]) -> dict[str, Any]:
        payload = self.llm_client.create_structured_output(
            system_prompt=RAG_SCOPE_ROUTING_SYSTEM_PROMPT,
            user_prompt=build_rag_scope_routing_prompt(
                question=request.question,
                standard_uid=space["standard_id"],
                chapters=space["chapters"],
                sections=space["sections"],
                top_k=request.topK,
            ),
            schema_name="rag_scope_routing",
            schema=RAG_SCOPE_ROUTING_SCHEMA,
        )
        selected_chapters = self._filter_selected_nodes(payload.get("selected_chapters") or [], space["chapter_ids"])
        selected_sections = self._filter_selected_nodes(payload.get("selected_sections") or [], space["section_ids"])
        return {
            "selected_chapters": selected_chapters,
            "selected_sections": selected_sections,
            "selected_chapter_ids": [item["node_uid"] for item in selected_chapters],
            "selected_section_ids": [item["node_uid"] for item in selected_sections],
        }

    def _retrieve_contexts(
        self,
        *,
        space: dict[str, Any],
        query_embedding: list[float],
        routing: dict[str, Any],
        top_k: int,
        chunk_top_k: int,
    ) -> dict[str, list[dict[str, Any]]]:
        selected_chapters = set(routing.get("selected_chapter_ids") or [])
        selected_sections = set(routing.get("selected_section_ids") or [])
        scope_node_ids = selected_chapters | selected_sections
        scoped_clause_ids = set()
        if scope_node_ids:
            for clause_uid, meta in space["clause_meta"].items():
                if meta.get("chapter_node_uid") in selected_chapters or meta.get("section_node_uid") in selected_sections:
                    scoped_clause_ids.add(clause_uid)

        scored: list[dict[str, Any]] = []
        for record in space["embedding_records"]:
            node_uid = record["node_uid"]
            node_type = record["node_type"]
            clause_uid = record.get("parent_clause_uid") or node_uid
            if scoped_clause_ids and clause_uid not in scoped_clause_ids:
                continue
            if node_type not in {"clause", "requirement"}:
                continue
            scored.append(
                {
                    "record": record,
                    "score": self._cosine_similarity(query_embedding, record["embedding"]),
                }
            )

        scored.sort(key=lambda item: item["score"], reverse=True)
        selected_records = [item["record"] for item in scored[: max(1, chunk_top_k)]]
        contexts = [self._build_context(space, record) for record in selected_records]
        citations = [self._build_citation(space, record) for record in selected_records]
        return {"contexts": contexts, "citations": citations}

    def _build_context(self, space: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
        node_uid = record["node_uid"]
        node_type = record["node_type"]
        requirement = space["requirements_by_uid"].get(node_uid) if node_type == "requirement" else None
        clause_uid = str((requirement or {}).get("parent_clause_uid") or node_uid)
        clause = space["clauses_by_uid"].get(clause_uid) or {}
        clause_meta = space["clause_meta"].get(clause_uid) or {}
        return {
            "node_type": node_type,
            "chapter_path": clause_meta.get("chapter_path") or clause.get("heading_path") or [],
            "clause_ref": clause.get("clause_ref") or (requirement or {}).get("clause_ref"),
            "clause_text": clause.get("source_text_normalized") or clause.get("source_text") or record.get("text") or "",
            "requirement_text": (requirement or {}).get("requirement_text"),
            "judgement_criteria": (requirement or {}).get("judgement_criteria") or [],
            "evidence_expected": (requirement or {}).get("evidence_expected") or [],
        }

    def _load_space(self, standard_id: str) -> dict[str, Any]:
        space_dir = self.config.kg_space_dir_for(standard_id)
        if not space_dir.exists():
            alt = self.config.kg_spaces_dir / standard_id.replace(":", "-")
            space_dir = alt if alt.exists() else space_dir
        if not space_dir.exists():
            raise FileNotFoundError(f"KG space {standard_id} was not found.")

        cache_key = str(space_dir.resolve())
        signature = self._space_signature(space_dir)
        cached = self._space_cache.get(cache_key)
        if cached and cached.get("signature") == signature:
            return cached

        nodes = self._read_json(space_dir / "graph_nodes.json")
        edges = self._read_json(space_dir / "graph_edges.json")
        clauses = self._read_json(space_dir / "clauses.json")
        requirements = self._read_json(space_dir / "requirements.json")
        embedding_records = self._read_embedding_store(space_dir / "embedding_store.jsonl")
        node_map = {str(node.get("node_uid") or ""): node for node in nodes if node.get("node_uid")}
        parent_by_id = self._build_parent_map(edges, node_map)
        clauses_by_uid = {str(item.get("clause_uid") or ""): item for item in clauses if item.get("clause_uid")}
        requirements_by_uid = {str(item.get("requirement_uid") or ""): item for item in requirements if item.get("requirement_uid")}
        clause_meta = {
            clause_uid: self._build_clause_meta(clause, node_map, parent_by_id)
            for clause_uid, clause in clauses_by_uid.items()
        }
        for record in embedding_records:
            if record["node_type"] == "requirement":
                requirement = requirements_by_uid.get(record["node_uid"]) or {}
                record["parent_clause_uid"] = requirement.get("parent_clause_uid")

        loaded = {
            "signature": signature,
            "standard_id": self._manifest_standard_id(space_dir, standard_id),
            "space_dir": space_dir,
            "node_map": node_map,
            "chapters": self._list_scope_nodes(node_map, "chapter"),
            "sections": self._list_scope_nodes(node_map, "section", parent_by_id=parent_by_id),
            "chapter_ids": {uid for uid, node in node_map.items() if node.get("node_type") == "chapter"},
            "section_ids": {uid for uid, node in node_map.items() if node.get("node_type") == "section"},
            "clauses_by_uid": clauses_by_uid,
            "requirements_by_uid": requirements_by_uid,
            "clause_meta": clause_meta,
            "embedding_records": embedding_records,
        }
        self._write_space_cache(loaded)
        self._space_cache[cache_key] = loaded
        return loaded

    def _build_clause_meta(
        self,
        clause: dict[str, Any],
        node_map: dict[str, dict[str, Any]],
        parent_by_id: dict[str, str],
    ) -> dict[str, Any]:
        clause_uid = str(clause.get("clause_uid") or "")
        parent_uid = str(clause.get("parent_uid") or parent_by_id.get(clause_uid) or "")
        section_uid = None
        chapter_uid = None
        if parent_uid:
            parent_node = node_map.get(parent_uid)
            if parent_node and parent_node.get("node_type") == "section":
                section_uid = parent_uid
                chapter_uid = parent_by_id.get(parent_uid)
            elif parent_node and parent_node.get("node_type") == "chapter":
                chapter_uid = parent_uid
        path = []
        for uid in [chapter_uid, section_uid]:
            node = node_map.get(uid or "")
            if not node:
                continue
            props = node.get("properties") or {}
            ref = props.get("ref")
            title = props.get("title") or node.get("label")
            path.append(" ".join(str(part) for part in [ref, title] if part).strip())
        return {
            "chapter_node_uid": chapter_uid,
            "section_node_uid": section_uid,
            "chapter_path": path or clause.get("heading_path") or [],
        }

    def _write_space_cache(self, space: dict[str, Any]) -> None:
        cache_dir = self.config.data_dir / "rag_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "standard_id": space["standard_id"],
            "space_dir": str(space["space_dir"]),
            "signature": space["signature"],
            "chapter_count": len(space["chapters"]),
            "section_count": len(space["sections"]),
            "embedding_record_count": len(space["embedding_records"]),
            "cached_at": datetime.now(UTC).isoformat(),
        }
        (cache_dir / f"{self._safe_filename(space['standard_id'])}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_run_log(self, run_id: str, payload: dict[str, Any]) -> None:
        run_dir = self.config.data_dir / "rag_runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _read_embedding_store(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            raise FileNotFoundError(f"Embedding store was not found: {path}")
        records: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            node_type = str(item.get("node_type") or "")
            if node_type not in {"clause", "requirement"}:
                continue
            embedding = item.get("embedding")
            if not isinstance(embedding, list) or not embedding:
                continue
            records.append(
                {
                    "node_uid": str(item.get("node_uid") or ""),
                    "standard_uid": item.get("standard_uid"),
                    "node_type": node_type,
                    "text": item.get("text") or "",
                    "embedding": [float(value) for value in embedding],
                }
            )
        return records

    @staticmethod
    def _filter_selected_nodes(raw_items: list[Any], allowed_ids: set[str]) -> list[dict[str, str]]:
        selected: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in raw_items:
            if isinstance(item, str):
                node_uid = item
                reason = ""
            elif isinstance(item, dict):
                node_uid = str(item.get("node_uid") or item.get("id") or "")
                reason = str(item.get("reason") or "")
            else:
                continue
            if node_uid not in allowed_ids or node_uid in seen:
                continue
            selected.append({"node_uid": node_uid, "reason": reason})
            seen.add(node_uid)
        return selected

    @staticmethod
    def _list_scope_nodes(
        node_map: dict[str, dict[str, Any]],
        node_type: str,
        *,
        parent_by_id: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        items = []
        for node_uid, node in node_map.items():
            if node.get("node_type") != node_type:
                continue
            props = node.get("properties") or {}
            items.append(
                {
                    "node_uid": node_uid,
                    "chapter_id": (parent_by_id or {}).get(node_uid),
                    "ref": props.get("ref"),
                    "title": props.get("title") or node.get("label"),
                    "summary": props.get("summary") or "",
                    "text_content": node.get("text_content") or "",
                }
            )
        items.sort(key=lambda item: str(item.get("ref") or item.get("node_uid") or ""))
        return items

    @staticmethod
    def _build_parent_map(edges: list[dict[str, Any]], node_map: dict[str, dict[str, Any]]) -> dict[str, str]:
        parent_by_id: dict[str, str] = {}
        for edge in edges:
            if str(edge.get("edge_type") or "").upper() != "CONTAINS":
                continue
            source_uid = str(edge.get("source_uid") or "")
            target_uid = str(edge.get("target_uid") or "")
            if source_uid in node_map and target_uid in node_map:
                parent_by_id[target_uid] = source_uid
        return parent_by_id

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        if not left or not right:
            return 0.0
        dot = 0.0
        left_norm = 0.0
        right_norm = 0.0
        for left_value, right_value in zip(left, right):
            dot += left_value * right_value
            left_norm += left_value * left_value
            right_norm += right_value * right_value
        denom = math.sqrt(left_norm) * math.sqrt(right_norm)
        return dot / denom if denom else 0.0

    def _build_citation(self, space: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
        node_uid = str(record.get("node_uid") or "")
        node_type = str(record.get("node_type") or "")
        requirement = space["requirements_by_uid"].get(node_uid) if node_type == "requirement" else None
        clause_uid = str((requirement or {}).get("parent_clause_uid") or node_uid)
        clause = space["clauses_by_uid"].get(clause_uid) or {}
        return {
            "node_uid": node_uid,
            "clause_ref": clause.get("clause_ref") or (requirement or {}).get("clause_ref"),
            "clause_uid": clause_uid,
        }

    @staticmethod
    def _read_json(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            raise FileNotFoundError(f"Required KG file was not found: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError(f"Required KG file must contain a JSON array: {path}")
        return payload

    @staticmethod
    def _manifest_standard_id(space_dir: Path, fallback: str) -> str:
        manifest_path = space_dir / "space_manifest.json"
        if not manifest_path.exists():
            return fallback
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return str(manifest.get("standard_id") or fallback)

    @staticmethod
    def _space_signature(space_dir: Path) -> str:
        digest = hashlib.sha256()
        for name in ["graph_nodes.json", "graph_edges.json", "clauses.json", "requirements.json", "embedding_store.jsonl"]:
            path = space_dir / name
            stat = path.stat()
            digest.update(f"{name}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8"))
        return digest.hexdigest()

    @staticmethod
    def _safe_filename(value: str) -> str:
        return value.replace(":", "-").replace("/", "-").replace("\\", "-")
