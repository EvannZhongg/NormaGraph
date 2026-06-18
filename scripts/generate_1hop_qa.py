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
DEFAULT_OUTPUT = "data/eval/sl258-2017-1hop-qa.json"


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
                    "question": {"type": "string", "minLength": 1},
                    "answer": {"type": "string", "minLength": 1},
                    "reasoning": {"type": "string", "minLength": 1},
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
                    "quality_notes": {"type": "string", "minLength": 1},
                },
            },
        }
    },
}


SYSTEM_PROMPT = """你是水利水电规范 RAG 评测集构建助手。你需要基于给定的单条 requirement 证据包生成中文问答。

要求：
1. 每个 candidate 生成 1 个问题和答案，candidate_id 必须原样返回。
2. 问题必须可以仅依据这一条 requirement 回答，不要依赖其他条文或外部知识。
3. 问题可以聚焦于适用条件、强制/禁止/可选要求、检查依据、技术动作或证据要求。
4. 答案必须严格依据输入 requirement，不得补写条文外信息。
5. reasoning 简要说明为什么这道题只需要这一条 requirement 就能回答。
6. supporting_clause_refs 只填写实际用到的 clause_ref；source_statement_refs 填实际用到的 requirement_uid。
7. 如果该 candidate 证据太弱，不适合形成高质量 1-hop 问答，也要返回，但 question/answer 要保守，quality_notes 说明原因。
8. 只输出符合 JSON Schema 的 JSON。"""


@dataclass(frozen=True)
class RequirementCandidate:
    candidate_id: str
    requirement_uid: str


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def stable_id(*parts: str, prefix: str = "qa-1hop-candidate") -> str:
    digest = hashlib.sha1("||".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}:{digest}"


