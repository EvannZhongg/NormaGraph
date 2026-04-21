from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from adapters.llm_client import ResponseAPIError, ResponsesAPIClient
from core.config import get_config
from services.standard_pipeline import StandardPipelineService


ALLOWED_LABELS = ["clause", "section", "reference_standard", "chapter", "appendix", "none"]
REFERENCE_STANDARD_TITLES = {"引用标准", "规范性引用文件", "引用文件"}
TITLE_CLASSIFICATION_BATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title_id": {"type": "string"},
                    "label": {"type": "string", "enum": ALLOWED_LABELS},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "rationale": {"type": "string"},
                },
                "required": ["title_id", "label", "confidence", "rationale"],
            },
        }
    },
    "required": ["items"],
}

TITLE_CLASSIFICATION_SYSTEM_PROMPT = """你是规范标题判别评估器，负责识别 OCR / 版面分析输出中被标记为 title 的文本块，在规范结构中的真实角色。

只允许输出以下标签：
- clause: 实际上是条文正文或条文起始，例如 1.0.1、2.3.4 这种规范条款。
- section: 结构性标题，但不是一级 chapter，例如 2.1、2.1.3 之类的节、小节。
- reference_standard: 专门表示“引用标准 / 规范性引用文件”这一类章节。
- chapter: 一级章节标题，例如 1 总则、7 防洪能力复核。
- appendix: 附录、附件、附表、附图等附属结构标题。
- none: 不应作为结构节点处理，包括封面标题、英文标题、目录条目、页眉页脚、噪声、以及 OCR 把正文误识别为 title 的情况。

判别要求：
1. 只能依据输入中的标题文本和上下文信息判断，不要假设存在额外规则。
2. 目录页条目即使长得像 chapter / section，只要明显是目录项而不是正文真实标题，一律判为 none。
3. 如果文本语义上就是“引用标准 / 规范性引用文件”，优先判为 reference_standard，而不是 chapter。
4. 如果文本本身像完整条文句子，或以 1.0.1 / 2.3.4 这类条款编号开头，优先判为 clause。
4.1 如果文本以 6.4.1～6.4.4、3.2.1-3.2.3 这类条款范围编号开头，本质上仍按 clause 处理。
5. 只有明确是附录/附件时才能判为 appendix。
6. 如果无法可靠判断，优先使用 none，而不是勉强归入结构类。
7. 你必须为每个输入 title_id 返回一条结果，不能遗漏。
8. 输出必须严格满足给定 JSON Schema。
"""


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").replace("\u3000", " ")).strip()


def load_title_candidates(content_list_path: Path) -> list[dict[str, Any]]:
    config = get_config().model_copy(deep=True)
    pipeline = StandardPipelineService(config=config)
    raw_pages = json.loads(content_list_path.read_text(encoding="utf-8"))
    normalized_blocks = pipeline._flatten_content_list(raw_pages)

    raw_title_levels: dict[str, int | None] = {}
    for page_idx, page in enumerate(raw_pages, start=1):
        for block_idx, item in enumerate(page, start=1):
            if item.get("type") != "title":
                continue
            block_id = f"p{page_idx:03d}-b{block_idx:03d}"
            raw_title_levels[block_id] = (item.get("content") or {}).get("level")

    title_indices = [index for index, block in enumerate(normalized_blocks) if block.get("source_type") == "title"]
    candidates: list[dict[str, Any]] = []
    for order, block_index in enumerate(title_indices, start=1):
        block = normalized_blocks[block_index]
        previous_title = normalized_blocks[title_indices[order - 2]] if order > 1 else None
        next_title = normalized_blocks[title_indices[order]] if order < len(title_indices) else None
        prev_block = normalized_blocks[block_index - 1] if block_index > 0 else None
        next_block = normalized_blocks[block_index + 1] if block_index + 1 < len(normalized_blocks) else None
        candidates.append(
            {
                "title_id": block["block_id"],
                "title_index": order,
                "page_idx": block["page_idx"],
                "text": block["text"],
                "text_normalized": block["text_normalized"],
                "raw_title_level": raw_title_levels.get(block["block_id"]),
                "previous_title": previous_title["text_normalized"] if previous_title else None,
                "next_title": next_title["text_normalized"] if next_title else None,
                "previous_block_preview": prev_block["text_normalized"] if prev_block else None,
                "next_block_preview": next_block["text_normalized"] if next_block else None,
            }
        )
    return candidates


