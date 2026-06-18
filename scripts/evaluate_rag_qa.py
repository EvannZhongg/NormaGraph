from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
import json
import math
import os
from pathlib import Path
import sys
import time
from typing import Any

import httpx

try:
    from tqdm.auto import tqdm
except ImportError:  # pragma: no cover
    tqdm = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from core.config import get_config
from models.schemas import QuestionRequest
from services.retrieval_qa_service import RetrievalQAService


DEFAULT_INPUTS = [
    "data/eval/sl258-2017-1hop-qa.json",
    "data/eval/sl258-2017-2hop-qa.json",
]
DEFAULT_OUTPUT = "data/eval/rag-eval-report.json"

JUDGE_CHAT_CONFIG: dict[str, Any] = {
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "api_key_env": "DS_API_KEY",
    "temperature": 0.0,
    "max_tokens": 8000,
    "timeout_seconds": 120,
    "max_retries": 2,
    "retry_backoff_seconds": 2.0,
}


JUDGE_SYSTEM_PROMPT = """你是水利水电规范的问答裁判。你的任务是判断模型回答是否与参考答案一致，并且是否被给定证据支持。

判定原则：
1. 只依据题目、参考答案、模型答案和证据判断，不要使用外部知识。
2. 如果模型答案覆盖了参考答案的核心结论和关键条件，即使额外补充了证据支持的相关内容，也应判为 correct。
3. 只有在额外内容与题意冲突、改变结论、引入无证据事实或造成明显误导时，才因额外内容降级。
4. 如果答案遗漏任一关键子问、关键条件、数值、等级或结论，判为 partial。
5. 如果答案与参考答案明显冲突、答非所问、声称上下文不足但证据中已有答案，或引入证据外内容导致结论错误，判为 incorrect。
6. 证据不足时，不要硬判为 correct。
7. 输出必须严格满足给定 JSON Schema。"""


JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {"type": "string", "enum": ["correct", "partial", "incorrect"]},
        "score": {"type": "integer", "minimum": 0, "maximum": 5},
        "evidence_supported": {"type": "boolean"},
        "coverage": {"type": "string", "enum": ["full", "partial", "none"]},
        "reason": {"type": "string"},
        "missing_points": {"type": "array", "items": {"type": "string"}},
        "hallucinated_points": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "verdict",
        "score",
        "evidence_supported",
        "coverage",
        "reason",
        "missing_points",
        "hallucinated_points",
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate RAG QA retrieval, answer quality, and token usage.")
    parser.add_argument(
        "--input",
        nargs="+",
        default=DEFAULT_INPUTS,
        help="One or more QA dataset paths (.json or .jsonl). Defaults to both sl258 1-hop and 2-hop sets.",
    )
    parser.add_argument("--kg-space", help="KG space directory. Defaults to the dataset standard_id when available.")
    parser.add_argument("--output", help="Output report JSON path.")
    parser.add_argument("--items-output", help="Optional JSONL path for per-item results.")
    parser.add_argument("--limit", type=int, help="Optional cap on the number of QA items to evaluate.")
    parser.add_argument("--top-k", type=int, default=8, help="Retrieval routing top-k.")
    parser.add_argument("--chunk-top-k", type=int, default=10, help="Final retrieval chunk top-k.")
    parser.add_argument("--query-mode", default="hybrid", choices=["hybrid", "graph", "vector"], help="Retrieval mode.")
    parser.add_argument("--history-turns", type=int, default=0, help="History turns sent to the QA service.")
    parser.add_argument("--retrieval-workers", type=int, default=4, help="Concurrent retrieval generation workers.")
    parser.add_argument("--judge-workers", type=int, default=4, help="Concurrent judge workers.")
    parser.add_argument("--no-judge", action="store_true", help="Disable LLM-as-judge evaluation.")
    parser.add_argument("--llm-timeout-seconds", type=int, help="Override retrieval QA LLM timeout.")
    return parser.parse_args()


