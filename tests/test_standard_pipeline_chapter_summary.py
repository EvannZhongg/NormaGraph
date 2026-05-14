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


class StubChapterSummaryService:
    def summarize_chapters(self, *, standard_uid: str, structure_nodes: list[dict], clauses: list[dict]) -> object:
        del standard_uid, clauses
        chapter_nodes = [node for node in structure_nodes if node.get("node_type") == "chapter"]
        chapter_items = {
            node["node_uid"]: {
                "summary": f'{node.get("title")}章节摘要',
                "summary_source_clause_count": 1,
                "summary_source_truncated": False,
            }
            for node in chapter_nodes
        }
        return type(
            "StubChapterSummaryResult",
            (),
            {
                "chapter_items": chapter_items,
                "warnings": [],
                "metrics": {
                    "chapter_summary_status": "completed",
                    "chapter_summary_discovered_count": len(chapter_nodes),
                    "chapter_summary_requested_count": len(chapter_nodes),
                    "chapter_summary_completed_count": len(chapter_nodes),
                    "chapter_summary_failed_count": 0,
                    "chapter_summary_skipped_count": 0,
                },
            },
        )()


class StandardPipelineChapterSummaryTest(unittest.TestCase):
    def setUp(self) -> None:
        temp_root = PROJECT_ROOT / "data" / "test-temp"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.artifact_dir = temp_root / f"standard-chapter-summary-{uuid.uuid4().hex[:8]}"
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        content = [
            [
                self._title("1 总则"),
                self._title("1.0.1 本标准适用于病险水库大坝安全评价。"),
                self._title("2 安全复核"),
                self._title("2.0.1 应复核防洪能力和结构安全。"),
            ]
        ]
        (self.artifact_dir / "content_list_v2.json").write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.artifact_dir, ignore_errors=True)

    def test_chapter_summary_is_written_back_to_structure_and_graph_nodes(self) -> None:
        config = get_config().model_copy(deep=True)
        config.llm.enabled = True
        config.embedding.enabled = False
        config.postgres.enabled = False
        config.knowledge_graph.extraction_mode = "heuristic"
        config.knowledge_graph.generate_chapter_summaries = True

        planner = StubStandardOutlinePlanner(
            items=[
                {"title_id": "p001-b001", "role": "chapter", "ref": "1", "confidence": 0.99, "rationale": "一级章节"},
                {"title_id": "p001-b002", "role": "clause", "ref": "1.0.1", "confidence": 0.99, "rationale": "条文"},
                {"title_id": "p001-b003", "role": "chapter", "ref": "2", "confidence": 0.99, "rationale": "一级章节"},
                {"title_id": "p001-b004", "role": "clause", "ref": "2.0.1", "confidence": 0.99, "rationale": "条文"},
            ]
        )
        service = StandardPipelineService(
            config=config,
            outline_planner=planner,
            chapter_summary_service=StubChapterSummaryService(),
        )
        output = service.run(self.artifact_dir, "sl-test:2026")

        structure_nodes = {node["node_uid"]: node for node in output.structure_nodes if node["node_type"] == "chapter"}
        graph_nodes = {node["node_uid"]: node for node in output.graph_nodes}

        self.assertEqual(output.metrics["chapter_summary_status"], "completed")
        self.assertEqual(output.metrics["chapter_summary_completed_count"], 2)
        self.assertEqual(structure_nodes["sl-test:2026:chapter:1"]["summary"], "总则章节摘要")
        self.assertEqual(structure_nodes["sl-test:2026:chapter:2"]["summary"], "安全复核章节摘要")
        self.assertEqual(graph_nodes["sl-test:2026:chapter:1"]["properties"]["summary"], "总则章节摘要")
        self.assertIn("总则章节摘要", graph_nodes["sl-test:2026:chapter:1"]["text_content"])

    @staticmethod
    def _title(text: str) -> dict:
        return {
            "type": "title",
            "content": {"title_content": [{"type": "text", "content": text}], "level": 1},
            "bbox": [0, 0, 10, 10],
        }


if __name__ == "__main__":
    unittest.main()
