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


class StandardPipelineRichTextTest(unittest.TestCase):
    def setUp(self) -> None:
        temp_root = PROJECT_ROOT / "data" / "test-temp"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.artifact_dir = temp_root / f"standard-rich-text-{uuid.uuid4().hex[:8]}"
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        content = [
            [
                self._title("1 总则"),
                self._paragraph_rich(
                    [
                        {"type": "text", "content": "1.0.1 当流域面积小于 "},
                        {"type": "equation_inline", "content": "1000 km^{2}"},
                        {"type": "text", "content": " 时，应复核设计洪水。"},
                    ]
                ),
                self._table(
                    "表 1.0.1-1 控制指标表",
                    "<table><tr><td>项目</td><td>数值</td></tr><tr><td>库容</td><td>100万m3</td></tr></table>",
                ),
                self._paragraph("1.0.2 倾度法计算公式如下："),
                self._equation(r"\gamma_{AB}=\frac{S_A-S_B}{\Delta l}"),
                self._paragraph_rich(
                    [
                        {"type": "text", "content": "式中 "},
                        {"type": "equation_inline", "content": r"\gamma_{AB}"},
                        {"type": "text", "content": " 为相连两测点之间的倾度。"},
                    ]
                ),
            ]
        ]
        (self.artifact_dir / "content_list_v2.json").write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.artifact_dir, ignore_errors=True)

    def test_formula_and_table_text_are_kept_in_clause_text(self) -> None:
        config = get_config().model_copy(deep=True)
        config.llm.enabled = False
        config.embedding.enabled = False
        config.postgres.enabled = False
        config.knowledge_graph.extraction_mode = "heuristic"

        output = StandardPipelineService(config=config).run(self.artifact_dir, "test:standard")
        clauses = {clause["clause_ref"]: clause for clause in output.clauses}

        clause_101_text = clauses["1.0.1"]["source_text_normalized"]
        self.assertIn(r"\(1000 km^{2}\)", clause_101_text)
        self.assertIn("表 1.0.1-1 控制指标表", clause_101_text)
        self.assertIn("项目 | 数值", clause_101_text)
        self.assertIn("库容 | 100万m3", clause_101_text)

        clause_102_text = clauses["1.0.2"]["source_text_normalized"]
        self.assertIn(r"\[\gamma_{AB}=\frac{S_A-S_B}{\Delta l}\]", clause_102_text)
        self.assertIn(r"\(\gamma_{AB}\)", clause_102_text)

    @staticmethod
    def _title(text: str) -> dict:
        return {
            "type": "title",
            "content": {"title_content": [{"type": "text", "content": text}], "level": 1},
            "bbox": [0, 0, 10, 10],
        }

    @staticmethod
    def _paragraph(text: str) -> dict:
        return {
            "type": "paragraph",
            "content": {"paragraph_content": [{"type": "text", "content": text}]},
            "bbox": [0, 0, 10, 10],
        }

    @staticmethod
    def _paragraph_rich(parts: list[dict]) -> dict:
        return {
            "type": "paragraph",
            "content": {"paragraph_content": parts},
            "bbox": [0, 0, 10, 10],
        }

    @staticmethod
    def _equation(latex: str) -> dict:
        return {
            "type": "equation_interline",
            "content": {"math_content": latex, "math_type": "latex"},
            "bbox": [0, 0, 10, 10],
        }

    @staticmethod
    def _table(caption: str, html: str) -> dict:
        return {
            "type": "table",
            "content": {
                "table_caption": [{"type": "text", "content": caption}],
                "html": html,
            },
            "bbox": [0, 0, 10, 10],
        }


if __name__ == "__main__":
    unittest.main()