def compact_text(value: str | None, max_chars: int) -> str:
    text = " ".join((value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"


def normalize_difficulty(value: Any) -> str:
    if value in {"easy", "medium", "hard"}:
        return str(value)
    return "medium"


def build_clause_index(clauses: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["clause_uid"]: item for item in clauses if isinstance(item.get("clause_uid"), str)}


def collect_1hop_candidates(
    requirements: list[dict[str, Any]],
    *,
    candidate_count: int,
    seed: int,
) -> list[RequirementCandidate]:
    pool = [
        RequirementCandidate(
            candidate_id=stable_id(item["requirement_uid"]),
            requirement_uid=item["requirement_uid"],
        )
        for item in requirements
        if isinstance(item.get("requirement_uid"), str)
        and isinstance(item.get("requirement_text"), str)
        and item.get("requirement_text").strip()
    ]
    rng = random.Random(seed)
    rng.shuffle(pool)
    return pool[: max(1, candidate_count)]


def build_requirement_source(
    requirement: dict[str, Any],
    clause: dict[str, Any] | None,
    *,
    max_source_chars: int,
) -> dict[str, Any]:
    clause = clause or {}
    return {
        "requirement_uid": requirement.get("requirement_uid"),
        "clause_uid": requirement.get("parent_clause_uid"),
        "clause_ref": requirement.get("clause_ref") or clause.get("clause_ref"),
        "requirement_text": compact_text(requirement.get("requirement_text"), max_source_chars),
        "modality": requirement.get("modality"),
        "subject": requirement.get("subject"),
        "action": requirement.get("action") or [],
        "object": requirement.get("object") or [],
        "applicability_rule": requirement.get("applicability_rule"),
        "judgement_criteria": requirement.get("judgement_criteria") or [],
        "evidence_expected": requirement.get("evidence_expected") or [],
        "domain_tags": requirement.get("domain_tags") or [],
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


def build_fallback_qa(
    candidate_id: str,
    source: dict[str, Any],
) -> dict[str, Any]:
    clause_ref = str(source.get("clause_ref") or source.get("clause_uid") or "未知条款")
    subject = str(source.get("subject") or "相关对象").strip()
    action = source.get("action") or []
    object_text = str((source.get("object") or ["相关事项"])[0]).strip()
    applicability_rule = str(source.get("applicability_rule") or "").strip()
    requirement_text = str(source.get("requirement_text") or "").strip()
    modality = str(source.get("modality") or "").strip()

    if modality == "forbidden":
        question = f"根据条款 {clause_ref}，禁止什么行为？"
        answer = requirement_text or f"条款 {clause_ref} 禁止相关行为。"
    elif modality == "may":
        question = f"根据条款 {clause_ref}，在什么条件下可以{action[0] if action else '执行相关措施'}？"
        answer = requirement_text or f"条款 {clause_ref} 规定了相关可选要求。"
    elif applicability_rule:
        question = f"在{applicability_rule}时，根据条款 {clause_ref} 应如何要求 {subject} 处理 {object_text}？"
        answer = requirement_text
    else:
        question = f"根据条款 {clause_ref}，{subject}对{object_text}提出了什么要求？"
        answer = requirement_text or f"条款 {clause_ref} 对相关事项提出了要求。"

    question = question.strip()
    answer = answer.strip()
    if not question:
        question = f"根据条款 {clause_ref}，该条文提出了什么要求？"
    if not answer:
        answer = f"依据条款 {clause_ref}，应按原文要求执行。"

    return {
        "candidate_id": candidate_id,
        "question": question,
        "answer": answer,
        "reasoning": f"该题仅依据条款 {clause_ref} 即可回答，核心信息已在单条 requirement 中完整给出。",
        "supporting_clause_refs": [clause_ref],
        "source_statement_refs": [str(source.get("requirement_uid") or "")],
        "difficulty": "easy",
        "quality_notes": "fallback generated from source requirement",
    }


def candidate_to_prompt_item(
    candidate: RequirementCandidate,
    *,
    requirements_by_uid: dict[str, dict[str, Any]],
    clause_by_uid: dict[str, dict[str, Any]],
    max_source_chars: int,
) -> dict[str, Any]:
    requirement = requirements_by_uid[candidate.requirement_uid]
    clause = clause_by_uid.get(requirement.get("parent_clause_uid"))
    source = build_requirement_source(requirement, clause, max_source_chars=max_source_chars)
    return {
        "candidate_id": candidate.candidate_id,
        "task": "基于单条 requirement 生成一个可以直接由该 requirement 回答的 1-hop 问答。",
        "source_requirement": source,
        "graph_1hop_path": [
            {
                "node_uid": candidate.requirement_uid,
                "node_type": "requirement",
            }
        ],
    }


def build_user_prompt(standard_id: str, candidates: list[dict[str, Any]]) -> str:
    payload = {
        "standard_id": standard_id,
        "question_style": "面向 RAG 检索评估的规范问答，问题必须能由单条 requirement 直接回答。",
        "candidates": candidates,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


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


def normalize_llm_items(raw_items: list[dict[str, Any]], candidates_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in raw_items:
        candidate_id = item.get("candidate_id")
        candidate = candidates_by_id.get(candidate_id)
        if not candidate:
            continue
        source = candidate["source_requirement"]
        question = str(item.get("question", "")).strip()
        answer = str(item.get("answer", "")).strip()
        reasoning = str(item.get("reasoning", "")).strip()
        if not question or not answer or not reasoning:
            fallback = build_fallback_qa(candidate_id, source)
            normalized.append(
                {
                    "id": stable_id(candidate_id, fallback["question"], prefix="qa"),
                    "candidate_id": candidate_id,
                    **fallback,
                    "source_requirement": source,
                    "citations": [
                        {
                            "clause_uid": source.get("clause_uid"),
                            "clause_ref": source.get("clause_ref"),
                            "requirement_uid": source.get("requirement_uid"),
                            "page_span": source.get("source_page_span") or [],
                            "heading_path": source.get("heading_path") or [],
                            "source_text": source.get("source_text"),
                        }
                    ],
                    "graph_1hop_path": candidate.get("graph_1hop_path"),
                }
            )
            continue
        normalized.append(
            {
                "id": stable_id(candidate_id, question, prefix="qa"),
                "candidate_id": candidate_id,
                "question": question,
                "answer": answer,
                "reasoning": reasoning,
                "difficulty": normalize_difficulty(item.get("difficulty")),
                "supporting_clause_refs": item.get("supporting_clause_refs") or [],
                "source_statement_refs": item.get("source_statement_refs") or [],
                "quality_notes": str(item.get("quality_notes", "")).strip(),
                "source_requirement": source,
                "citations": [
                    {
                        "clause_uid": source.get("clause_uid"),
                        "clause_ref": source.get("clause_ref"),
                        "requirement_uid": source.get("requirement_uid"),
                        "page_span": source.get("source_page_span") or [],
                        "heading_path": source.get("heading_path") or [],
                        "source_text": source.get("source_text"),
                    }
                ],
                "graph_1hop_path": candidate.get("graph_1hop_path"),
            }
        )
    return normalized


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate 1-hop RAG evaluation Q&A from a standard KG space using the LLM settings in config.yaml."
    )
    parser.add_argument("--artifact-dir", default=DEFAULT_ARTIFACT_DIR, help="MinerU artifact directory for provenance.")
    parser.add_argument("--kg-space", default=DEFAULT_KG_SPACE_DIR, help="KG space directory containing clauses/requirements/graph files.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output JSON path.")
    parser.add_argument("--jsonl-output", help="Optional JSONL output path. Defaults to output path with .jsonl suffix.")
    parser.add_argument("--question-count", type=int, default=20, help="Number of final Q&A items to generate.")
    parser.add_argument(
        "--candidate-count",
        type=int,
        help="Number of local 1-hop candidates to sample before LLM generation. Defaults to max(question-count * 2, 100).",
    )
    parser.add_argument("--batch-size", type=int, default=8, help="How many candidates to send per LLM request.")
    parser.add_argument("--seed", type=int, default=2582017, help="Random seed for deterministic candidate sampling.")
    parser.add_argument("--max-source-chars", type=int, default=900, help="Maximum source text characters per cited clause.")
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

    clauses = read_json(kg_space_dir / "clauses.json")
    requirement_rows = read_json(kg_space_dir / "requirements.json")
    clause_by_uid = build_clause_index(clauses)
    requirements_by_uid = {
        item["requirement_uid"]: item
        for item in requirement_rows
        if isinstance(item.get("requirement_uid"), str)
    }

    target_count = max(1, args.question_count)
    candidate_count = args.candidate_count or max(target_count * 2, 100)
    candidate_pool = collect_1hop_candidates(
        requirement_rows,
        candidate_count=candidate_count,
        seed=args.seed,
    )
    prompt_candidates = [
        candidate_to_prompt_item(
            candidate,
            requirements_by_uid=requirements_by_uid,
            clause_by_uid=clause_by_uid,
            max_source_chars=args.max_source_chars,
        )
        for candidate in candidate_pool
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    jsonl_output_path.parent.mkdir(parents=True, exist_ok=True)

    metadata = {
        "standard_id": standard_id,
        "artifact_dir": str(artifact_dir),
        "kg_space_dir": str(kg_space_dir),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "question_count_requested": target_count,
        "candidate_count": len(prompt_candidates),
        "candidate_strategy": "single requirement -> single-hop QA",
    }

    if args.dry_run_candidates:
        report = {
            "metadata": metadata,
            "items": [],
            "candidates": prompt_candidates[:target_count],
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
            schema_name="one_hop_rag_qa_batch",
            schema=QA_SCHEMA,
        )
        raw_items = extract_llm_items(result)
        qa_items.extend(normalize_llm_items(raw_items, candidates_by_id))

    qa_items = qa_items[:target_count]
    report = {
        "metadata": {
            **metadata,
            "model": config.llm.model,
            "base_url": config.llm.base_url,
            "question_count_generated": len(qa_items),
        },
        "items": qa_items,
    }
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_jsonl(jsonl_output_path, qa_items)

    print(f"Generated Q&A items: {len(qa_items)}")
    print(f"JSON written to: {output_path}")
    print(f"JSONL written to: {jsonl_output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
