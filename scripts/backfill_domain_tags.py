"""Replace previously generated domain tags in standard KG spaces via the tagging API."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adapters.llm_client import ResponsesAPIClient
from core.config import get_config
from services.domain_tagging import DomainTaggingService
from services.graph_materialization import GraphMaterializationService


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def backfill_space(space_dir: Path, tagging: DomainTaggingService, graph_builder: GraphMaterializationService, chunk_size: int) -> dict[str, int]:
    clauses_path = space_dir / "clauses.json"
    requirements_path = space_dir / "requirements.json"
    if not clauses_path.exists() or not requirements_path.exists():
        return {"clauses": 0, "requirements": 0, "tag_calls": 0}
    clauses = json.loads(clauses_path.read_text(encoding="utf-8"))
    requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
    for clause in clauses:
        clause.pop("concepts", None)
        clause["domain_tags"] = []
    for requirement in requirements:
        requirement["domain_tags"] = []

    standard_uid = str((json.loads((space_dir / "space_manifest.json").read_text(encoding="utf-8")) if (space_dir / "space_manifest.json").exists() else {}).get("standard_id") or space_dir.name)
    calls = 0
    tags_by_clause: dict[str, list[str]] = {}
    for start in range(0, len(clauses), max(1, chunk_size)):
        batch = clauses[start : start + max(1, chunk_size)]
        result = tagging.tag_clauses(standard_uid, batch)
        calls += result.metrics.get("tag_call_count", 0)
        tags_by_clause.update(result.clause_tags)
    for clause in clauses:
        clause["domain_tags"] = tags_by_clause.get(clause.get("clause_uid"), [])
    for requirement in requirements:
        requirement["domain_tags"] = list(tags_by_clause.get(requirement.get("parent_clause_uid"), []))

    _write(clauses_path, clauses)
    _write(requirements_path, requirements)

    structure_path = space_dir / "normalized_structure.json"
    if structure_path.exists():
        structure = json.loads(structure_path.read_text(encoding="utf-8"))
        graph = graph_builder.build(
            standard_uid=standard_uid,
            structure_nodes=structure.get("nodes", []),
            clauses=clauses,
            requirements=requirements,
        )
        _write(space_dir / "graph_nodes.json", graph.nodes)
        _write(space_dir / "graph_edges.json", graph.edges)
    return {"clauses": len(clauses), "requirements": len(requirements), "tag_calls": calls}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--standard-id", help="Only process one KG space")
    parser.add_argument("--chunk-size", type=int, default=6)
    args = parser.parse_args()
    config = get_config()
    client = ResponsesAPIClient(config)
    tagging = DomainTaggingService(config, client)
    if not tagging.enabled:
        raise SystemExit(f"{config.llm.api_key_env} is required for domain tag backfill")
    graph_builder = GraphMaterializationService(config)
    spaces = [config.kg_space_dir_for(args.standard_id)] if args.standard_id else sorted(config.kg_spaces_dir.iterdir())
    totals = {"clauses": 0, "requirements": 0, "tag_calls": 0}
    for space in spaces:
        if not space.is_dir():
            continue
        stats = backfill_space(space, tagging, graph_builder, args.chunk_size)
        print(f"{space.name}: {stats}")
        for key, value in stats.items():
            totals[key] += value
    print(f"total: {totals}")


if __name__ == "__main__":
    main()
