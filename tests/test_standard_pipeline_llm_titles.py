from __future__ import annotations

from pathlib import Path
import json
import shutil
import sys
import unittest
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from core.config import get_config
from services.standard_pipeline import StandardPipelineService


class StubStandardOutlinePlanner:
    def __init__(self, items: list[dict]) -> None:
        self.items = items
        self.enabled = True

    def plan_titles(self, *, standard_uid: str, title_inventory: list[dict]) -> object:
        del standard_uid, title_inventory
        return type(
            "StubResult",
            (),
            {
                "items": self.items,
                "warnings": [],
                "metrics": {
                    "title_planner_requested_count": len(self.items),
                    "title_planner_batch_count": 1,
                    "title_planner_successful_count": len(self.items),
                    "title_planner_failed_batch_count": 0,
                    "title_planner_role_counts": {},
                },
            },
        )()


class StandardPipelineLLMTitleTest(unittest.TestCase):
    def setUp(self) -> None:
        temp_root = PROJECT_ROOT / "data" / "test-temp"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.artifact_dir = temp_root / f"standard-title-llm-{uuid.uuid4().hex[:8]}"
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        content = [
            [
                self._title("1 总则"),
                self._title("1.0.1 本标准适用于病险水库大坝安全评价。"),
                self._title("2 规范性引用文件"),
                self._title("2.0.1 应符合现行有关标准要求。"),
                self._title("附录 A 术语"),
                self._title("1.0.1 附录资料应完整。"),
            ]
        ]
        (self.artifact_dir / "content_list_v2.json").write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.artifact_dir, ignore_errors=True)

    def test_llm_title_planner_is_used_when_enabled(self) -> None:
        config = get_config().model_copy(deep=True)
        config.llm.enabled = True
        config.embedding.enabled = False
        config.postgres.enabled = False
        config.knowledge_graph.materialize_graph = False
        config.knowledge_graph.extraction_mode = "heuristic"

        planner = StubStandardOutlinePlanner(
            items=[
                {"title_id": "p001-b001", "role": "chapter", "ref": "1", "confidence": 0.99, "rationale": "一级章节"},
                {"title_id": "p001-b002", "role": "clause", "ref": "1.0.1", "confidence": 0.98, "rationale": "条文误识别为标题"},
                {"title_id": "p001-b003", "role": "reference_standard", "ref": "2", "confidence": 0.99, "rationale": "规范性引用文件"},
                {"title_id": "p001-b004", "role": "clause", "ref": "2.0.1", "confidence": 0.98, "rationale": "条文误识别为标题"},
                {"title_id": "p001-b005", "role": "appendix", "ref": "A", "confidence": 0.99, "rationale": "附录标题"},
                {"title_id": "p001-b006", "role": "clause", "ref": "1.0.1", "confidence": 0.98, "rationale": "附录条文"},
            ]
        )
        service = StandardPipelineService(config=config, outline_planner=planner)
        output = service.run(self.artifact_dir, "sl-test:2026")

        structure_by_block = {node["source_block_id"]: node for node in output.structure_nodes}
        clauses = {clause["clause_uid"]: clause for clause in output.clauses}

        self.assertEqual(output.metrics["title_plan_source"], "llm")
        self.assertEqual(output.metrics["title_plan_llm_item_count"], 6)
        self.assertEqual(structure_by_block["p001-b001"]["node_type"], "chapter")
        self.assertEqual(structure_by_block["p001-b003"]["node_type"], "reference_standard")
        self.assertEqual(structure_by_block["p001-b005"]["node_type"], "appendix")
        self.assertNotIn("p001-b002", structure_by_block)
        self.assertNotIn("p001-b004", structure_by_block)
        self.assertIn("sl-test:2026:main:1.0.1", clauses)
        self.assertIn("sl-test:2026:main:2.0.1", clauses)
        self.assertIn("sl-test:2026:appendix-a:1.0.1", clauses)
        self.assertEqual(clauses["sl-test:2026:main:1.0.1"]["chapter_ref"], "1")
        self.assertEqual(clauses["sl-test:2026:main:2.0.1"]["chapter_ref"], "2")
        self.assertEqual(clauses["sl-test:2026:appendix-a:1.0.1"]["appendix_ref"], "A")

    def test_pipeline_accepts_prefixed_content_list_v2_file(self) -> None:
        content_list_path = self.artifact_dir / "content_list_v2.json"
        prefixed_path = self.artifact_dir / "6799bb12-32ae-45c0-ac6a-9eaf395f0a35_content_list_v2.json"
        content_list_path.rename(prefixed_path)

        config = get_config().model_copy(deep=True)
        config.llm.enabled = True
        config.embedding.enabled = False
        config.postgres.enabled = False
        config.knowledge_graph.materialize_graph = False
        config.knowledge_graph.extraction_mode = "heuristic"

        planner = StubStandardOutlinePlanner(
            items=[
                {"title_id": "p001-b001", "role": "chapter", "ref": "1", "confidence": 0.99, "rationale": "一级章节"},
                {"title_id": "p001-b002", "role": "clause", "ref": "1.0.1", "confidence": 0.98, "rationale": "条文误识别为标题"},
                {"title_id": "p001-b003", "role": "reference_standard", "ref": "2", "confidence": 0.99, "rationale": "规范性引用文件"},
                {"title_id": "p001-b004", "role": "clause", "ref": "2.0.1", "confidence": 0.98, "rationale": "条文误识别为标题"},
                {"title_id": "p001-b005", "role": "appendix", "ref": "A", "confidence": 0.99, "rationale": "附录标题"},
                {"title_id": "p001-b006", "role": "clause", "ref": "1.0.1", "confidence": 0.98, "rationale": "附录条文"},
            ]
        )
        service = StandardPipelineService(config=config, outline_planner=planner)
        output = service.run(self.artifact_dir, "sl-test:2026")

        self.assertGreater(output.metrics["normalized_block_count"], 0)
        self.assertEqual(output.structure_nodes[0]["node_type"], "chapter")

    def test_unnumbered_llm_appendix_does_not_crash_clause_uid_generation(self) -> None:
        config = get_config().model_copy(deep=True)
        config.llm.enabled = True
        config.embedding.enabled = False
        config.postgres.enabled = False
        config.knowledge_graph.materialize_graph = False
        config.knowledge_graph.extraction_mode = "heuristic"

        planner = StubStandardOutlinePlanner(
            items=[
                {"title_id": "p001-b001", "role": "chapter", "ref": "1", "confidence": 0.99, "rationale": "一级章节"},
                {"title_id": "p001-b002", "role": "clause", "ref": "1.0.1", "confidence": 0.98, "rationale": "条文误识别为标题"},
                {"title_id": "p001-b003", "role": "reference_standard", "ref": "2", "confidence": 0.99, "rationale": "规范性引用文件"},
                {"title_id": "p001-b004", "role": "clause", "ref": "2.0.1", "confidence": 0.98, "rationale": "条文误识别为标题"},
                {"title_id": "p001-b005", "role": "appendix", "ref": "A", "confidence": 0.99, "rationale": "附录标题"},
                {"title_id": "p001-b006", "role": "clause", "ref": "1.0.1", "confidence": 0.98, "rationale": "附录条文"},
            ]
        )
        service = StandardPipelineService(config=config, outline_planner=planner)
        output = service.run(self.artifact_dir, "sl-test:2026")
        appendix_node = next(node for node in output.structure_nodes if node["node_type"] == "appendix")
        appendix_node["ref"] = None

        self.assertEqual(service._make_clause_uid("sl-test:2026", "appendix", appendix_node, "1.0.1"), "sl-test:2026:appendix-a:1.0.1")

    def test_heuristic_title_plan_recognizes_toc_and_ignores_toc_entries(self) -> None:
        content = [
            [
                self._title("目录"),
                self._title("1 总则 1"),
                self._title("1.0.1 本标准适用于病险水库大坝安全评价。 1"),
            ],
            [
                self._title("1 总则"),
                self._title("1.0.1 本标准适用于病险水库大坝安全评价。"),
            ],
        ]
        (self.artifact_dir / "content_list_v2.json").write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")

        config = get_config().model_copy(deep=True)
        config.llm.enabled = False
        config.embedding.enabled = False
        config.postgres.enabled = False
        config.knowledge_graph.materialize_graph = False
        config.knowledge_graph.extraction_mode = "heuristic"

        service = StandardPipelineService(config=config)
        output = service.run(self.artifact_dir, "sl-test:2026")
        plan_by_text = {item["text_normalized"]: item for item in output.title_plan}
        clauses = {clause["clause_uid"]: clause for clause in output.clauses}

        self.assertEqual(plan_by_text["目录"]["role"], "toc")
        self.assertEqual(plan_by_text["1 总则 1"]["role"], "ignore")
        self.assertEqual(plan_by_text["1.0.1 本标准适用于病险水库大坝安全评价。 1"]["role"], "ignore")
        self.assertIn("sl-test:2026:main:1.0.1", clauses)
        self.assertEqual(clauses["sl-test:2026:main:1.0.1"]["chapter_ref"], "1")

    @staticmethod
    def _title(text: str) -> dict:
        return {
            "type": "title",
            "content": {"title_content": [{"type": "text", "content": text}], "level": 1},
            "bbox": [0, 0, 10, 10],
        }


if __name__ == "__main__":
    unittest.main()
