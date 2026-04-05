from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from core.config import get_config
from models.schemas import StandardDetail
from repositories.standard_registry import StandardRegistry
from services.standard_pipeline import StandardPipelineService


def _sync_registry(config, standard_id: str, artifact_dir: Path, graph_space_dir: Path) -> None:
    registry = StandardRegistry(config.registry_path)
    existing = registry.get(standard_id)
    year = standard_id.split(':')[-1] if ':' in standard_id else None
    default_code = standard_id.split(':')[0].upper() if ':' in standard_id else standard_id.upper()
    detail = StandardDetail(
        standardId=standard_id,
        code=existing.code if existing else default_code,
        year=existing.year if existing else year,
        title=existing.title if existing else standard_id,
        aliases=existing.aliases if existing else [],
        effectiveDate=existing.effectiveDate if existing else None,
        documentId=artifact_dir.name,
        artifactDir=str(artifact_dir),
        graphSpaceDir=str(graph_space_dir),
        graphStatus='ready',
        latestJobId=existing.latestJobId if existing else None,
    )
    registry.upsert(detail)


def main() -> None:
    parser = argparse.ArgumentParser(description='Run structure normalization, clause segmentation, and LLM/heuristic KG extraction for a MinerU artifact directory.')
    parser.add_argument('--artifact-dir', required=True, help='Path to the parse artifact directory containing content_list_v2.json')
    parser.add_argument('--standard-id', required=True, help='Standard UID, for example sl258:2017')
    parser.add_argument('--graph-space-dir', help='Optional output graph space directory. Defaults to data/kg_spaces/<standard_id>.')
    parser.add_argument('--disable-llm', action='store_true', help='Force heuristic extraction for this run only.')
    parser.add_argument('--llm-timeout-seconds', type=int, help='Override LLM response timeout for this run only.')
    args = parser.parse_args()

    artifact_dir = Path(args.artifact_dir)
    if not artifact_dir.is_absolute():
        artifact_dir = (PROJECT_ROOT / artifact_dir).resolve()

    config = get_config()
    if args.disable_llm:
        config.llm.enabled = False
        config.knowledge_graph.extraction_mode = 'heuristic'
    if args.llm_timeout_seconds is not None:
        config.llm.timeout_seconds = args.llm_timeout_seconds

    if args.graph_space_dir:
        graph_space_dir = Path(args.graph_space_dir)
        if not graph_space_dir.is_absolute():
            graph_space_dir = (PROJECT_ROOT / graph_space_dir).resolve()
    else:
        graph_space_dir = config.kg_space_dir_for(args.standard_id)

    service = StandardPipelineService(config=config)
    output = service.run(artifact_dir, args.standard_id)
    files = service.write_outputs(
        graph_space_dir,
        output,
        artifact_dir=artifact_dir,
        standard_uid=args.standard_id,
        document_id=artifact_dir.name,
    )
    _sync_registry(config, args.standard_id, artifact_dir, graph_space_dir)

    print('artifact_dir', artifact_dir)
    print('graph_space_dir', graph_space_dir)
    print('normalized_blocks', len(output.normalized_blocks))
    print('structure_nodes', len(output.structure_nodes))
    print('clauses', len(output.clauses))
    print('requirements', len(output.requirements))
    print('graph_nodes', len(output.graph_nodes))
    print('graph_edges', len(output.graph_edges))
    print('embedding_documents', len(output.embedding_documents))
    print('extraction_mode_effective', output.metrics.get('extraction_mode_effective'))
    print('embedding_generation_status', output.metrics.get('embedding_generation_status'))
    print('postgres_persist_status', output.metrics.get('postgres_persist_status'))
    for key, path in files.items():
        print(key, path)


if __name__ == '__main__':
    main()
