from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import sys
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from adapters.llm_client import ResponseAPIError, ResponsesAPIClient
from core.config import get_config


DEFAULT_ARTIFACT_DIR = "data/artifacts/1_sl-258-2017-dfbc2c54-891c5f31"
DEFAULT_KG_SPACE_DIR = "data/kg_spaces/sl258-2017"
DEFAULT_OUTPUT = "data/eval/sl258-2017-2hop-qa.json"


QA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "candidate_id",
                    "question",
                    "answer",
                    "reasoning",
                    "supporting_clause_refs",
                    "source_statement_refs",
                    "difficulty",
                    "quality_notes",
                ],
                "properties": {
                    "candidate_id": {"type": "string"},
                    "question": {"type": "string"},
                    "answer": {"type": "string"},
                    "reasoning": {"type": "string"},
                    "supporting_clause_refs": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "source_statement_refs": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "difficulty": {
                        "type": "string",
                        "enum": ["easy", "medium", "hard"],
                    },
                    "quality_notes": {"type": "string"},
                },
            },
        }
    },
}


SYSTEM_PROMPT = """你是水利水电规范 RAG 评测集构建助手。你需要基于给定的 2-hop 证据包生成中文问答。

要求：
1. 每个 candidate 生成 1 个问题和答案，candidate_id 必须原样返回。
2. 问题必须需要综合两个来源条文/要求才能回答，不能只问单条条文可直接回答的问题。
3. 答案必须只依据输入证据，不得补充外部知识、不得扩大适用范围。
4. 答案中应自然引用条款号，例如“依据 2.1.1 和 2.1.3……”，但不要编造条款。
5. reasoning 简要说明如何把两个证据点合并得到答案，适合后续做评测审计。
6. supporting_clause_refs 只填写实际用到的条款号；source_statement_refs 填实际用到的 requirement_uid。
7. 如果某个 candidate 证据不足以形成 2-hop 问题，请仍返回该 candidate_id，并在 question/answer 中给出保守可答内容，quality_notes 说明不足。
8. 只输出符合 JSON Schema 的 JSON。"""


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    concept_uid: str
    concept_label: str
    left_requirement_uid: str
    right_requirement_uid: str
    left_about_edge_uid: str
    right_about_edge_uid: str


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def stable_id(*parts: str, prefix: str = "qa-candidate") -> str:
    digest = hashlib.sha1("||".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}:{digest}"


