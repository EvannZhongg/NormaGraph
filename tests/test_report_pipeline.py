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
                self._paragraph_rich([
                    {'type': 'text', 'content': '堰上总水头取 '},
                    {'type': 'equation_inline', 'content': 'H_0 = H'},
                    {'type': 'text', 'content': ' 进行计算。'},
                ]),
                self._equation('Q = m \\varepsilon B \\sigma_{s} \\sqrt{2g} \\cdot H_{0}^{3/2}'),
                self._table('表 2.1-1 工程特性表', '<table><tr><td>项目</td><td>数值</td></tr></table>'),
                self._image('图 2.1-1 工程布置图', 'images/sample.jpg'),
            ]
        ]
        (self.artifact_dir / 'content_list_v2.json').write_text(json.dumps(content, ensure_ascii=False), encoding='utf-8')

    def tearDown(self) -> None:
        shutil.rmtree(self.artifact_dir, ignore_errors=True)

    def test_llm_title_plan_drives_report_sections(self) -> None:
        config = get_config().model_copy(deep=True)
        config.llm.enabled = False
        service = ReportPipelineService(config=config, outline_planner=StubOutlinePlanner(self._default_title_plan_items()))
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
        self.assertNotIn('table_text', output.tables[0])
        self.assertEqual(output.tables[0]['table_html'], '<table><tr><td>项目</td><td>数值</td></tr></table>')
        self.assertEqual(len(output.figures), 1)
        self.assertEqual(output.figures[0]['figure_ref'], '2.1-1')
        self.assertTrue(any(node['node_type'] == 'report_table' for node in output.report_nodes))
        self.assertTrue(any(node['node_type'] == 'report_figure' for node in output.report_nodes))
        self.assertGreaterEqual(len(output.report_units), 4)
        combined_unit_text = '\n'.join(unit['text_normalized'] for unit in output.report_units)
        self.assertIn(r'\(H_0 = H\)', combined_unit_text)
        self.assertIn(r'\[Q = m \varepsilon B \sigma_{s} \sqrt{2g} \cdot H_{0}^{3/2}\]', combined_unit_text)
        self.assertEqual(output.metrics['title_plan_source'], 'llm')
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
        self.assertEqual(output.metrics['title_plan_source'], 'llm')
        planned_item = next(item for item in output.title_plan if item['title_id'] == 'p002-b004')
        self.assertEqual(planned_item['planner_source'], 'llm')
        self.assertEqual(planned_item['section_kind'], 'section')

    def test_pipeline_accepts_prefixed_content_list_v2_file(self) -> None:
        content_list_path = self.artifact_dir / 'content_list_v2.json'
        prefixed_path = self.artifact_dir / '6799bb12-32ae-45c0-ac6a-9eaf395f0a35_content_list_v2.json'
        content_list_path.rename(prefixed_path)

        config = get_config().model_copy(deep=True)
        config.llm.enabled = False
        service = ReportPipelineService(
            config=config,
            outline_planner=StubOutlinePlanner(
                [
                    {
                        'title_id': 'p001-b001',
                        'role': 'front_matter',
                        'section_kind': 'front_matter',
                        'hierarchy_level': 1,
                        'is_structural': False,
                        'ref': None,
                    }
                ]
            ),
        )
        output = service.run(self.artifact_dir, 'report-doc')

        self.assertGreater(output.metrics['normalized_block_count'], 0)
        self.assertEqual(output.sections[0]['title'], '内容提要')

    def test_text_units_merge_continuous_text_until_section_or_non_text_boundary(self) -> None:
        content = [
            [
                self._title('1 工程概况'),
                self._title('1.1 工程设计及审批过程'),
                self._paragraph('第一段审批过程。'),
                self._paragraph('第二段审批过程。'),
                self._paragraph('第三段审批过程。'),
            ],
            [
                self._paragraph('第四段审批过程。'),
                self._paragraph('第五段审批过程。'),
                self._paragraph('第六段审批过程。'),
                self._title('1.2 重大设计变更及审批过程'),
                self._paragraph('变更审批内容。'),
            ],
        ]
        (self.artifact_dir / 'content_list_v2.json').write_text(json.dumps(content, ensure_ascii=False), encoding='utf-8')

        config = get_config().model_copy(deep=True)
        config.llm.enabled = False
        service = ReportPipelineService(
            config=config,
            outline_planner=StubOutlinePlanner(
                [
                    {
                        'title_id': 'p001-b001',
                        'role': 'chapter',
                        'section_kind': 'chapter',
                        'hierarchy_level': 1,
                        'is_structural': True,
                        'ref': '1',
                    },
                    {
                        'title_id': 'p001-b002',
                        'role': 'section',
                        'section_kind': 'section',
                        'hierarchy_level': 2,
                        'is_structural': True,
                        'ref': '1.1',
                    },
                    {
                        'title_id': 'p002-b004',
                        'role': 'section',
                        'section_kind': 'section',
                        'hierarchy_level': 2,
                        'is_structural': True,
                        'ref': '1.2',
                    },
                ]
            ),
        )
        output = service.run(self.artifact_dir, 'report-doc')

        design_section = next(section for section in output.sections if section['title'] == '1.1 工程设计及审批过程')
        design_units = [unit for unit in output.report_units if unit['parent_section_uid'] == design_section['section_uid']]

        self.assertEqual(len(design_units), 1)
        self.assertEqual(design_units[0]['source_page_span'], [1, 2])
        self.assertEqual(
            design_units[0]['source_block_ids'],
            ['p001-b003', 'p001-b004', 'p001-b005', 'p002-b001', 'p002-b002', 'p002-b003'],
        )
        self.assertIn('第六段审批过程。', design_units[0]['text_normalized'])

    def test_unplanned_titles_are_kept_in_current_unit_instead_of_heuristic_sections(self) -> None:
        content = [
            [
                self._title('目录'),
                self._title('1 基本情况 …… 1'),
                self._list(['1.1 工程概况 …… 1', '1.2 工程设计及建设过程 ..... 2']),
                self._title('2 现场安全检查及安全检测 …… 12'),
                self._list(['2.1 现场安全检查内容 ..... 12']),
            ]
        ]
        (self.artifact_dir / 'content_list_v2.json').write_text(json.dumps(content, ensure_ascii=False), encoding='utf-8')

        config = get_config().model_copy(deep=True)
        config.llm.enabled = False
        service = ReportPipelineService(
            config=config,
            outline_planner=StubOutlinePlanner(
                [
                    {
                        'title_id': 'p001-b001',
                        'role': 'toc',
                        'section_kind': 'toc',
                        'hierarchy_level': 1,
                        'is_structural': False,
                        'ref': None,
                    }
                ]
            ),
        )
        output = service.run(self.artifact_dir, 'report-doc')

        self.assertEqual([section['title'] for section in output.sections], ['目录'])
        self.assertEqual(len(output.report_units), 2)
        self.assertEqual(output.report_units[0]['source_block_ids'], ['p001-b002', 'p001-b003-i001', 'p001-b003-i002'])
        self.assertIn('1 基本情况 …… 1', output.report_units[0]['text_normalized'])
        self.assertIn('1.2 工程设计及建设过程 ..... 2', output.report_units[0]['text_normalized'])

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
    def _list(items: list[str]) -> dict:
        return {
            'type': 'list',
            'content': {
                'list_items': [
                    {'item_content': [{'type': 'text', 'content': item}]}
                    for item in items
                ]
            },
            'bbox': [0, 0, 10, 10],
        }

    @staticmethod
    def _paragraph_rich(parts: list[dict]) -> dict:
        return {
            'type': 'paragraph',
            'content': {'paragraph_content': parts},
            'bbox': [0, 0, 10, 10],
        }

    @staticmethod
    def _equation(latex: str) -> dict:
        return {
            'type': 'equation_interline',
            'content': {
                'math_content': latex,
                'math_type': 'latex',
                'image_source': {'path': 'images/equation.jpg'},
            },
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

    @staticmethod
    def _default_title_plan_items() -> list[dict]:
        return [
            {
                'title_id': 'p001-b001',
                'role': 'front_matter',
                'section_kind': 'front_matter',
                'hierarchy_level': 1,
                'is_structural': False,
                'ref': None,
            },
            {
                'title_id': 'p002-b001',
                'role': 'chapter',
                'section_kind': 'chapter',
                'hierarchy_level': 1,
                'is_structural': True,
                'ref': '1',
            },
            {
                'title_id': 'p002-b002',
                'role': 'section',
                'section_kind': 'section',
                'hierarchy_level': 2,
                'is_structural': True,
                'ref': '1.1',
            },
            {
                'title_id': 'p002-b004',
                'role': 'topic',
                'section_kind': 'topic',
                'hierarchy_level': 4,
                'is_structural': False,
                'ref': '1',
            },
            {
                'title_id': 'p002-b006',
                'role': 'topic',
                'section_kind': 'topic',
                'hierarchy_level': 4,
                'is_structural': False,
                'ref': '2',
            },
            {
                'title_id': 'p002-b008',
                'role': 'chapter',
                'section_kind': 'chapter',
                'hierarchy_level': 1,
                'is_structural': True,
                'ref': '2',
            },
            {
                'title_id': 'p002-b010',
                'role': 'section',
                'section_kind': 'section',
                'hierarchy_level': 2,
                'is_structural': True,
                'ref': '2.1',
            },
        ]


if __name__ == '__main__':
    unittest.main()