def build_baseline_labels(kg_space_dir: Path) -> dict[str, dict[str, Any]]:
    structure_path = kg_space_dir / "normalized_structure.json"
    clauses_path = kg_space_dir / "clauses.json"
    structure = json.loads(structure_path.read_text(encoding="utf-8"))
    clauses = json.loads(clauses_path.read_text(encoding="utf-8"))

    baseline: dict[str, dict[str, Any]] = {}
    for node in structure.get("nodes", []):
        block_id = node.get("source_block_id")
        if not block_id:
            continue
        label = str(node.get("node_type") or "").strip().lower()
        if label == "chapter" and normalize_text(node.get("title")) in REFERENCE_STANDARD_TITLES:
            label = "reference_standard"
        baseline[block_id] = {
            "baseline_label": label,
            "baseline_source": "normalized_structure",
            "baseline_ref": node.get("ref"),
            "baseline_title": node.get("title"),
        }

    for clause in clauses:
        block_ids = clause.get("source_block_ids") or []
        if not block_ids:
            continue
        block_id = block_ids[0]
        baseline.setdefault(
            block_id,
            {
                "baseline_label": "clause",
                "baseline_source": "clauses",
                "baseline_ref": clause.get("clause_ref"),
                "baseline_title": clause.get("source_text_normalized"),
            },
        )

    return baseline