def compact_text(value: str | None, max_chars: int) -> str:
    text = " ".join((value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"


def build_clause_sources(
    clauses: list[dict[str, Any]],
    requirements: dict[str, dict[str, Any]],
    requirement_uid: str,
    *,
    max_source_chars: int,
) -> dict[str, Any]:
    requirement = requirements[requirement_uid]
    clause_uid = requirement.get("parent_clause_uid")
    clause = next((item for item in clauses if item.get("clause_uid") == clause_uid), None) or {}
    return {
        "requirement_uid": requirement_uid,
        "requirement_text": compact_text(requirement.get("requirement_text"), max_source_chars),
        "modality": requirement.get("modality"),
        "subject": requirement.get("subject"),
        "action": requirement.get("action") or [],
        "object": requirement.get("object") or [],
        "applicability_rule": requirement.get("applicability_rule"),
        "judgement_criteria": requirement.get("judgement_criteria") or [],
        "evidence_expected": requirement.get("evidence_expected") or [],
        "domain_tags": requirement.get("domain_tags") or [],
        "clause_uid": clause_uid,
        "clause_ref": requirement.get("clause_ref") or clause.get("clause_ref"),
        "heading_path": clause.get("heading_path") or [],
        "source_page_span": requirement.get("source_page_span") or clause.get("source_page_span") or [],
        "source_text": compact_text(
            requirement.get("source_text_normalized")
            or requirement.get("source_text")
            or clause.get("source_text_normalized")
            or clause.get("source_text"),
            max_source_chars,
        ),
    }


def collect_2hop_candidates(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    requirements: dict[str, dict[str, Any]],
    *,
    candidate_count: int,
    seed: int,
    allow_same_clause: bool,
) -> list[Candidate]:
    node_by_uid = {node["node_uid"]: node for node in nodes}
    about_by_concept: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        if edge.get("edge_type") != "ABOUT":
            continue
        source_uid = edge.get("source_uid")
        target_uid = edge.get("target_uid")
        if source_uid not in requirements:
            continue
        target_node = node_by_uid.get(target_uid)
        if not target_node or target_node.get("node_type") != "concept":
            continue
        about_by_concept.setdefault(target_uid, []).append(edge)

    candidates: list[Candidate] = []
    seen_pairs: set[tuple[str, str, str]] = set()
    for concept_uid, concept_edges in sorted(about_by_concept.items()):
        if len(concept_edges) < 2:
            continue
        concept_node = node_by_uid[concept_uid]
        concept_label = concept_node.get("label") or concept_node.get("text_content") or concept_uid
        ordered_edges = sorted(concept_edges, key=lambda item: item.get("source_uid", ""))
        for left_index, left_edge in enumerate(ordered_edges):
            for right_edge in ordered_edges[left_index + 1 :]:
                left_uid = left_edge["source_uid"]
                right_uid = right_edge["source_uid"]
                left_clause = requirements[left_uid].get("parent_clause_uid")
                right_clause = requirements[right_uid].get("parent_clause_uid")
                if not allow_same_clause and left_clause == right_clause:
                    continue
                pair_key = (concept_uid, left_uid, right_uid)
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                candidates.append(
                    Candidate(
                        candidate_id=stable_id(concept_uid, left_uid, right_uid),
                        concept_uid=concept_uid,
                        concept_label=str(concept_label),
                        left_requirement_uid=left_uid,
                        right_requirement_uid=right_uid,
                        left_about_edge_uid=left_edge.get("edge_uid", ""),
                        right_about_edge_uid=right_edge.get("edge_uid", ""),
                    )
                )

    rng = random.Random(seed)
    rng.shuffle(candidates)
    return candidates[: max(1, candidate_count)]


def filter_candidates(
    candidates: list[Candidate],
    *,
    exclude_candidate_ids: set[str],
    candidate_count: int,
) -> list[Candidate]:
    filtered = [candidate for candidate in candidates if candidate.candidate_id not in exclude_candidate_ids]
    return filtered[: max(1, candidate_count)]


def candidate_to_prompt_item(
    candidate: Candidate,
    *,
    clauses: list[dict[str, Any]],
    requirements: dict[str, dict[str, Any]],
    max_source_chars: int,
) -> dict[str, Any]:
    left_source = build_clause_sources(
        clauses,
        requirements,
        candidate.left_requirement_uid,
        max_source_chars=max_source_chars,
    )
    right_source = build_clause_sources(
        clauses,
        requirements,
        candidate.right_requirement_uid,
        max_source_chars=max_source_chars,
    )
    return {
        "candidate_id": candidate.candidate_id,
        "task": "基于 shared_concept 将两个 requirement 综合成一个 2-hop 问答。",
        "shared_concept": {
            "concept_uid": candidate.concept_uid,
            "label": candidate.concept_label,
        },
        "graph_2hop_path": [
            {"node_uid": candidate.left_requirement_uid, "node_type": "requirement"},
            {
                "edge_uid": candidate.left_about_edge_uid,
                "edge_type": "ABOUT",
                "direction": "requirement_to_concept",
            },
            {"node_uid": candidate.concept_uid, "node_type": "concept"},
            {
                "edge_uid": candidate.right_about_edge_uid,
                "edge_type": "ABOUT",
                "direction": "concept_to_requirement",
            },
            {"node_uid": candidate.right_requirement_uid, "node_type": "requirement"},
        ],
        "sources": [left_source, right_source],
    }


def build_user_prompt(standard_id: str, candidates: list[dict[str, Any]]) -> str:
    payload = {
        "standard_id": standard_id,
        "question_style": "面向 RAG 检索评估的规范问答，问题应自然、明确、需要跨两个证据点回答。",
        "candidates": candidates,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def normalize_llm_items(raw_items: list[dict[str, Any]], candidates_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in raw_items:
        candidate_id = item.get("candidate_id")
        candidate = candidates_by_id.get(candidate_id)
        if not candidate:
            continue
        sources = candidate["sources"]
        citations = [
            {
                "clause_uid": source.get("clause_uid"),
                "clause_ref": source.get("clause_ref"),
                "requirement_uid": source.get("requirement_uid"),
                "page_span": source.get("source_page_span") or [],
                "heading_path": source.get("heading_path") or [],
                "source_text": source.get("source_text"),
            }
            for source in sources
        ]
        normalized.append(
            {
                "id": stable_id(candidate_id, item.get("question", ""), prefix="qa"),
                "candidate_id": candidate_id,
                "question": str(item.get("question", "")).strip(),
                "answer": str(item.get("answer", "")).strip(),
                "reasoning": str(item.get("reasoning", "")).strip(),
                "difficulty": normalize_difficulty(item.get("difficulty")),
                "supporting_clause_refs": item.get("supporting_clause_refs") or [],
                "source_statement_refs": item.get("source_statement_refs") or [],
                "quality_notes": str(item.get("quality_notes", "")).strip(),
                "shared_concept": candidate.get("shared_concept"),
                "graph_2hop_path": candidate.get("graph_2hop_path"),
                "citations": citations,
            }
        )
    return normalized


def extract_llm_items(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if not isinstance(result, dict):
        raise ResponseAPIError(f"LLM output was not a JSON object or array: {type(result).__name__}")

    for key in ("items", "qa_items", "questions", "data", "results"):
        value = result.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    for value in result.values():
        if isinstance(value, list) and all(isinstance(item, dict) for item in value):
            return value

    preview = json.dumps(result, ensure_ascii=False)[:1000]
    raise ResponseAPIError(f"LLM output did not contain an item array. Parsed output preview: {preview}")


def normalize_difficulty(value: Any) -> str:
    if value in {"easy", "medium", "hard"}:
        return str(value)
    return "medium"


def load_existing_report(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"metadata": {}, "items": []}
    data = read_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"Existing output file must be a JSON object: {path}")
    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError(f"Existing output file must contain an items array: {path}")
    return data


def merge_items(existing_items: list[dict[str, Any]], new_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_item_ids: set[str] = set()
    seen_candidate_ids: set[str] = set()
    for item in [*existing_items, *new_items]:
        item_id = item.get("id")
        candidate_id = item.get("candidate_id")
        if isinstance(item_id, str) and item_id in seen_item_ids:
            continue
        if isinstance(candidate_id, str) and candidate_id in seen_candidate_ids:
            continue
        merged.append(item)
        if isinstance(item_id, str):
            seen_item_ids.add(item_id)
        if isinstance(candidate_id, str):
            seen_candidate_ids.add(candidate_id)
    return merged


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate 2-hop RAG evaluation Q&A from a standard KG space using the LLM settings in config.yaml."
    )
    parser.add_argument("--artifact-dir", default=DEFAULT_ARTIFACT_DIR, help="MinerU artifact directory for provenance.")
    parser.add_argument("--kg-space", default=DEFAULT_KG_SPACE_DIR, help="KG space directory containing clauses/requirements/graph files.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output JSON path.")
    parser.add_argument("--jsonl-output", help="Optional JSONL output path. Defaults to output path with .jsonl suffix.")
    parser.add_argument("--question-count", type=int, default=20, help="Number of final Q&A items to generate.")
    parser.add_argument(
        "--candidate-count",
        type=int,
        help="Number of local 2-hop candidates to sample before LLM generation. Defaults to max(question-count * 2, 80).",
    )
    parser.add_argument("--batch-size", type=int, default=5, help="How many candidates to send per LLM request.")
    parser.add_argument("--seed", type=int, default=2582017, help="Random seed for deterministic candidate sampling.")
    parser.add_argument("--max-source-chars", type=int, default=900, help="Maximum source text characters per cited clause.")
    parser.add_argument("--allow-same-clause", action="store_true", help="Allow two requirements from the same clause in a 2-hop candidate.")
    parser.add_argument("--append", action="store_true", help="Append new Q&A to the existing output JSON/JSONL instead of overwriting.")
    parser.add_argument("--dry-run-candidates", action="store_true", help="Only write sampled candidate evidence packs; do not call the LLM.")
    parser.add_argument("--llm-timeout-seconds", type=int, help="Override config.yaml LLM timeout for this run.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    artifact_dir = resolve_path(args.artifact_dir)
    kg_space_dir = resolve_path(args.kg_space)
    output_path = resolve_path(args.output)
    jsonl_output_path = resolve_path(args.jsonl_output) if args.jsonl_output else output_path.with_suffix(".jsonl")

    manifest_path = kg_space_dir / "space_manifest.json"
    manifest = read_json(manifest_path) if manifest_path.exists() else {}
    standard_id = manifest.get("standard_id") or kg_space_dir.name
    existing_report = load_existing_report(output_path) if args.append else {"metadata": {}, "items": []}
    existing_items = existing_report.get("items", [])
    existing_candidate_ids = {
        item.get("candidate_id")
        for item in existing_items
        if isinstance(item, dict) and isinstance(item.get("candidate_id"), str)
    }

    clauses = read_json(kg_space_dir / "clauses.json")
    requirement_rows = read_json(kg_space_dir / "requirements.json")
    nodes = read_json(kg_space_dir / "graph_nodes.json")
    edges = read_json(kg_space_dir / "graph_edges.json")
    requirements = {item["requirement_uid"]: item for item in requirement_rows}
    target_count = max(1, args.question_count)
    candidate_count = args.candidate_count or max(target_count * 2, 80)

    candidate_pool = collect_2hop_candidates(
        nodes,
        edges,
        requirements,
        candidate_count=max(candidate_count + len(existing_candidate_ids), candidate_count),
        seed=args.seed,
        allow_same_clause=args.allow_same_clause,
    )
    candidates = filter_candidates(
        candidate_pool,
        exclude_candidate_ids=existing_candidate_ids,
        candidate_count=candidate_count,
    )
    if args.append and not candidates:
        print("No new 2-hop candidates available after excluding existing candidate_ids.", file=sys.stderr)
        return 3
    prompt_candidates = [
        candidate_to_prompt_item(
            candidate,
            clauses=clauses,
            requirements=requirements,
            max_source_chars=args.max_source_chars,
        )
        for candidate in candidates
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    jsonl_output_path.parent.mkdir(parents=True, exist_ok=True)

    metadata = {
        "standard_id": standard_id,
        "artifact_dir": str(artifact_dir),
        "kg_space_dir": str(kg_space_dir),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "question_count_requested": args.question_count,
        "append": args.append,
        "existing_item_count": len(existing_items),
        "candidate_count": len(prompt_candidates),
        "candidate_strategy": "requirement -ABOUT-> concept <-ABOUT- requirement",
    }

    if args.dry_run_candidates:
        report = {
            "metadata": metadata,
            "items": [],
            "candidates": prompt_candidates[: args.question_count],
        }
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Dry-run candidate report written to: {output_path}")
        print(f"Sampled candidates: {len(prompt_candidates)}")
        return 0

    config = get_config().model_copy(deep=True)
    if args.llm_timeout_seconds is not None:
        config.llm.timeout_seconds = args.llm_timeout_seconds
    client = ResponsesAPIClient(config)
    if not client.enabled:
        print(f"LLM client is not configured. Set {config.llm.api_key_env} in .env first.", file=sys.stderr)
        return 2

    qa_items: list[dict[str, Any]] = []
    candidates_by_id = {item["candidate_id"]: item for item in prompt_candidates}
    batch_size = max(1, args.batch_size)
    for batch_index, batch in enumerate(chunks(prompt_candidates, batch_size), start=1):
        if len(qa_items) >= target_count:
            break
        print(f"Generating batch {batch_index}, candidates={len(batch)}", flush=True)
        result = client.create_structured_output(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=build_user_prompt(standard_id, batch),
            schema_name="two_hop_rag_qa_batch",
            schema=QA_SCHEMA,
        )
        raw_items = extract_llm_items(result)
        qa_items.extend(normalize_llm_items(raw_items, candidates_by_id))

    qa_items = qa_items[:target_count]
    final_items = merge_items(existing_items, qa_items) if args.append else qa_items
    previous_metadata = existing_report.get("metadata") if isinstance(existing_report.get("metadata"), dict) else {}
    report = {
        "metadata": {
            **previous_metadata,
            **metadata,
            "model": config.llm.model,
            "base_url": config.llm.base_url,
            "question_count_generated": len(qa_items),
            "total_item_count": len(final_items),
            "last_append_at": metadata["generated_at"] if args.append else None,
        },
        "items": final_items,
    }
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_jsonl(jsonl_output_path, final_items)

    print(f"Generated Q&A items: {len(qa_items)}")
    if args.append:
        print(f"Existing Q&A items: {len(existing_items)}")
        print(f"Total Q&A items: {len(final_items)}")
    print(f"JSON written to: {output_path}")
    print(f"JSONL written to: {jsonl_output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
