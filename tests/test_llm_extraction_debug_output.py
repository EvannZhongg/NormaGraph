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
from services.llm_extraction import LLMGraphExtractionService


class FailingClient:
    enabled = True

    def create_structured_output(self, **kwargs):
        del kwargs
        raise ResponseAPIOutputError(
            "Responses API response did not contain output_text.",
            payload={"id": "resp-test", "status": "completed", "output": [{"content": []}]},
        )


class LLMExtractionDebugOutputTest(unittest.TestCase):
    def test_failed_clause_error_includes_response_payload_preview(self) -> None:
        config = get_config().model_copy(deep=True)
        config.llm.enabled = True
        config.llm.batch_max_retries = 0
        service = LLMGraphExtractionService(config, FailingClient())

        result = service.extract_clauses(
            "sl-test:2026",
            [
                {
                    "clause_uid": "sl-test:2026:main:1.0.1",
                    "clause_ref": "1.0.1",
                    "chapter_ref": "1",
                    "section_ref": None,
                    "source_text_normalized": "1.0.1 应进行安全评价。",
                    "list_items": [],
                }
            ],
        )

        self.assertEqual(result.metrics["failed_clause_call_count"], 1)
        self.assertEqual(result.metrics["failed_batch_count"], 0)
        self.assertIn("response_payload_preview=", result.warnings[0])
        self.assertIn("resp-test", result.warnings[0])


if __name__ == "__main__":
    unittest.main()
