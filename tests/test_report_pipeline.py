from __future__ import annotations

from pathlib import Path
import json
import shutil
import sys
import unittest
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from core.config import get_config
from services.report_pipeline import ReportPipelineService
from services.report_outline_planner import ReportTitlePlanResult


class StubOutlinePlanner:
    def __init__(self, items: list[dict]) -> None:
        self.items = items
        self.enabled = True

    def plan_titles(self, document_id: str, title_inventory: list[dict]) -> ReportTitlePlanResult:
        return ReportTitlePlanResult(
            items=self.items,
            metrics={
                'planner_requested_title_count': len(title_inventory),
                'planner_batch_count': 1,
                'planner_successful_title_count': len(self.items),
                'planner_failed_batch_count': 0,
            },
        )


class ReportPipelineStructureTest(unittest.TestCase):
    def setUp(self) -> None:
        temp_root = PROJECT_ROOT / 'data' / 'test-temp'
        temp_root.mkdir(parents=True, exist_ok=True)
        self.artifact_dir = temp_root / f'report-pipeline-{uuid.uuid4().hex[:8]}'
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        content = [
            [
                self._title('内容提要'),
                self._paragraph('本报告对工程安全情况进行综合评价。'),
            ],
            [
                self._title('1 引言'),
                self._title('1.1 工作基础'),
                self._paragraph('本次安全评价在前期资料收集基础上开展。'),
                self._title('1 基础资料收集'),
                self._paragraph('收集了设计、施工、监测等资料。'),
                self._title('2 现场安全检查'),
                self._paragraph('组织了现场检查并形成记录。'),
                self._title('2 工程概况'),
                self._paragraph('工程由大坝和泄洪洞组成。'),
                self._title('2.1 工程基本情况'),
                self._paragraph('工程规模为中型。'),
                self._table('表 2.1-1 工程特性表', '<table><tr><td>项目</td><td>数值</td></tr></table>'),
                self._image('图 2.1-1 工程布置图', 'images/sample.jpg'),
            ]
        ]
        (self.artifact_dir / 'content_list_v2.json').write_text(json.dumps(content, ensure_ascii=False), encoding='utf-8')

    def tearDown(self) -> None:
        shutil.rmtree(self.artifact_dir, ignore_errors=True)

    def test_numeric_titles_inside_subsection_are_demoted_to_topics(self) -> None:
        config = get_config().model_copy(deep=True)
        config.llm.enabled = False
        service = ReportPipelineService(config=config)
        output = service.run(self.artifact_dir, 'report-doc')

        sections_by_title = {section['title']: section for section in output.sections}

        self.assertEqual(sections_by_title['1 引言']['section_kind'], 'chapter')
        self.assertEqual(sections_by_title['1.1 工作基础']['section_kind'], 'section')
        self.assertEqual(sections_by_title['1 基础资料收集']['section_kind'], 'topic')
        self.assertEqual(sections_by_title['2 现场安全检查']['section_kind'], 'topic')
        self.assertEqual(sections_by_title['2 工程概况']['section_kind'], 'chapter')
        self.assertEqual(sections_by_title['2.1 工程基本情况']['section_kind'], 'section')
        self.assertEqual(sections_by_title['1 基础资料收集']['parent_section_uid'], sections_by_title['1.1 工作基础']['section_uid'])

        self.assertEqual(len(output.tables), 1)
        self.assertEqual(output.tables[0]['table_ref'], '2.1-1')
        self.assertEqual(len(output.figures), 1)
        self.assertEqual(output.figures[0]['figure_ref'], '2.1-1')
        self.assertTrue(any(node['node_type'] == 'report_table' for node in output.report_nodes))
        self.assertTrue(any(node['node_type'] == 'report_figure' for node in output.report_nodes))
        self.assertGreaterEqual(len(output.report_units), 4)
        self.assertEqual(output.metrics['title_plan_source'], 'heuristic')
        self.assertEqual(len(output.title_inventory), 7)
        self.assertEqual(output.title_plan[0]['title_id'], output.title_inventory[0]['title_id'])

    def test_pipeline_can_apply_external_title_plan_overrides(self) -> None:
        config = get_config().model_copy(deep=True)
        config.llm.enabled = False
        planner = StubOutlinePlanner(
            items=[
                {
                    'title_id': 'p002-b004',
                    'role': 'section',
                    'section_kind': 'section',
                    'hierarchy_level': 2,
                    'is_structural': True,
                    'ref': '1',
                    'confidence': 0.95,
                    'rationale': '在这个测试中把局部标题提升为结构节。',
                    'planner_source': 'llm',
                }
            ]
        )
        service = ReportPipelineService(config=config, outline_planner=planner)
        output = service.run(self.artifact_dir, 'report-doc')

        sections_by_title = {section['title']: section for section in output.sections}
        self.assertEqual(sections_by_title['1 基础资料收集']['section_kind'], 'section')
        self.assertEqual(output.metrics['title_plan_source'], 'hybrid')
        planned_item = next(item for item in output.title_plan if item['title_id'] == 'p002-b004')
        self.assertEqual(planned_item['planner_source'], 'llm')
        self.assertEqual(planned_item['section_kind'], 'section')

    @staticmethod
    def _title(text: str) -> dict:
        return {
            'type': 'title',
            'content': {'title_content': [{'type': 'text', 'content': text}], 'level': 1},
            'bbox': [0, 0, 10, 10],
        }

    @staticmethod
    def _paragraph(text: str) -> dict:
        return {
            'type': 'paragraph',
            'content': {'paragraph_content': [{'type': 'text', 'content': text}]},
            'bbox': [0, 0, 10, 10],
        }

    @staticmethod
    def _table(caption: str, html: str) -> dict:
        return {
            'type': 'table',
            'content': {
                'table_caption': [{'type': 'text', 'content': caption}],
                'html': html,
                'image_source': {'path': 'images/table.jpg'},
            },
            'bbox': [0, 0, 10, 10],
        }

    @staticmethod
    def _image(caption: str, path: str) -> dict:
        return {
            'type': 'image',
            'content': {
                'image_caption': [{'type': 'text', 'content': caption}],
                'image_source': {'path': path},
            },
            'bbox': [0, 0, 10, 10],
        }


if __name__ == '__main__':
    unittest.main()
