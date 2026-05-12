from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from services.ingestion_service import IngestionService


class ReportClauseSummaryAggregationTest(unittest.TestCase):
    def test_clause_summaries_are_unique_and_missing_is_global(self) -> None:
        service = IngestionService.__new__(IngestionService)
        aggregate = service._aggregate_report_comparison(
            [
                {
                    "reportUnitId": "unit-1",
                    "matchedChapterIds": ["chapter-1"],
                    "matchedSectionIds": ["section-1"],
                    "items": [
                        {
                            "clauseId": "clause-1",
                            "status": "covered",
                            "reason": "覆盖证据 1",
                            "reportEvidence": "报告证据 1",
                        },
                        {
                            "clauseId": "clause-2",
                            "status": "covered",
                            "reason": "覆盖证据 2",
                            "reportEvidence": "报告证据 2",
                        },
                    ],
                },
                {
                    "reportUnitId": "unit-2",
                    "matchedChapterIds": ["chapter-1"],
                    "matchedSectionIds": ["section-1"],
                    "items": [
                        {
                            "clauseId": "clause-1",
                            "status": "covered",
                            "reason": "覆盖证据 3",
                            "reportEvidence": "报告证据 3",
                        },
                        {
                            "clauseId": "clause-2",
                            "status": "violated",
                            "reason": "违规证据",
                            "reportEvidence": "报告违规证据",
                        },
                        {
                            "clauseId": "unknown-clause",
                            "status": "violated",
                            "reason": "未知条款应丢弃",
                            "reportEvidence": None,
                        },
                    ],
                },
            ],
            [
                {"id": "clause-1", "clause_ref": "1.0.1", "section_id": "section-1", "chapter_id": "chapter-1", "label": "1.0.1"},
                {"id": "clause-2", "clause_ref": "1.0.2", "section_id": "section-1", "chapter_id": "chapter-1", "label": "1.0.2"},
                {"id": "clause-3", "clause_ref": "1.0.3", "section_id": "section-1", "chapter_id": "chapter-1", "label": "1.0.3"},
            ],
        )

        summaries = {item["clauseId"]: item for item in aggregate["clauseSummaries"]}

        self.assertEqual(len(summaries), 3)
        self.assertEqual(summaries["clause-1"]["finalStatus"], "covered")
        self.assertEqual(summaries["clause-1"]["coveredCount"], 2)
        self.assertEqual(summaries["clause-2"]["finalStatus"], "violated")
        self.assertEqual(summaries["clause-2"]["coveredCount"], 1)
        self.assertEqual(summaries["clause-2"]["violatedCount"], 1)
        self.assertEqual(summaries["clause-3"]["finalStatus"], "missing")
        self.assertEqual(summaries["clause-3"]["evidenceUnits"], [])
        self.assertEqual(aggregate["summary"], "clauses=3, covered=1, violated=1, missing=1")
        self.assertEqual(aggregate["coverageScore"], 0.3333)
        self.assertEqual([item["clauseId"] for item in aggregate["items"]], ["clause-1", "clause-2"])


if __name__ == "__main__":
    unittest.main()