class ChatJudgeClient:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.api_key = os.getenv(str(config.get("api_key_env") or "LLM_API_KEY"))
        self.reset_stats()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def reset_stats(self) -> None:
        self._request_count = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._total_tokens = 0
        self._last_usage: dict[str, int] | None = None

    def judge(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        result, _ = self.judge_with_usage(system_prompt=system_prompt, user_prompt=user_prompt)
        return result

    def judge_with_usage(self, *, system_prompt: str, user_prompt: str) -> tuple[dict[str, Any], dict[str, int]]:
        if not self.enabled:
            raise RuntimeError(f"Judge client is not configured. Set {self.config['api_key_env']} in .env.")

        url = f"{str(self.config['base_url']).rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.config["model"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.config["temperature"],
            "max_tokens": self.config["max_tokens"],
            "response_format": {"type": "json_object"},
        }

        max_retries = max(0, int(self.config.get("max_retries", 0) or 0))
        last_exc: Exception | None = None
        for attempt in range(1, max_retries + 2):
            try:
                with httpx.Client(timeout=float(self.config["timeout_seconds"])) as client:
                    response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
                usage = self._usage_from_payload(data.get("usage"))
                content = self._extract_message_content(data)
                return self._parse_json(content), usage
            except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                last_exc = exc
                if attempt >= max_retries + 1 or not self._is_retryable(exc):
                    break
                delay = float(self.config.get("retry_backoff_seconds", 0) or 0) * attempt
                if delay > 0:
                    time.sleep(delay)
        raise RuntimeError(f"Judge chat request failed: {last_exc}") from last_exc

    def _usage_from_payload(self, raw_usage: Any) -> dict[str, int]:
        if not isinstance(raw_usage, dict):
            return {"request_count": 1, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        input_tokens = self._usage_int(raw_usage, "prompt_tokens", "input_tokens")
        output_tokens = self._usage_int(raw_usage, "completion_tokens", "output_tokens")
        total_tokens = self._usage_int(raw_usage, "total_tokens")
        if total_tokens == 0 and (input_tokens or output_tokens):
            total_tokens = input_tokens + output_tokens
        return {
            "request_count": 1,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        }

    @staticmethod
    def _usage_int(raw_usage: dict[str, Any], *keys: str) -> int:
        for key in keys:
            value = raw_usage.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                return int(value)
        return 0

    @staticmethod
    def _extract_message_content(payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("Judge chat response did not contain choices.")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise RuntimeError("Judge chat response did not contain a message.")
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        raise RuntimeError("Judge chat response did not contain text content.")

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        candidates = [text.strip()]
        if text.strip().startswith("```"):
            stripped = text.strip().strip("`").strip()
            if stripped.lower().startswith("json"):
                stripped = stripped[4:].strip()
            candidates.append(stripped)
        start = text.find("{")
        end = text.rfind("}")
        if 0 <= start < end:
            candidates.append(text[start : end + 1])
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        raise RuntimeError("Judge chat response was not valid JSON.")

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in {408, 409, 425, 429, 500, 502, 503, 504}
        return False


def load_eval_items(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        items: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    items.append(json.loads(line))
        return items, {}

    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)], {}
    if isinstance(payload, dict):
        items = payload.get("items") or []
        if not isinstance(items, list):
            raise ValueError(f"Eval file {path} must contain an items array.")
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        return [item for item in items if isinstance(item, dict)], metadata
    raise ValueError(f"Unsupported eval file structure: {path}")


def resolve_path(value: str | Path, *, base: Path = PROJECT_ROOT) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def unique_clause_targets(item: dict[str, Any]) -> list[dict[str, Any]]:
    citations = item.get("citations") if isinstance(item.get("citations"), list) else []
    targets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        clause_uid = str(citation.get("clause_uid") or "").strip()
        clause_ref = str(citation.get("clause_ref") or "").strip()
        requirement_uid = str(citation.get("requirement_uid") or "").strip()
        key = clause_uid or clause_ref or requirement_uid
        if not key or key in seen:
            continue
        seen.add(key)
        targets.append(
            {
                "clause_uid": clause_uid or None,
                "clause_ref": clause_ref or None,
                "requirement_uid": requirement_uid or None,
            }
        )

    if targets:
        return targets

    supporting_clause_refs = item.get("supporting_clause_refs") if isinstance(item.get("supporting_clause_refs"), list) else []
    for clause_ref in supporting_clause_refs:
        ref = str(clause_ref or "").strip()
        if not ref or ref in seen:
            continue
        seen.add(ref)
        targets.append({"clause_uid": None, "clause_ref": ref, "requirement_uid": None})
    return targets


def context_matches_target(context: dict[str, Any], target: dict[str, Any]) -> bool:
    ctx_node_uid = str(context.get("node_uid") or "").strip()
    ctx_clause_uid = str(context.get("clause_node_uid") or context.get("clause_uid") or "").strip()
    ctx_clause_ref = str(context.get("clause_ref") or "").strip()

    target_clause_uid = str(target.get("clause_uid") or "").strip()
    target_clause_ref = str(target.get("clause_ref") or "").strip()
    target_requirement_uid = str(target.get("requirement_uid") or "").strip()

    if target_clause_uid and (ctx_node_uid == target_clause_uid or ctx_clause_uid == target_clause_uid):
        return True
    if target_requirement_uid and ctx_node_uid == target_requirement_uid:
        return True
    if target_clause_ref and ctx_clause_ref == target_clause_ref:
        return True
    return False


def compute_retrieval_metrics(contexts: list[dict[str, Any]], gold_targets: list[dict[str, Any]]) -> dict[str, Any]:
    retrieved_count = len(contexts)
    gold_count = len(gold_targets)
    if not retrieved_count or not gold_count:
        return {
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "mrr": 0.0,
            "ndcg": 0.0,
            "retrieved_count": retrieved_count,
            "gold_count": gold_count,
            "relevant_count": 0,
            "matched_gold_count": 0,
            "relevant_ranks": [],
        }

    relevant_ranks: list[int] = []
    matched_gold_indices: set[int] = set()
    relevant_count = 0
    dcg = 0.0
    first_relevant_rank: int | None = None

    for rank, context in enumerate(contexts, start=1):
        matched_target_indices = [
            index for index, target in enumerate(gold_targets) if context_matches_target(context, target)
        ]
        if not matched_target_indices:
            continue
        relevant_count += 1
        relevant_ranks.append(rank)
        if first_relevant_rank is None:
            first_relevant_rank = rank
        new_matches = [index for index in matched_target_indices if index not in matched_gold_indices]
        if new_matches:
            dcg += 1.0 / math.log2(rank + 1)
            matched_gold_indices.update(new_matches)

    matched_gold_count = len(matched_gold_indices)
    precision = relevant_count / retrieved_count if retrieved_count else 0.0
    recall = matched_gold_count / gold_count if gold_count else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if precision and recall else 0.0
    mrr = (1.0 / first_relevant_rank) if first_relevant_rank else 0.0
    ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(gold_count, retrieved_count) + 1))
    ndcg = (dcg / ideal_dcg) if ideal_dcg else 0.0

    return {
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "mrr": round(mrr, 6),
        "ndcg": round(ndcg, 6),
        "retrieved_count": retrieved_count,
        "gold_count": gold_count,
        "relevant_count": relevant_count,
        "matched_gold_count": matched_gold_count,
        "relevant_ranks": relevant_ranks,
    }


