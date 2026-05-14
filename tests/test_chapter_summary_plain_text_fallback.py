from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from adapters.llm_client import ResponseAPIOutputError
from core.config import get_config
from services.chapter_summary_service import ChapterSummaryService


class PlainTextSummaryClient:
    enabled = True

    def create_structured_output(self, **kwargs):
        del kwargs
        raise ResponseAPIOutputError(
            "Responses API did not return valid JSON text.",
            raw_text="本章规定水库大坝工程质量评价的目的、内容与方法。要求复核地质条件、基础处理及结构完整性。",
        )


class ChapterSummaryPlainTextFallbackTest(unittest.TestCase):
    def test_plain_text_summary_is_accepted_when_json_parsing_fails(self) -> None:
        config = get_config().model_copy(deep=True)
        config.knowledge_graph.generate_chapter_summaries = True
        service = ChapterSummaryService(config, PlainTextSummaryClient())

        result = service.summarize_chapters(
            standard_uid="sl-test:2026",
            structure_nodes=[
                {
                    "node_uid": "sl-test:2026:chapter:1",
                    "node_type": "chapter",
                    "ref": "1",
                    "title": "工程质量评价",
                    "raw_text": "1 工程质量评价",
                }
            ],
            clauses=[
                {
                    "clause_uid": "sl-test:2026:main:1.0.1",
                    "body_kind": "main",
                    "chapter_ref": "1",
                    "clause_ref": "1.0.1",
                    "source_text_normalized": "1.0.1 应进行工程质量评价。",
                }
            ],
        )

        self.assertEqual(result.metrics["chapter_summary_status"], "completed")
        self.assertEqual(result.metrics["chapter_summary_completed_count"], 1)
        self.assertIn("工程质量评价", result.chapter_items["sl-test:2026:chapter:1"]["summary"])


if __name__ == "__main__":
    unittest.main()
