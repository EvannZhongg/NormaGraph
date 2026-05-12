from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from services.ingestion_service import IngestionService


class ReportComparisonGraphSummaryTest(unittest.TestCase):
    def test_report_unit_graph_node_carries_unit_summary(self) -> None:
        service = IngestionService.__new__(IngestionService)
        graph = service._build_report_comparison_graph(
            document_id="report-doc",
            report_unit={
                "unitUid": "report:doc:unit:14",
                "textNormalized": "应急预案文本",
                "sectionPath": ["5 运行管理评价", "5.2 调度运行", "5.2.2 应急预案"],
            },
            report_summary="matched covered=1, violated=0",
            standard_id="sl258:2017",
            nodes=[],
            edges=[],
            matched_chapter_ids=[],
            matched_section_ids=[],
            comparison_items=[],
        )

        report_node = graph["nodes"][0]
        self.assertEqual(report_node["nodeType"], "report_unit")
        self.assertEqual(
            report_node["properties"]["summary"],
            "matched covered=1, violated=0",
        )
        self.assertEqual(report_node["properties"]["text_content"], "应急预案文本")


if __name__ == "__main__":
    unittest.main()
