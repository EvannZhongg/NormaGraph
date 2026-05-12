from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from services.ingestion_service import IngestionService


class ReportComparisonFailureFallbackTest(unittest.TestCase):
    def test_failed_report_unit_result_is_materialized_without_raising(self) -> None:
        service = IngestionService.__new__(IngestionService)
        result = service._build_failed_report_unit_result(
            document_id="report-doc",
            report_unit={"unitUid": "unit-1", "parentSectionUid": "section-1", "text": "text", "sectionPath": []},
            standard_id="sl258:2017",
            nodes=[],
            edges=[],
            error="Report comparison returned no clause assessments for sl258:2017.",
            matched_chapter_ids=["sl258:2017:chapter:6"],
            matched_section_ids=["sl258:2017:section:6.2"],
            chapter_routing_reasoning="已定位到运行管理章节",
            section_routing_reasoning="已定位到6.2节",
        )

        self.assertEqual(result["coverageScore"], 0.0)
        self.assertEqual(result["error"], "Report comparison returned no clause assessments for sl258:2017.")
        self.assertEqual(result["matchedChapterIds"], ["sl258:2017:chapter:6"])
        self.assertEqual(result["items"], [])
        self.assertIn("Evaluation failed after retries", result["summary"])
        self.assertEqual(result["graph"]["nodes"][0]["properties"]["summary"], result["summary"])


if __name__ == "__main__":
    unittest.main()