def compact_text(value: Any, max_chars: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def build_judge_prompt(
    *,
    question: str,
    reference_answer: str,
    predicted_answer: str,
    gold_targets: list[dict[str, Any]],
    retrieved_contexts: list[dict[str, Any]],
    item: dict[str, Any],
) -> str:
    payload = {
        "question": question,
        "reference_answer": reference_answer,
        "predicted_answer": predicted_answer,
        "gold_targets": [
            {
                "clause_uid": target.get("clause_uid"),
                "clause_ref": target.get("clause_ref"),
                "requirement_uid": target.get("requirement_uid"),
            }
            for target in gold_targets
        ],
        "gold_evidence": item.get("citations") or [],
        "retrieved_contexts": [
            {
                "node_type": context.get("node_type"),
                "clause_ref": context.get("clause_ref"),
                "chapter_path": context.get("chapter_path") or [],
                "clause_text": compact_text(context.get("clause_text"), 320),
                "requirement_text": compact_text(context.get("requirement_text"), 240),
                "judgement_criteria": context.get("judgement_criteria") or [],
                "evidence_expected": context.get("evidence_expected") or [],
            }
            for context in retrieved_contexts
        ],
        "instructions": [
            "判断模型答案是否与参考答案语义一致，且是否被给定证据支持。",
            "如果答案覆盖参考答案核心内容，且额外内容均来自证据并不改变结论，verdict=correct, score=5。",
            "不要仅因为答案比参考答案更详细、列出了更多有证据支持的相关条文，就判为 partial。",
            "如果答案遗漏关键子问、关键条件、数值、等级或结论，verdict=partial, score=3或4。",
            "如果答案明显错误、答非所问、错误声称上下文不足，或额外内容导致结论错误，verdict=incorrect, score=0到2。",
        ],
        "output_schema": JUDGE_SCHEMA,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def normalize_judge_result(raw_result: dict[str, Any]) -> dict[str, Any]:
    verdict = str(raw_result.get("verdict") or "incorrect").strip().lower()
    if verdict not in {"correct", "partial", "incorrect"}:
        verdict = "incorrect"

    try:
        score = int(raw_result.get("score", 0) or 0)
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(5, score))

    coverage = str(raw_result.get("coverage") or "none").strip().lower()
    if coverage not in {"full", "partial", "none"}:
        coverage = "none"

    return {
        "verdict": verdict,
        "score": score,
        "evidence_supported": bool(raw_result.get("evidence_supported")),
        "coverage": coverage,
        "reason": str(raw_result.get("reason") or ""),
        "missing_points": [
            str(item)
            for item in (raw_result.get("missing_points") if isinstance(raw_result.get("missing_points"), list) else [])
        ],
        "hallucinated_points": [
            str(item)
            for item in (
                raw_result.get("hallucinated_points")
                if isinstance(raw_result.get("hallucinated_points"), list)
                else []
            )
        ],
    }


def safe_read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def aggregate_numeric(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 6)


def empty_token_totals() -> dict[str, int]:
    return {
        "retrieval_llm_input": 0,
        "retrieval_llm_output": 0,
        "retrieval_llm_total": 0,
        "retrieval_embedding_input": 0,
        "retrieval_embedding_total": 0,
        "judge_llm_input": 0,
        "judge_llm_output": 0,
        "judge_llm_total": 0,
    }


def build_summary(
    *,
    input_count: int,
    reports: list[dict[str, Any]],
    retrieval_precisions: list[float],
    retrieval_recalls: list[float],
    retrieval_f1s: list[float],
    retrieval_mrrs: list[float],
    retrieval_ndcgs: list[float],
    judge_scores: list[float],
    judge_correct_flags: list[int],
    judge_supported_flags: list[int],
    token_totals: dict[str, int],
    judge_enabled: bool,
) -> dict[str, Any]:
    evaluated_items = [item for item in reports if item.get("status") == "succeeded"]
    return {
        "input_count": input_count,
        "success_count": len(evaluated_items),
        "failed_count": len(reports) - len(evaluated_items),
        "retrieval": {
            "precision": aggregate_numeric(retrieval_precisions),
            "recall": aggregate_numeric(retrieval_recalls),
            "f1": aggregate_numeric(retrieval_f1s),
            "mrr": aggregate_numeric(retrieval_mrrs),
            "ndcg": aggregate_numeric(retrieval_ndcgs),
        },
        "judge": {
            "enabled": judge_enabled,
            "accuracy": round(sum(judge_correct_flags) / len(judge_correct_flags), 6) if judge_correct_flags else 0.0,
            "support_rate": round(sum(judge_supported_flags) / len(judge_supported_flags), 6) if judge_supported_flags else 0.0,
            "average_score": aggregate_numeric(judge_scores),
        },
        "token_usage": {
            "retrieval": {
                "llm_input_tokens": token_totals["retrieval_llm_input"],
                "llm_output_tokens": token_totals["retrieval_llm_output"],
                "llm_total_tokens": token_totals["retrieval_llm_total"],
                "embedding_input_tokens": token_totals["retrieval_embedding_input"],
                "embedding_total_tokens": token_totals["retrieval_embedding_total"],
            },
            "judge": {
                "llm_input_tokens": token_totals["judge_llm_input"],
                "llm_output_tokens": token_totals["judge_llm_output"],
                "llm_total_tokens": token_totals["judge_llm_total"],
            },
            "total": {
                "llm_input_tokens": token_totals["retrieval_llm_input"] + token_totals["judge_llm_input"],
                "llm_output_tokens": token_totals["retrieval_llm_output"] + token_totals["judge_llm_output"],
                "llm_total_tokens": token_totals["retrieval_llm_total"] + token_totals["judge_llm_total"],
                "embedding_input_tokens": token_totals["retrieval_embedding_input"],
                "embedding_total_tokens": token_totals["retrieval_embedding_total"],
            },
        },
    }


def merge_token_totals(target: dict[str, int], source: dict[str, int]) -> None:
    for key, value in source.items():
        target[key] = target.get(key, 0) + int(value or 0)


def run_with_progress(
    *,
    items: list[Any],
    worker,
    max_workers: int,
    desc: str,
) -> list[Any]:
    if not items:
        return []
    results: list[Any] = [None] * len(items)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {executor.submit(worker, item): index for index, item in enumerate(items)}
        iterator = as_completed(future_to_index)
        if tqdm is not None:
            iterator = tqdm(iterator, total=len(items), desc=desc, dynamic_ncols=True)
        for future in iterator:
            index = future_to_index[future]
            results[index] = future.result()
    return results


def nested_retrieval_usage_totals(raw_usage: dict[str, Any]) -> dict[str, int]:
    llm_usage = raw_usage.get("llm") if isinstance(raw_usage.get("llm"), dict) else {}
    embedding_usage = raw_usage.get("embedding") if isinstance(raw_usage.get("embedding"), dict) else {}
    return {
        "retrieval_llm_input": int(llm_usage.get("input_tokens", 0) or 0),
        "retrieval_llm_output": int(llm_usage.get("output_tokens", 0) or 0),
        "retrieval_llm_total": int(llm_usage.get("total_tokens", 0) or 0),
        "retrieval_embedding_input": int(embedding_usage.get("input_tokens", 0) or 0),
        "retrieval_embedding_total": int(embedding_usage.get("total_tokens", 0) or 0),
    }


def evaluate_dataset(
    *,
    input_path: Path,
    args: argparse.Namespace,
    config: Any,
    judge_client: ChatJudgeClient,
    judge_enabled: bool,
    started_at: datetime,
) -> dict[str, Any]:
    items, input_metadata = load_eval_items(input_path)
    if args.limit is not None:
        items = items[: max(0, args.limit)]

    kg_space_dir = resolve_path(
        args.kg_space or input_metadata.get("kg_space_dir") or input_metadata.get("kgSpaceDir") or "data/kg_spaces/sl258-2017"
    )
    manifest_path = kg_space_dir / "space_manifest.json"
    manifest = safe_read_json(manifest_path) if manifest_path.exists() else {}
    standard_id = str(
        input_metadata.get("standard_id")
        or input_metadata.get("standardId")
        or manifest.get("standard_id")
        or kg_space_dir.name.replace("-", ":")
    )

    prepared_items: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        question = str(item.get("question") or "").strip()
        reference_answer = str(item.get("answer") or "").strip()
        gold_targets = unique_clause_targets(item)
        prepared_items.append(
            {
                "index": index,
                "item": item,
                "question": question,
                "reference_answer": reference_answer,
                "gold_targets": gold_targets,
            }
        )

    def run_retrieval(entry: dict[str, Any]) -> dict[str, Any]:
        index = entry["index"]
        item = entry["item"]
        question = entry["question"]
        reference_answer = entry["reference_answer"]
        gold_targets = entry["gold_targets"]

        if not question:
            return {
                "index": index,
                "status": "skipped",
                "reason": "empty_question",
                "item": item,
                "question": question,
                "reference_answer": reference_answer,
                "gold_targets": gold_targets,
            }

        request = QuestionRequest(
            question=question,
            standardIds=[standard_id],
            queryMode=args.query_mode,
            topK=args.top_k,
            chunkTopK=args.chunk_top_k,
            historyTurns=args.history_turns,
            rerank=True,
            userPrompt=None,
            expandCitations=True,
        )

        worker_service = RetrievalQAService(config)
        response = worker_service.answer(request)
        run_id = None
        if response.graphHops and isinstance(response.graphHops[0], dict):
            run_id = str(response.graphHops[0].get("runId") or "")
        if not run_id:
            raise RuntimeError("Retrieval service did not return a run id.")
        run_log_path = config.data_dir / "rag_runs" / run_id / "run.json"
        run_log = safe_read_json(run_log_path)
        retrieved_contexts = run_log.get("retrieval", {}).get("contexts") or []
        if not isinstance(retrieved_contexts, list):
            retrieved_contexts = []
        retrieved_citations = run_log.get("citations") or response.citations or []
        if not isinstance(retrieved_citations, list):
            retrieved_citations = []
        deduped_citations: list[dict[str, Any]] = []
        seen_citations: set[tuple[str, str]] = set()
        for citation in retrieved_citations:
            if not isinstance(citation, dict):
                continue
            key = (str(citation.get("node_uid") or ""), str(citation.get("clause_ref") or ""))
            if key in seen_citations:
                continue
            seen_citations.add(key)
            deduped_citations.append(citation)
        retrieval_metrics = compute_retrieval_metrics(deduped_citations, gold_targets)
        return {
            "index": index,
            "status": "succeeded",
            "question": question,
            "reference_answer": reference_answer,
            "predicted_answer": response.answer,
            "gold_targets": gold_targets,
            "retrieval_metrics": retrieval_metrics,
            "retrieved_contexts": retrieved_contexts,
            "retrieved_citations": deduped_citations,
            "run_id": run_id,
            "citations": response.citations,
            "token_usage": {
                "retrieval": run_log.get("usage", {}),
                "judge": {"request_count": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            },
            "item": item,
        }

    retrieval_progress_desc = f"retrieval {input_path.stem}"
    per_item_reports = run_with_progress(
        items=prepared_items,
        worker=run_retrieval,
        max_workers=max(1, int(args.retrieval_workers)),
        desc=retrieval_progress_desc,
    )

    retrieval_precisions: list[float] = []
    retrieval_recalls: list[float] = []
    retrieval_f1s: list[float] = []
    retrieval_mrrs: list[float] = []
    retrieval_ndcgs: list[float] = []
    judge_scores: list[float] = []
    judge_correct_flags: list[int] = []
    judge_supported_flags: list[int] = []
    token_totals = empty_token_totals()

    for report in per_item_reports:
        if report.get("status") != "succeeded":
            continue
        metrics = report.get("retrieval_metrics") if isinstance(report.get("retrieval_metrics"), dict) else {}
        retrieval_precisions.append(float(metrics.get("precision", 0.0) or 0.0))
        retrieval_recalls.append(float(metrics.get("recall", 0.0) or 0.0))
        retrieval_f1s.append(float(metrics.get("f1", 0.0) or 0.0))
        retrieval_mrrs.append(float(metrics.get("mrr", 0.0) or 0.0))
        retrieval_ndcgs.append(float(metrics.get("ndcg", 0.0) or 0.0))
        retrieval_usage = report.get("token_usage", {}).get("retrieval", {})
        merge_token_totals(token_totals, nested_retrieval_usage_totals(retrieval_usage))

    judge_queue = [report for report in per_item_reports if report.get("status") == "succeeded"]
    if judge_enabled and judge_queue:
        def run_judge(report: dict[str, Any]) -> dict[str, Any]:
            judge_result, judge_usage_delta = judge_client.judge_with_usage(
                system_prompt=JUDGE_SYSTEM_PROMPT,
                user_prompt=build_judge_prompt(
                    question=str(report.get("question") or ""),
                    reference_answer=str(report.get("reference_answer") or ""),
                    predicted_answer=str(report.get("predicted_answer") or "").strip(),
                    gold_targets=report.get("gold_targets") if isinstance(report.get("gold_targets"), list) else [],
                    retrieved_contexts=report.get("retrieved_contexts") if isinstance(report.get("retrieved_contexts"), list) else [],
                    item=report.get("item") if isinstance(report.get("item"), dict) else {},
                ),
            )
            return {
                "index": report["index"],
                "judge": normalize_judge_result(judge_result),
                "judge_usage_delta": judge_usage_delta,
            }

        judged_reports = run_with_progress(
            items=judge_queue,
            worker=run_judge,
            max_workers=max(1, int(args.judge_workers)),
            desc=f"judge {input_path.stem}",
        )
        judged_by_index = {item["index"]: item for item in judged_reports}
        for report in per_item_reports:
            if report.get("status") != "succeeded":
                continue
            judged = judged_by_index.get(report["index"])
            if not judged:
                continue
            judge_result = judged.get("judge")
            judge_usage_delta = judged.get("judge_usage_delta") or {"request_count": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            report["judge"] = judge_result
            report["token_usage"]["judge"] = judge_usage_delta
            token_totals["judge_llm_input"] += int(judge_usage_delta.get("input_tokens", 0) or 0)
            token_totals["judge_llm_output"] += int(judge_usage_delta.get("output_tokens", 0) or 0)
            token_totals["judge_llm_total"] += int(judge_usage_delta.get("total_tokens", 0) or 0)
            judge_scores.append(float(judge_result.get("score", 0) or 0))
            judge_correct_flags.append(1 if judge_result.get("verdict") == "correct" else 0)
            judge_supported_flags.append(1 if judge_result.get("evidence_supported") else 0)

    summary = build_summary(
        input_count=len(items),
        reports=per_item_reports,
        retrieval_precisions=retrieval_precisions,
        retrieval_recalls=retrieval_recalls,
        retrieval_f1s=retrieval_f1s,
        retrieval_mrrs=retrieval_mrrs,
        retrieval_ndcgs=retrieval_ndcgs,
        judge_scores=judge_scores,
        judge_correct_flags=judge_correct_flags,
        judge_supported_flags=judge_supported_flags,
        token_totals=token_totals,
        judge_enabled=judge_enabled,
    )

    return {
        "input_path": str(input_path),
        "kg_space_dir": str(kg_space_dir),
        "standard_id": standard_id,
        "summary": summary,
        "items": per_item_reports,
    }


def main() -> int:
    args = parse_args()
    input_paths = [resolve_path(path) for path in args.input]
    output_path = resolve_path(args.output or DEFAULT_OUTPUT)
    items_output_path = resolve_path(args.items_output or output_path.with_suffix(".jsonl"))

    config = get_config().model_copy(deep=True)
    if args.llm_timeout_seconds is not None:
        config.llm.timeout_seconds = args.llm_timeout_seconds

    judge_client = ChatJudgeClient(JUDGE_CHAT_CONFIG)
    judge_enabled = bool(not args.no_judge and judge_client.enabled)
    if not judge_enabled and not args.no_judge:
        print(f"Judge LLM is not configured. Set {JUDGE_CHAT_CONFIG['api_key_env']} in .env to enable it.", file=sys.stderr)

    started_at = datetime.now(UTC)
    datasets: list[dict[str, Any]] = []
    for input_path in input_paths:
        dataset_report = evaluate_dataset(
            input_path=input_path,
            args=args,
            config=config,
            judge_client=judge_client,
            judge_enabled=judge_enabled,
            started_at=started_at,
        )
        datasets.append(dataset_report)

    completed_at = datetime.now(UTC)
    all_reports: list[dict[str, Any]] = []
    all_precisions: list[float] = []
    all_recalls: list[float] = []
    all_f1s: list[float] = []
    all_mrrs: list[float] = []
    all_ndcgs: list[float] = []
    all_judge_scores: list[float] = []
    all_judge_correct_flags: list[int] = []
    all_judge_supported_flags: list[int] = []
    overall_token_totals = empty_token_totals()
    overall_input_count = 0

    for dataset in datasets:
        summary = dataset["summary"]
        overall_input_count += int(summary.get("input_count", 0) or 0)
        items = dataset.get("items") if isinstance(dataset.get("items"), list) else []
        all_reports.extend(items)
        for item in items:
            if item.get("status") != "succeeded":
                continue
            metrics = item.get("retrieval_metrics") if isinstance(item.get("retrieval_metrics"), dict) else {}
            all_precisions.append(float(metrics.get("precision", 0.0) or 0.0))
            all_recalls.append(float(metrics.get("recall", 0.0) or 0.0))
            all_f1s.append(float(metrics.get("f1", 0.0) or 0.0))
            all_mrrs.append(float(metrics.get("mrr", 0.0) or 0.0))
            all_ndcgs.append(float(metrics.get("ndcg", 0.0) or 0.0))
            judge = item.get("judge") if isinstance(item.get("judge"), dict) else None
            if judge:
                all_judge_scores.append(float(judge.get("score", 0.0) or 0.0))
                all_judge_correct_flags.append(1 if judge.get("verdict") == "correct" else 0)
                all_judge_supported_flags.append(1 if judge.get("evidence_supported") else 0)

        tokens = summary.get("token_usage") if isinstance(summary.get("token_usage"), dict) else {}
        retrieval_tokens = tokens.get("retrieval") if isinstance(tokens.get("retrieval"), dict) else {}
        judge_tokens = tokens.get("judge") if isinstance(tokens.get("judge"), dict) else {}
        merge_token_totals(
            overall_token_totals,
            {
                "retrieval_llm_input": retrieval_tokens.get("llm_input_tokens", 0),
                "retrieval_llm_output": retrieval_tokens.get("llm_output_tokens", 0),
                "retrieval_llm_total": retrieval_tokens.get("llm_total_tokens", 0),
                "retrieval_embedding_input": retrieval_tokens.get("embedding_input_tokens", 0),
                "retrieval_embedding_total": retrieval_tokens.get("embedding_total_tokens", 0),
                "judge_llm_input": judge_tokens.get("llm_input_tokens", 0),
                "judge_llm_output": judge_tokens.get("llm_output_tokens", 0),
                "judge_llm_total": judge_tokens.get("llm_total_tokens", 0),
            },
        )

    overall_summary = build_summary(
        input_count=overall_input_count,
        reports=all_reports,
        retrieval_precisions=all_precisions,
        retrieval_recalls=all_recalls,
        retrieval_f1s=all_f1s,
        retrieval_mrrs=all_mrrs,
        retrieval_ndcgs=all_ndcgs,
        judge_scores=all_judge_scores,
        judge_correct_flags=all_judge_correct_flags,
        judge_supported_flags=all_judge_supported_flags,
        token_totals=overall_token_totals,
        judge_enabled=judge_enabled,
    )

    report = {
        "metadata": {
            "input_paths": [str(path) for path in input_paths],
            "model": config.llm.model,
            "base_url": config.llm.base_url,
            "judge": {
                "base_url": JUDGE_CHAT_CONFIG["base_url"],
                "model": JUDGE_CHAT_CONFIG["model"],
                "api_key_env": JUDGE_CHAT_CONFIG["api_key_env"],
                "endpoint": "/chat/completions",
                "enabled": judge_enabled,
            },
            "top_k": args.top_k,
            "chunk_top_k": args.chunk_top_k,
            "query_mode": args.query_mode,
            "history_turns": args.history_turns,
            "judge_enabled": judge_enabled,
            "generated_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
        },
        "summary": overall_summary,
        "datasets": datasets,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    items_output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    jsonl_rows = []
    for dataset in datasets:
        dataset_name = Path(str(dataset.get("input_path") or "")).stem
        for item in dataset.get("items") or []:
            row = dict(item)
            row["dataset"] = dataset_name
            row["input_path"] = dataset.get("input_path")
            jsonl_rows.append(row)
    write_jsonl(items_output_path, jsonl_rows)

    print(json.dumps(overall_summary, ensure_ascii=False, indent=2))
    print(f"\nReport written to: {output_path}")
    print(f"Item log written to: {items_output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
