from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from core.config import get_config
from services.standard_pipeline import StandardPipelineService


class StandardPipelineTableIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        config = get_config().model_copy(deep=True)
        config.llm.enabled = False
        config.embedding.enabled = False
        config.postgres.enabled = False
        config.knowledge_graph.extraction_mode = "heuristic"

        artifact_dir = PROJECT_ROOT / "data" / "artifacts" / "6_sl-253-2018-b005eabd-b20f8321"
        service = StandardPipelineService(config=config)
        output = service.run(artifact_dir, "sl253:2018")

        cls.output = output
        cls.blocks = {block["block_id"]: block for block in output.normalized_blocks}
        cls.clauses = {clause["clause_uid"]: clause for clause in output.clauses}
        cls.graph_nodes = {node["node_uid"]: node for node in output.graph_nodes}
        cls.graph_edges = output.graph_edges
        cls.embedding_documents = {item["node_uid"]: item for item in output.embedding_documents}
        cls.requirements = output.requirements

    def test_inline_title_is_not_promoted_to_structure(self) -> None:
        self.assertFalse(any(node.get("source_block_id") == "p031-b007" for node in self.output.structure_nodes))
        self.assertEqual(self.clauses["sl253:2018:main:5.3.10"]["chapter_ref"], "5")

    def test_table_blocks_capture_caption_and_reference(self) -> None:
        table_1 = self.blocks["p031-b009"]
        table_2 = self.blocks["p031-b011"]

        self.assertEqual(table_1["table_ref"], "5.3.9-1")
        self.assertEqual(table_2["table_ref"], "5.3.9-2")
        self.assertNotIn("table_text", table_1)
        self.assertNotIn("table_text", table_2)
        self.assertIn("<table>", table_1["table_html"])
        self.assertIn("溢洪道的级别", table_2["table_html"])

    def test_clause_539_keeps_inline_title_and_owns_tables(self) -> None:
        clause = self.clauses["sl253:2018:main:5.3.9"]
        text = clause["source_text_normalized"]
        table_refs = [table["table_ref"] for table in clause["tables"]]

        self.assertIn("3 抗滑稳定安全系数规定如下：", text)
        self.assertIn("1）按抗剪断强度公式（5.3.9-1）计算的堰基面抗滑稳定安全系数", text)
        self.assertIn("2）按抗剪强度公式（5.3.9-2）计算的堰基面抗滑稳定安全系数", text)
        self.assertIn("5.3.9-1", table_refs)
        self.assertIn("5.3.9-2", table_refs)
        self.assertGreaterEqual(clause["table_count"], 2)
        self.assertEqual([item["item_ref"] for item in clause["list_items"]], ["1）", "2）"])
        for table in clause["tables"]:
            self.assertIn("table_html", table)
            self.assertNotIn("table_text", table)

    def test_clause_539_requirements_are_split_from_list_items(self) -> None:
        clause_requirements = [item for item in self.requirements if item["parent_clause_uid"] == "sl253:2018:main:5.3.9"]

        self.assertEqual(len(clause_requirements), 2)
        self.assertTrue(clause_requirements[0]["requirement_text"].startswith("按抗剪断强度公式（5.3.9-1）"))
        self.assertTrue(clause_requirements[1]["requirement_text"].startswith("按抗剪强度公式（5.3.9-2）"))

    def test_graph_contains_table_nodes_under_clause_539(self) -> None:
        clause_uid = "sl253:2018:main:5.3.9"
        table_nodes = {
            node_uid: node
            for node_uid, node in self.graph_nodes.items()
            if node["node_type"] == "table" and node["properties"].get("parent_clause_uid") == clause_uid
        }

        table_refs = {node["properties"].get("table_ref") for node in table_nodes.values()}
        self.assertIn("5.3.9-1", table_refs)
        self.assertIn("5.3.9-2", table_refs)
        self.assertTrue(
            any(
                edge["edge_type"] == "CONTAINS"
                and edge["source_uid"] == clause_uid
                and edge["target_uid"] in table_nodes
                for edge in self.graph_edges
            )
        )
        target_node = next(node for node in table_nodes.values() if node["properties"].get("table_ref") == "5.3.9-1")
        self.assertNotIn("table_text", target_node["properties"])
        self.assertIn("<table>", target_node["properties"]["table_html"])
        self.assertEqual(target_node["text_content"], "结构设计 > 控制段\n5.3.9\n表 5.3.9-1 堰基面抗滑稳定安全系数{\\mathbf{K}}^{\\prime }")
        self.assertIn("<table>", self.embedding_documents[target_node["node_uid"]]["text"])


if __name__ == "__main__":
    unittest.main()
