from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from core.config import get_config
from services.report_pipeline import ReportPipelineService


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Build a report_space from a MinerU artifact directory containing content_list_v2.json.'
    )
    parser.add_argument('--artifact-dir', required=True, help='Path to the parse artifact directory.')
    parser.add_argument('--document-id', help='Optional document identifier. Defaults to artifact directory name.')
    parser.add_argument('--report-space-dir', help='Optional output directory. Defaults to data/report_spaces/<document_id>.')
    parser.add_argument('--source-path', help='Optional original source file path for manifest metadata.')
    args = parser.parse_args()

    artifact_dir = Path(args.artifact_dir)
    if not artifact_dir.is_absolute():
        artifact_dir = (PROJECT_ROOT / artifact_dir).resolve()
    if not artifact_dir.exists():
        raise FileNotFoundError(f'Artifact directory was not found: {artifact_dir}')

    document_id = args.document_id or artifact_dir.name
    config = get_config()

    if args.report_space_dir:
        report_space_dir = Path(args.report_space_dir)
        if not report_space_dir.is_absolute():
            report_space_dir = (PROJECT_ROOT / report_space_dir).resolve()
    else:
        report_space_dir = config.report_space_dir_for(document_id)

    source_path = None
    if args.source_path:
        source_path = Path(args.source_path)
        if not source_path.is_absolute():
            source_path = (PROJECT_ROOT / source_path).resolve()

    service = ReportPipelineService(config=config)
    output = service.run(artifact_dir, document_id)
    files = service.write_outputs(
        report_space_dir,
        output,
        artifact_dir=artifact_dir,
        document_id=document_id,
        source_path=source_path,
    )

    print('artifact_dir', artifact_dir)
    print('document_id', document_id)
    print('report_space_dir', report_space_dir)
    print('normalized_blocks', len(output.normalized_blocks))
    print('title_inventory', len(output.title_inventory))
    print('title_plan', len(output.title_plan))
    print('title_plan_source', output.metrics.get('title_plan_source'))
    print('sections', len(output.sections))
    print('report_units', len(output.report_units))
    print('tables', len(output.tables))
    print('figures', len(output.figures))
    print('report_nodes', len(output.report_nodes))
    print('report_edges', len(output.report_edges))
    print('embedding_documents', len(output.embedding_documents))
    print('section_kind_counts', output.metrics.get('section_kind_counts'))
    for key, path in files.items():
        print(key, path)


if __name__ == '__main__':
    main()
