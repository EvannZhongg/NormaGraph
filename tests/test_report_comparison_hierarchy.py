from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from services.ingestion_service import IngestionService
from services.report_comparison_agent import build_report_chapter_routing_prompt


class ReportComparisonHierarchyTest(unittest.TestCase):
    def test_chapter_summary_is_exposed_to_report_comparison_candidates(self) -> None:
        service = IngestionService.__new__(IngestionService)
        nodes = [
            {
                "node_uid": "sl-test:2026",
                "node_type": "standard",
                "label": "SL TEST",
                "properties": {},
            },
            {
                "node_uid": "sl-test:2026:chapter:1",
                "node_type": "chapter",
                "label": "总则",
                "text_content": "1 总则\n本章摘要",
                "properties": {
                    "ref": "1",
                    "title": "总则",
                    "summary": "本章摘要",
                },
            },
        ]
        edges = [
            {
                "edge_uid": "edge:1",
                "edge_type": "CONTAINS",
                "source_uid": "sl-test:2026",
                "target_uid": "sl-test:2026:chapter:1",
                "properties": {},
            }
        ]

        hierarchy = service._build_standard_hierarchy(nodes, edges)

        self.assertEqual(hierarchy["chapters"][0]["summary"], "本章摘要")

    def test_chapter_routing_prompt_keeps_summary_in_context(self) -> None:
        prompt = build_report_chapter_routing_prompt(
            {"unit_uid": "u1", "text": "报告提到防洪能力复核。"},
            [
                {
                    "id": "sl-test:2026:chapter:7",
                    "ref": "7",
                    "label": "防洪能力复核",
                    "title": "防洪能力复核",
                    "summary": "本章主要规定防洪标准、泄洪能力与复核要求。",
                }
            ],
        )

        self.assertIn('"summary": "本章主要规定防洪标准、泄洪能力与复核要求。"', prompt)


if __name__ == "__main__":
    unittest.main()
