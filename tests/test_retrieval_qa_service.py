from __future__ import annotations

import json
import tempfile
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from adapters.llm_client import EmbeddingsAPIClient, ResponsesAPIClient
from core.config import get_config
from models.schemas import QuestionRequest
from services.retrieval_qa_service import RetrievalQAService


class FakeResponsesClient(ResponsesAPIClient):
    def __init__(self, config):
        super().__init__(config)
        self._routing_payloads = []
        self._answer_payloads = []

    def create_structured_output(self, *, system_prompt, user_prompt, schema_name, schema):
        self._routing_payloads.append(json.loads(user_prompt))
        return {
            "selected_chapters": [{"node_uid": "sl258:2017:chapter:1", "reason": "general scope"}],
            "selected_sections": [],
        }

    def create_text_output(self, *, system_prompt, user_prompt):
        self._answer_payloads.append(json.loads(user_prompt))
        return "检索回答"


class FakeEmbeddingsClient(EmbeddingsAPIClient):
    def __init__(self, config):
        super().__init__(config)

    def embed_texts(self, texts):
        self._call_count += 1
        self._request_attempt_count += 1
        self._input_tokens += len(texts)
        self._total_tokens += len(texts)
        self._last_usage = {"input_tokens": len(texts), "total_tokens": len(texts)}
        return [[1.0, 0.0, 0.0] for _ in texts]


class RetrievalQAServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = get_config()
        cls.config.llm.enabled = True
        cls.config.llm.api_key = "test"
        cls.config.embedding.enabled = True
        cls.config.embedding.api_key = "test"

    def test_answer_writes_run_log_and_returns_citations(self) -> None:
        config = self.config.model_copy(deep=True)
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            config.root_dir = tmp_path
            config.storage.data_dir = "data"
            config.storage.kg_spaces_dir = "data/kg_spaces"
            source_space = Path("data/kg_spaces/sl258-2017")
            target_space = tmp_path / "data/kg_spaces/sl258-2017"
            target_space.parent.mkdir(parents=True, exist_ok=True)
            import shutil

            shutil.copytree(source_space, target_space)

            service = RetrievalQAService(
                config,
                llm_client=FakeResponsesClient(config),
                embedding_client=FakeEmbeddingsClient(config),
            )
            response = service.answer(
                QuestionRequest(
                    question="防洪安全评价需要哪些资料？",
                    standardIds=["sl258:2017"],
                    queryMode="hybrid",
                    topK=4,
                    chunkTopK=6,
                    historyTurns=2,
                    rerank=True,
                    userPrompt=None,
                )
            )

            self.assertEqual(response.answer, "检索回答")
            self.assertTrue(response.citations)
            run_dir = tmp_path / "data/rag_runs"
            run_files = list(run_dir.rglob("run.json"))
            self.assertEqual(len(run_files), 1)
            payload = json.loads(run_files[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["kg_space"]["standard_id"], "sl258:2017")
            self.assertIn("usage", payload)
            self.assertIn("embedding", payload["usage"])
            self.assertNotIn("rank", json.dumps(payload["retrieval"]["contexts"], ensure_ascii=False))
            self.assertNotIn("score", json.dumps(payload["retrieval"]["contexts"], ensure_ascii=False))

    def test_filter_selected_nodes_accepts_string_items(self) -> None:
        selected = RetrievalQAService._filter_selected_nodes(
            ["sl258:2017:chapter:1", {"node_uid": "sl258:2017:chapter:2", "reason": "matched"}],
            {"sl258:2017:chapter:1", "sl258:2017:chapter:2"},
        )

        self.assertEqual(
            selected,
            [
                {"node_uid": "sl258:2017:chapter:1", "reason": ""},
                {"node_uid": "sl258:2017:chapter:2", "reason": "matched"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
