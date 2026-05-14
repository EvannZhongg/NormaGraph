from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from adapters.llm_client import ResponsesAPIClient
from core.config import get_config
from services.standard_outline_planner import StandardOutlinePlannerService
from services.standard_pipeline import StandardPipelineService


def normalize_role(value: Any) -> str:
    role = str(value or "").strip().lower()
    if role in {"toc", "appendix", "reference_standard", "chapter", "section", "clause"}:
        return role
    return "ignore"


def load_title_candidates(content_list_path: Path) -> list[dict[str, Any]]:
    config = get_config().model_copy(deep=True)
    pipeline = StandardPipelineService(config=config)
    raw_pages = json.loads(content_list_path.read_text(encoding="utf-8"))
    normalized_blocks = pipeline._flatten_content_list(raw_pages)
    return pipeline._build_title_inventory(normalized_blocks)


def build_baseline_roles(kg_space_dir: Path) -> dict[str, dict[str, Any]]:
    structure_path = kg_space_dir / "normalized_structure.json"
    clauses_path = kg_space_dir / "clauses.json"
    structure = json.loads(structure_path.read_text(encoding="utf-8"))
    clauses = json.loads(clauses_path.read_text(encoding="utf-8"))

    baseline: dict[str, dict[str, Any]] = {}
    for node in structure.get("nodes", []):
        block_id = node.get("source_block_id")
        if not block_id:
            continue
        node_type = normalize_role(node.get("node_type"))
        baseline[block_id] = {
            "baseline_role": node_type,
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
                "baseline_role": "clause",
                "baseline_source": "clauses",
                "baseline_ref": clause.get("clause_ref"),
                "baseline_title": clause.get("source_text_normalized"),
            },
        )

    return baseline


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
        baseline_role = baseline.get("baseline_role", "ignore")
        prediction = predictions_by_id[title_id]
        predicted_role = normalize_role(prediction.get("role"))
        baseline_counts[baseline_role] += 1
        prediction_counts[predicted_role] += 1
        confusion[(baseline_role, predicted_role)] += 1
        if baseline_role == predicted_role:
            exact_match_count += 1
            continue
        mismatches.append(
            {
                "title_id": title_id,
                "page_idx": title["page_idx"],
                "text": title["text"],
                "baseline_role": baseline_role,
                "predicted_role": predicted_role,
                "predicted_ref": prediction.get("ref"),
                "baseline_source": baseline.get("baseline_source", "default_ignore"),
                "baseline_ref": baseline.get("baseline_ref"),
            }
        )

    return {
        "title_count": len(titles),
        "exact_match_count": exact_match_count,
        "exact_match_rate": round(exact_match_count / len(titles), 4) if titles else 0.0,
        "baseline_counts": dict(sorted(baseline_counts.items())),
        "prediction_counts": dict(sorted(prediction_counts.items())),
        "confusion_matrix": [
            {"baseline_role": baseline_role, "predicted_role": predicted_role, "count": count}
            for (baseline_role, predicted_role), count in sorted(confusion.items())
        ],
        "mismatches": mismatches,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LLM title planner evaluation for standard documents.")
    parser.add_argument(
        "--content-list",
        default="data/artifacts/1_sl-258-2017-a2514234-0faab894/content_list_v2.json",
        help="Path to content_list_v2.json",
    )
    parser.add_argument(
        "--kg-space",
        default="data/kg_spaces/sl258-2017",
        help="Path to an existing kg space used as comparison baseline.",
    )
    parser.add_argument("--limit", type=int, help="Optional cap on the number of titles to evaluate.")
    parser.add_argument(
        "--output",
        default="data/test-temp/sl258-title-llm-plan-eval.json",
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
    baseline_by_block_id = build_baseline_roles(kg_space_dir)

    planner = StandardOutlinePlannerService(config, client)
    result = planner.plan_titles(standard_uid=kg_space_dir.name, title_inventory=titles)
    predictions_by_id = {item["title_id"]: item for item in result.items}

    evaluated_items = [
        {
            **title,
            "baseline_role": baseline_by_block_id.get(title["title_id"], {}).get("baseline_role", "ignore"),
            "predicted_role": normalize_role(predictions_by_id[title["title_id"]].get("role")),
            "predicted_ref": predictions_by_id[title["title_id"]].get("ref"),
        }
        for title in titles
        if title["title_id"] in predictions_by_id
    ]
    summary = evaluate_predictions(titles, baseline_by_block_id, predictions_by_id)
    report = {
        "metadata": {
            "content_list_path": str(content_list_path),
            "kg_space_dir": str(kg_space_dir),
            "model": config.llm.model,
            "roles": ["toc", "appendix", "reference_standard", "chapter", "section", "clause", "ignore"],
        },
        "planner_metrics": result.metrics,
        "planner_warnings": result.warnings,
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