def build_user_prompt(batch: list[dict[str, Any]], previous_tail: list[dict[str, Any]]) -> str:
    payload = {
        "task": "判断这些被 OCR 标记为 title 的文本块，在规范正文中的真实类别。",
        "notes": [
            "这是一份规范文档，不是报告。",
            "不要模仿已有规则抽取器；只基于输入上下文做判断。",
            "如果某个标题明显是目录条目、封面标题、双语标题、噪声或 OCR 误判，请输出 none。",
            "如果某个标题语义上是‘引用标准/规范性引用文件’，输出 reference_standard。",
        ],
        "previous_batch_tail": [
            {
                "title_id": item["title_id"],
                "page_idx": item["page_idx"],
                "text": item["text"],
            }
            for item in previous_tail
        ],
        "current_titles": batch,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def classify_batch(
    client: ResponsesAPIClient,
    batch: list[dict[str, Any]],
    previous_tail: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    payload = client.create_structured_output(
        system_prompt=TITLE_CLASSIFICATION_SYSTEM_PROMPT,
        user_prompt=build_user_prompt(batch=batch, previous_tail=previous_tail),
        schema_name="standard_title_classification_batch",
        schema=TITLE_CLASSIFICATION_BATCH_SCHEMA,
    )
    items: Any = None
    if isinstance(payload, dict):
        for key in ("items", "results", "titles", "classifications"):
            value = payload.get(key)
            if isinstance(value, list):
                items = value
                break
        if items is None and isinstance(payload.get("data"), dict):
            for key in ("items", "results", "titles", "classifications"):
                value = payload["data"].get(key)
                if isinstance(value, list):
                    items = value
                    break
        if items is None and payload.get("title_id"):
            items = [payload]
        if items is None and payload and all(isinstance(key, str) for key in payload):
            items = [
                {
                    "title_id": key,
                    "label": value if isinstance(value, str) else str((value or {}).get("label") or ""),
                    "confidence": 0.0 if isinstance(value, str) else float((value or {}).get("confidence") or 0.0),
                    "rationale": "" if isinstance(value, str) else str((value or {}).get("rationale") or ""),
                }
                for key, value in payload.items()
            ]
    elif isinstance(payload, list):
        items = payload

    if not isinstance(items, list):
        raise ResponseAPIError(f"Title classification payload did not contain an items list: {payload!r}")

    normalized_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or not item.get("title_id"):
            continue
        normalized_items.append(
            {
                "title_id": str(item.get("title_id")),
                "label": str(item.get("label") or item.get("category") or "none"),
                "confidence": float(item.get("confidence") or 0.0),
                "rationale": str(item.get("rationale") or item.get("reason") or ""),
            }
        )

    by_id = {item["title_id"]: item for item in normalized_items}
    missing = [item["title_id"] for item in batch if item["title_id"] not in by_id]
    if missing:
        raise ResponseAPIError(f"Missing title classifications for: {', '.join(missing[:8])}")

    return [by_id[item["title_id"]] for item in batch]


def evaluate_predictions(
    titles: list[dict[str, Any]],
    baseline_by_block_id: dict[str, dict[str, Any]],
    predictions_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    confusion = Counter()
    baseline_counts = Counter()
    prediction_counts = Counter()
    mismatches: list[dict[str, Any]] = []
    exact_match_count = 0

    for title in titles:
        title_id = title["title_id"]
        baseline = baseline_by_block_id.get(title_id, {})
        baseline_label = baseline.get("baseline_label", "none")
        prediction = predictions_by_id[title_id]
        predicted_label = prediction["label"]
        baseline_counts[baseline_label] += 1
        prediction_counts[predicted_label] += 1
        confusion[(baseline_label, predicted_label)] += 1
        if baseline_label == predicted_label:
            exact_match_count += 1
            continue
        mismatches.append(
            {
                "title_id": title_id,
                "page_idx": title["page_idx"],
                "text": title["text"],
                "baseline_label": baseline_label,
                "predicted_label": predicted_label,
                "confidence": prediction["confidence"],
                "rationale": prediction["rationale"],
                "baseline_source": baseline.get("baseline_source", "default_none"),
                "baseline_ref": baseline.get("baseline_ref"),
                "looks_like_toc_entry": bool(re.search(r"\s\d+\s*$", title["text_normalized"])),
            }
        )

    return {
        "title_count": len(titles),
        "exact_match_count": exact_match_count,
        "exact_match_rate": round(exact_match_count / len(titles), 4) if titles else 0.0,
        "baseline_counts": dict(sorted(baseline_counts.items())),
        "prediction_counts": dict(sorted(prediction_counts.items())),
        "confusion_matrix": [
            {"baseline_label": baseline_label, "predicted_label": predicted_label, "count": count}
            for (baseline_label, predicted_label), count in sorted(confusion.items())
        ],
        "mismatches": mismatches,
        "semantic_upgrades": {
            "chapter_to_reference_standard": sum(
                1
                for item in mismatches
                if item["baseline_label"] == "chapter" and item["predicted_label"] == "reference_standard"
            ),
            "chapter_or_section_to_none_with_page_suffix": sum(
                1
                for item in mismatches
                if item["baseline_label"] in {"chapter", "section"}
                and item["predicted_label"] == "none"
                and item["looks_like_toc_entry"]
            ),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LLM title classifier evaluation for standard documents.")
    parser.add_argument(
        "--content-list",
        default="data/artifacts/1_sl-258-2017-a2514234-0faab894/content_list_v2.json",
        help="Path to content_list_v2.json",
    )
    parser.add_argument(
        "--kg-space",
        default="data/kg_spaces/sl258-2017",
        help="Path to the existing kg space used as comparison baseline.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=12,
        help="How many title candidates to send per LLM request.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Optional cap on the number of titles to evaluate.",
    )
    parser.add_argument(
        "--output",
        default="data/test-temp/sl258-title-llm-eval.json",
        help="Path to write the evaluation report JSON.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = get_config().model_copy(deep=True)
    client = ResponsesAPIClient(config)
    if not client.enabled:
        print(f"LLM client is not configured. Set {config.llm.api_key_env} in .env first.", file=sys.stderr)
        return 2

    content_list_path = (PROJECT_ROOT / args.content_list).resolve()
    kg_space_dir = (PROJECT_ROOT / args.kg_space).resolve()
    output_path = (PROJECT_ROOT / args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    titles = load_title_candidates(content_list_path)
    if args.limit is not None:
        titles = titles[: max(0, args.limit)]
    baseline_by_block_id = build_baseline_labels(kg_space_dir)

    predictions_by_id: dict[str, dict[str, Any]] = {}
    previous_tail: list[dict[str, Any]] = []
    batch_size = max(1, args.batch_size)
    for start in range(0, len(titles), batch_size):
        batch = titles[start : start + batch_size]
        print(
            f"Evaluating batch {start // batch_size + 1}/{(len(titles) + batch_size - 1) // batch_size} "
            f"with {len(batch)} titles...",
            flush=True,
        )
        predictions = classify_batch(client=client, batch=batch, previous_tail=previous_tail)
        for item in predictions:
            predictions_by_id[item["title_id"]] = item
        previous_tail = batch[-4:]

    evaluated_items: list[dict[str, Any]] = []
    for title in titles:
        title_id = title["title_id"]
        baseline = baseline_by_block_id.get(title_id, {})
        prediction = predictions_by_id[title_id]
        evaluated_items.append(
            {
                **title,
                "baseline_label": baseline.get("baseline_label", "none"),
                "baseline_source": baseline.get("baseline_source", "default_none"),
                "baseline_ref": baseline.get("baseline_ref"),
                "predicted_label": prediction["label"],
                "prediction_confidence": prediction["confidence"],
                "prediction_rationale": prediction["rationale"],
                "matches_baseline": baseline.get("baseline_label", "none") == prediction["label"],
            }
        )

    summary = evaluate_predictions(
        titles=titles,
        baseline_by_block_id=baseline_by_block_id,
        predictions_by_id=predictions_by_id,
    )
    report = {
        "metadata": {
            "content_list_path": str(content_list_path),
            "kg_space_dir": str(kg_space_dir),
            "model": config.llm.model,
            "batch_size": batch_size,
            "label_set": ALLOWED_LABELS,
        },
        "summary": summary,
        "items": evaluated_items,
    }
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("\nSummary")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nDetailed report written to: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
