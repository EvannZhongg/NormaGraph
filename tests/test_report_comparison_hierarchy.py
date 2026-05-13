from __future__ import annotations

from pathlib import Path
import json
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

    def test_report_section_scope_routing_payload_uses_section_context(self) -> None:
        service = IngestionService.__new__(IngestionService)
        long_text = "安全复核内容" * 800
        scope = service._build_report_scope_payload(
            "report:doc:section:2",
            {
                "sectionUid": "report:doc:section:2",
                "title": "2 安全复核",
                "path": ["2 安全复核"],
                "orderIndex": 2,
            },
            [
                {
                    "unitUid": "report:doc:unit:1",
                    "parentSectionUid": "report:doc:section:2",
                    "unitType": "text",
                    "title": "2.1 防洪能力复核",
                    "sectionPath": ["2 安全复核"],
                    "structuralPath": ["2 安全复核"],
                    "textNormalized": long_text,
                    "orderIndex": 3,
                    "pageSpan": [5, 6],
                },
                {
                    "unitUid": "report:doc:unit:2",
                    "parentSectionUid": "report:doc:section:2",
                    "unitType": "text",
                    "title": "2.2 渗流稳定复核",
                    "sectionPath": ["2 安全复核"],
                    "structuralPath": ["2 安全复核"],
                    "textNormalized": "渗流稳定内容",
                    "orderIndex": 4,
                    "pageSpan": [7],
                },
            ],
        )
        prompt = build_report_chapter_routing_prompt(scope, [])
        payload = json.loads(prompt)
        report_unit = payload["report_unit"]

        self.assertEqual(report_unit["title"], "2 安全复核")
        self.assertEqual(report_unit["section_path"], ["2 安全复核"])
        self.assertEqual(report_unit["unit_titles"], ["2.1 防洪能力复核", "2.2 渗流稳定复核"])
        self.assertTrue(report_unit["text"].endswith("...<truncated>"))
        self.assertLessEqual(len(report_unit["text"]), 4000)


if __name__ == "__main__":
    unittest.main()
