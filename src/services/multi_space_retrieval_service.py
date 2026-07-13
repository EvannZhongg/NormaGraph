from __future__ import annotations

from datetime import UTC, datetime
import json
import re
import uuid
from pathlib import Path
from typing import Any

from adapters.llm_client import EmbeddingsAPIClient, ResponseAPIError, ResponsesAPIClient
from core.config import AppConfig
from models.schemas import QuestionRequest, QuestionResponse
from prompts import RAG_ANSWER_SYSTEM_PROMPT, build_rag_answer_prompt
from services.retrieval_qa_service import RetrievalQAService


STANDARD_TOKEN_RE = re.compile(r"\b(?:gb/t|gb|sl|dl/t|sdj|slj)\s*[-/]?\s*\d+(?:[-:]\d{2,4})?\b", re.IGNORECASE)


class MultiSpaceRetrievalService(RetrievalQAService):
    def __init__(
        self,
        config: AppConfig,
        *,
        llm_client: ResponsesAPIClient | None = None,
        embedding_client: EmbeddingsAPIClient | None = None,
    ) -> None:
        super().__init__(config, llm_client=llm_client, embedding_client=embedding_client)
        self._profile_cache: dict[str, dict[str, Any]] = {}

    def answer(self, request: QuestionRequest) -> QuestionResponse:
        standard_ids = request.standardIds or self._list_available_standard_ids()
        if not standard_ids:
            raise FileNotFoundError("No KG space was selected for QA.")

        started_at = datetime.now(UTC)
        run_id = f"{started_at.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
        self.llm_client.reset_stats()
        self.embedding_client.reset_stats()

        query_embedding = self.embedding_client.embed_texts([request.question])[0]
        space_candidates = self._route_spaces(
            question=request.question,
            query_embedding=query_embedding,
            standard_ids=standard_ids,
            apply_global_limit=not request.standardIds,
        )
        if not space_candidates:
            raise FileNotFoundError("No matching KG space was found for QA.")

        all_contexts: list[dict[str, Any]] = []
        all_citations: list[dict[str, Any]] = []
        graph_hops: list[dict[str, Any]] = []
        for candidate in space_candidates:
            space = self._load_space(candidate["standard_id"])
            chapter_recall = self._recall_chapters(space=space, query_embedding=query_embedding)
            scoped_space = self._scope_space_for_chapters(space, chapter_recall["chapter_ids"])
            routing = self._route_scope_with_fallback(request, scoped_space, chapter_recall)
            retrieval = self._retrieve_contexts(
                space=space,
                query_embedding=query_embedding,
                routing=routing,
                top_k=request.topK,
                chunk_top_k=request.chunkTopK,
            )
            for context, citation in zip(retrieval["contexts"], retrieval["citations"]):
                chapter_score = self._chapter_score_for_context(space, context, chapter_recall["chapter_scores"])
                final_score = self._weighted_context_score(
                    vector_score=float(context.get("score") or 0.0),
                    space_score=float(candidate.get("score") or 0.0),
                    chapter_score=chapter_score,
                )
                expansion = self._expand_graph_context(space, context)
                context.update(
                    {
                        "space_score": candidate.get("score"),
                        "space_reason": candidate.get("reason"),
                        "chapter_score": chapter_score,
                        "final_score": final_score,
                        "graph_expansion": expansion,
                    }
                )
                citation.update(
                    {
                        "space_score": candidate.get("score"),
                        "chapter_score": chapter_score,
                        "final_score": final_score,
                    }
                )
                all_contexts.append(context)
                all_citations.append(citation)
            graph_hops.append(
                {
                    "standardId": space["standard_id"],
                    "spaceRouting": candidate,
                    "chapterRecall": chapter_recall,
                    "routing": routing,
                    "retrievedNodeIds": [item["node_uid"] for item in retrieval["citations"]],
                    "runId": run_id,
                }
            )

        selected_contexts = self._merge_contexts(all_contexts, max_contexts=request.chunkTopK)
        selected_node_keys = {(item.get("standard_uid"), item.get("node_uid")) for item in selected_contexts}
        selected_citations = [
            item
            for item in sorted(all_citations, key=lambda row: float(row.get("final_score") or 0.0), reverse=True)
            if (item.get("standard_id"), item.get("node_uid")) in selected_node_keys
        ][: len(selected_contexts)]

        answer = self.llm_client.create_text_output(
            system_prompt=RAG_ANSWER_SYSTEM_PROMPT,
            user_prompt=build_rag_answer_prompt(
                question=request.question,
                standard_uid="multi-space" if len(space_candidates) != 1 else space_candidates[0]["standard_id"],
                retrieval_contexts=selected_contexts,
                user_prompt=request.userPrompt,
            ),
        )
        completed_at = datetime.now(UTC)
        log_payload = {
            "run_id": run_id,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "question": request.question,
            "request": request.model_dump(mode="json"),
            "space_candidates": space_candidates,
            "retrieval": {
                "query_mode": request.queryMode,
                "top_k": request.topK,
                "chunk_top_k": request.chunkTopK,
                "candidate_context_count": len(all_contexts),
                "returned_count": len(selected_contexts),
                "contexts": selected_contexts,
            },
            "usage": {
                "llm": self.llm_client.snapshot_stats(),
                "embedding": self.embedding_client.snapshot_stats(),
            },
            "answer": answer,
            "citations": selected_citations,
        }
        self._write_run_log(run_id, log_payload)
        return QuestionResponse(
            answer=answer,
            standardIds=[item["standard_id"] for item in space_candidates],
            citations=selected_citations,
            graphHops=graph_hops,
        )

    def _route_spaces(
        self,
        *,
        question: str,
        query_embedding: list[float],
        standard_ids: list[str],
        apply_global_limit: bool,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for standard_id in dict.fromkeys(standard_ids):
            profile = self._load_space_profile(standard_id)
            if not profile:
                continue
            scope_score = 0.0
            if profile.get("scope_embedding"):
                scope_score = self._cosine_similarity(query_embedding, profile["scope_embedding"])
            best_chapter_score = 0.0
            for chapter in profile.get("chapter_embeddings") or []:
                best_chapter_score = max(best_chapter_score, self._cosine_similarity(query_embedding, chapter["embedding"]))
            keyword_score = self._keyword_score(question, profile.get("keywords") or [])
            explicit_score = self._explicit_standard_score(question, standard_id, profile)
            score = (0.65 * scope_score) + (0.20 * best_chapter_score) + (0.10 * keyword_score) + (0.05 * explicit_score)
            if explicit_score:
                score += 0.2
            candidates.append(
                {
                    "standard_id": standard_id,
                    "score": score,
                    "scope_score": scope_score,
                    "best_chapter_score": best_chapter_score,
                    "keyword_score": keyword_score,
                    "explicit_score": explicit_score,
                    "reason": self._space_reason(profile, scope_score, best_chapter_score, keyword_score, explicit_score),
                }
            )
        candidates.sort(key=lambda item: item["score"], reverse=True)
        if apply_global_limit:
            return candidates[: max(1, int(self.config.retrieval.global_top_k))]
        return candidates

    def _recall_chapters(self, *, space: dict[str, Any], query_embedding: list[float]) -> dict[str, Any]:
        profile = self._load_space_profile(space["standard_id"])
        scored: list[dict[str, Any]] = []
        for item in profile.get("chapter_embeddings") or []:
            node_uid = str(item.get("source_node_uid") or item.get("node_uid") or "").removesuffix("#summary")
            if node_uid not in space["chapter_ids"]:
                continue
            scored.append(
                {
                    "node_uid": node_uid,
                    "score": self._cosine_similarity(query_embedding, item["embedding"]),
                }
            )
        scored.sort(key=lambda item: item["score"], reverse=True)
        top_k = max(1, int(self.config.retrieval.chapter_top_k))
        selected = scored[:top_k]
        if not selected:
            selected = [{"node_uid": item["node_uid"], "score": 0.0} for item in space["chapters"][:top_k]]
        return {
            "chapter_ids": [item["node_uid"] for item in selected],
            "chapter_scores": {item["node_uid"]: float(item.get("score") or 0.0) for item in selected},
            "items": selected,
        }

    def _scope_space_for_chapters(self, space: dict[str, Any], chapter_ids: list[str]) -> dict[str, Any]:
        chapter_id_set = set(chapter_ids)
        if not chapter_id_set:
            return space
        section_ids = {
            section["node_uid"]
            for section in space["sections"]
            if section.get("chapter_id") in chapter_id_set
        }
        scoped = dict(space)
        scoped["chapters"] = [chapter for chapter in space["chapters"] if chapter["node_uid"] in chapter_id_set]
        scoped["sections"] = [section for section in space["sections"] if section["node_uid"] in section_ids]
        scoped["chapter_ids"] = {chapter["node_uid"] for chapter in scoped["chapters"]}
        scoped["section_ids"] = {section["node_uid"] for section in scoped["sections"]}
        return scoped

    def _route_scope_with_fallback(
        self,
        request: QuestionRequest,
        scoped_space: dict[str, Any],
        chapter_recall: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            routing = self._route_scope(request, scoped_space)
        except ResponseAPIError as exc:
            routing = {
                "selected_chapters": [{"node_uid": node_uid, "reason": f"chapter_summary_recall_fallback:{exc}"} for node_uid in chapter_recall["chapter_ids"]],
                "selected_sections": [],
                "selected_chapter_ids": list(chapter_recall["chapter_ids"]),
                "selected_section_ids": [],
            }
        if not routing.get("selected_chapter_ids") and not routing.get("selected_section_ids"):
            routing["selected_chapters"] = [{"node_uid": node_uid, "reason": "chapter_summary_recall_fallback"} for node_uid in chapter_recall["chapter_ids"]]
            routing["selected_chapter_ids"] = list(chapter_recall["chapter_ids"])
        return routing

    def _chapter_score_for_context(self, space: dict[str, Any], context: dict[str, Any], chapter_scores: dict[str, float]) -> float:
        clause_uid = str(context.get("clause_uid") or "")
        meta = space["clause_meta"].get(clause_uid) or {}
        chapter_uid = str(meta.get("chapter_node_uid") or "")
        return float(chapter_scores.get(chapter_uid, 0.0))

    @staticmethod
    def _weighted_context_score(*, vector_score: float, space_score: float, chapter_score: float) -> float:
        return (0.70 * vector_score) + (0.20 * space_score) + (0.10 * chapter_score)

    def _expand_graph_context(self, space: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
        max_nodes = max(0, int(self.config.retrieval.graph_expansion_max_nodes))
        if max_nodes == 0:
            return []
        node_map = space["node_map"]
        start_ids = {str(context.get("node_uid") or ""), str(context.get("clause_uid") or "")}
        start_ids = {node_uid for node_uid in start_ids if node_uid in node_map}
        expanded: list[dict[str, Any]] = []
        seen: set[str] = set()
        for edge in space.get("edges") or []:
            source_uid = str(edge.get("source_uid") or "")
            target_uid = str(edge.get("target_uid") or "")
            if source_uid not in start_ids and target_uid not in start_ids:
                continue
            neighbor_uid = target_uid if source_uid in start_ids else source_uid
            if neighbor_uid in seen or neighbor_uid not in node_map:
                continue
            seen.add(neighbor_uid)
            node = node_map[neighbor_uid]
            expanded.append(
                {
                    "node_uid": neighbor_uid,
                    "node_type": node.get("node_type"),
                    "label": self._node_label(node),
                    "edge_type": edge.get("edge_type"),
                    "text": self._preview_text(node.get("text_content") or self._node_label(node), 260),
                }
            )
            if len(expanded) >= max_nodes:
                break
        return expanded

    def _merge_contexts(self, contexts: list[dict[str, Any]], *, max_contexts: int) -> list[dict[str, Any]]:
        contexts = sorted(contexts, key=lambda item: float(item.get("final_score") or 0.0), reverse=True)
        limit = max(1, max_contexts)
        selected: list[dict[str, Any]] = []
        per_space_seen: set[str] = set()
        for context in contexts:
            standard_uid = str(context.get("standard_uid") or "")
            if standard_uid in per_space_seen:
                continue
            selected.append(context)
            per_space_seen.add(standard_uid)
            if len(selected) >= limit:
                return selected
        for context in contexts:
            key = (context.get("standard_uid"), context.get("node_uid"))
            if any((item.get("standard_uid"), item.get("node_uid")) == key for item in selected):
                continue
            selected.append(context)
            if len(selected) >= limit:
                break
        return selected

    def _load_space_profile(self, standard_id: str) -> dict[str, Any]:
        space_dir = self._resolve_space_dir(standard_id)
        cache_key = str(space_dir.resolve())
        cached = self._profile_cache.get(cache_key)
        signature = self._profile_signature(space_dir)
        if cached and cached.get("signature") == signature:
            return cached
        profile_path = space_dir / "kg_space_profile.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8")) if profile_path.exists() else {}
        manifest_id = self._manifest_standard_id(space_dir, standard_id)
        records = self._read_summary_embedding_records(space_dir / "embedding_store.jsonl")
        loaded = {
            "signature": signature,
            "standard_id": str(profile.get("standard_id") or manifest_id),
            "keywords": profile.get("keywords") or [],
            "scope_summary": profile.get("scope_summary") or "",
            "scope_embedding": next((item["embedding"] for item in records if item["node_type"] == "kg_scope_summary"), []),
            "chapter_embeddings": [item for item in records if item["node_type"] == "chapter_summary"],
        }
        self._profile_cache[cache_key] = loaded
        return loaded

    def _resolve_space_dir(self, standard_id: str) -> Path:
        space_dir = self.config.kg_space_dir_for(standard_id)
        if not space_dir.exists():
            alt = self.config.kg_spaces_dir / standard_id.replace(":", "-")
            space_dir = alt if alt.exists() else space_dir
        if not space_dir.exists():
            raise FileNotFoundError(f"KG space {standard_id} was not found.")
        return space_dir

    def _read_summary_embedding_records(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            node_type = str(item.get("node_type") or "")
            if node_type not in {"kg_scope_summary", "chapter_summary"}:
                continue
            embedding = item.get("embedding")
            if not isinstance(embedding, list) or not embedding:
                continue
            records.append(
                {
                    "node_uid": str(item.get("node_uid") or ""),
                    "source_node_uid": item.get("source_node_uid"),
                    "standard_uid": item.get("standard_uid"),
                    "node_type": node_type,
                    "text": item.get("text") or "",
                    "embedding": [float(value) for value in embedding],
                }
            )
        return records

    def _list_available_standard_ids(self) -> list[str]:
        ids: list[str] = []
        if not self.config.kg_spaces_dir.exists():
            return ids
        for path in sorted(item for item in self.config.kg_spaces_dir.iterdir() if item.is_dir()):
            if not (path / "graph_nodes.json").exists() or not (path / "embedding_store.jsonl").exists():
                continue
            ids.append(self._manifest_standard_id(path, path.name.replace("-", ":")))
        return ids

    def _keyword_score(self, question: str, keywords: list[Any]) -> float:
        normalized_question = self._normalize_for_match(question)
        if not normalized_question or not keywords:
            return 0.0
        hits = 0
        for keyword in keywords:
            normalized_keyword = self._normalize_for_match(str(keyword or ""))
            if normalized_keyword and normalized_keyword in normalized_question:
                hits += 1
        return min(1.0, hits / max(1, min(len(keywords), 8)))

    def _explicit_standard_score(self, question: str, standard_id: str, profile: dict[str, Any]) -> float:
        normalized_question = self._normalize_for_match(question)
        normalized_id = self._normalize_for_match(standard_id)
        compact_id = normalized_id.replace(":", "").replace("-", "")
        if normalized_id and normalized_id in normalized_question:
            return 1.0
        if compact_id and compact_id in normalized_question.replace(":", "").replace("-", ""):
            return 1.0
        for token in STANDARD_TOKEN_RE.findall(question):
            if self._normalize_for_match(token).replace("-", "").replace("/", "") in compact_id:
                return 1.0
        return 0.0

    @staticmethod
    def _space_reason(profile: dict[str, Any], scope_score: float, chapter_score: float, keyword_score: float, explicit_score: float) -> str:
        parts = [
            f"scope={scope_score:.3f}",
            f"chapter={chapter_score:.3f}",
            f"keyword={keyword_score:.3f}",
        ]
        if explicit_score:
            parts.append("explicit_standard_match")
        keywords = [str(item) for item in (profile.get("keywords") or [])[:5]]
        if keywords:
            parts.append("keywords=" + ",".join(keywords))
        return "; ".join(parts)

    @staticmethod
    def _profile_signature(space_dir: Path) -> str:
        parts = []
        for name in ["kg_space_profile.json", "embedding_store.jsonl"]:
            path = space_dir / name
            if not path.exists():
                continue
            stat = path.stat()
            parts.append(f"{name}:{stat.st_size}:{stat.st_mtime_ns}")
        return "|".join(parts)

    @staticmethod
    def _normalize_for_match(value: str) -> str:
        return re.sub(r"\s+", "", str(value or "").lower())

    @staticmethod
    def _node_label(node: dict[str, Any]) -> str:
        return str(node.get("label") or node.get("text_content") or node.get("node_uid") or "")

    @staticmethod
    def _preview_text(value: Any, max_chars: int) -> str:
        text = re.sub(r"\s+", " ", str(value or "").strip())
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3].rstrip() + "..."
