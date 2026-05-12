from __future__ import annotations

from pathlib import Path
import json
import shutil
import sys
import unittest
import uuid

from fastapi import BackgroundTasks


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from core.config import get_config
from services.ingestion_service import IngestionService


class ReportSpaceUnitsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_root = PROJECT_ROOT / "data" / "test-temp" / f"report-space-units-{uuid.uuid4().hex[:8]}"
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self.config = get_config().model_copy(update={"root_dir": self.temp_root}, deep=True)
        self.service = IngestionService(
            self.config,
            job_store=None,
            registry=None,
            mineru_client=None,
            normalization_service=None,
            standard_pipeline_service=None,
            report_pipeline_service=None,
        )
        self.document_id = "report-with-table"
        self.report_space_dir = self.config.report_space_dir_for(self.document_id)
        self.report_space_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(
            "sections.json",
            [
                {
                    "section_uid": "report:section:1",
                    "parent_section_uid": None,
                    "title": "1 工程概况",
                    "section_kind": "section",
                    "path": ["1 工程概况"],
                    "order_index": 1,
                    "page_span": [1, 1],
                    "member_count": 2,
                }
            ],
        )
        self._write_json(
            "report_units.json",
            [
                {
                    "unit_uid": "report:unit:1",
                    "parent_section_uid": "report:section:1",
                    "unit_type": "text",
                    "section_path": ["1 工程概况"],
                    "structural_path": ["1 工程概况"],
                    "text": "这是正文。",
                    "text_normalized": "这是正文。",
                    "order_index": 1,
                    "source_page_span": [1, 1],
                }
            ],
        )
        self._write_json(
            "tables.json",
            [
                {
                    "table_uid": "report:table:1",
                    "parent_section_uid": "report:section:1",
                    "section_path": ["1 工程概况"],
                    "structural_path": ["1 工程概况"],
                    "table_ref": "1.1-1",
                    "table_caption": "表 1.1-1 主要参数表",
                    "table_html": "<table><tr><td>项目</td><td>数值</td></tr></table>",
                    "order_index": 2,
                    "source_page_idx": 1,
                }
            ],
        )
        self._write_json("segmentation_metrics.json", {"report_unit_count": 1, "table_count": 1})
        self._write_json("space_manifest.json", {"artifact_dir": None})

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_root, ignore_errors=True)

    def test_get_report_space_detail_returns_persisted_report_units_only(self) -> None:
        detail = self.service.get_report_space_detail(self.document_id)

        self.assertEqual(len(detail["reportUnits"]), 1)
        self.assertEqual(detail["reportUnits"][0]["unitType"], "text")
        self.assertEqual(detail["reportUnits"][0]["unitUid"], "report:unit:1")

    def test_start_report_comparison_counts_merged_report_units_only(self) -> None:
        background_tasks = BackgroundTasks()

        detail = self.service.start_report_comparison(self.document_id, "sl258:2017", background_tasks)

        self.assertEqual(detail["status"], "queued")
        self.assertEqual(detail["totalUnits"], 1)
        self.assertEqual(detail["completedUnits"], 0)
        self.assertEqual(len(background_tasks.tasks), 1)

    def _write_json(self, name: str, payload: object) -> None:
        (self.report_space_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
