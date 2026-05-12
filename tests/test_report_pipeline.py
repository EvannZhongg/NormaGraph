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
from adapters.llm_client import ResponseAPIError, ResponseAPIRequestError
from services.report_outline_planner import ReportOutlinePlannerService, ReportTitlePlanResult


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


class StubLLMClient:
    enabled = True

    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.call_count = 0

    def create_structured_output(self, **kwargs: object) -> object:
        self.call_count += 1
        return self.payload


class FailingRequestLLMClient:
    enabled = True

    def __init__(self) -> None:
        self.call_count = 0

    def create_structured_output(self, **kwargs: object) -> object:
        self.call_count += 1
        raise ResponseAPIRequestError('LLM request failed after 2/2 attempts: timeout')


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
        self.assertEqual(sections_by_title['2 工程概况']['section_kind'], 'chapter')
        self.assertNotIn('1.1 工作基础', sections_by_title)
        self.assertNotIn('1 基础资料收集', sections_by_title)
        self.assertNotIn('2 现场安全检查', sections_by_title)
        self.assertNotIn('2.1 工程基本情况', sections_by_title)
        units_by_title = {unit['title']: unit for unit in output.report_units if unit.get('title')}
        self.assertIn('1.1 工作基础', units_by_title)
        self.assertIn('2.1 工程基本情况', units_by_title)
        self.assertEqual(units_by_title['1.1 工作基础']['parent_section_uid'], sections_by_title['1 引言']['section_uid'])
        self.assertEqual(units_by_title['2.1 工程基本情况']['parent_section_uid'], sections_by_title['2 工程概况']['section_uid'])

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
        self.assertEqual(output.metrics['title_plan_llm_item_count'], 7)
        self.assertEqual(output.metrics['title_plan_heuristic_item_count'], 0)
        self.assertEqual(len(output.title_inventory), 7)
        self.assertEqual(output.title_plan[0]['title_id'], output.title_inventory[0]['title_id'])

    def test_pipeline_can_apply_external_title_plan_overrides(self) -> None:
        config = get_config().model_copy(deep=True)
        config.llm.enabled = False
        planner = StubOutlinePlanner(
            items=[
                {
                    'title_id': 'p002-b004',
                    'role': 'unit',
                    'section_kind': 'unit',
                    'hierarchy_level': 2,
                    'is_structural': False,
                    'ref': '1',
                    'planner_source': 'llm',
                }
            ]
        )
        service = ReportPipelineService(config=config, outline_planner=planner)
        output = service.run(self.artifact_dir, 'report-doc')

        sections_by_title = {section['title']: section for section in output.sections}
        self.assertNotIn('1 基础资料收集', sections_by_title)
        self.assertEqual(output.metrics['title_plan_source'], 'llm')
        self.assertEqual(output.metrics['title_plan_llm_item_count'], 1)
        self.assertEqual(output.metrics['title_plan_heuristic_item_count'], 0)
        self.assertEqual(output.metrics['title_plan_missing_item_count'], 6)
        planned_item = next(item for item in output.title_plan if item['title_id'] == 'p002-b004')
        self.assertEqual(planned_item['planner_source'], 'llm')
        self.assertEqual(planned_item['section_kind'], 'unit')
        self.assertTrue(any(unit.get('title') == '1 基础资料收集' for unit in output.report_units))

    def test_partial_llm_title_plan_leaves_missing_titles_unplanned(self) -> None:
        content = [
            [
                self._title('1 工程概况'),
                self._paragraph('工程概况正文。'),
                self._title('1.1 建设过程'),
                self._paragraph('建设过程正文。'),
                self._title('2 现场安全检查'),
                self._paragraph('现场检查正文。'),
                self._title('2.1 检查结果'),
                self._paragraph('检查结果正文。'),
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
                        'role': 'chapter',
                        'section_kind': 'chapter',
                        'hierarchy_level': 1,
                        'is_structural': True,
                        'ref': '1',
                    }
                ]
            ),
        )
        output = service.run(self.artifact_dir, 'report-doc')

        sections_by_title = {section['title']: section for section in output.sections}

        self.assertEqual(len(output.title_inventory), 4)
        self.assertEqual(len(output.title_plan), 1)
        self.assertEqual(output.metrics['title_plan_source'], 'llm')
        self.assertEqual(output.metrics['title_plan_llm_item_count'], 1)
        self.assertEqual(output.metrics['title_plan_heuristic_item_count'], 0)
        self.assertEqual(output.metrics['title_plan_missing_item_count'], 3)
        self.assertEqual(sections_by_title['1 工程概况']['title_planner_source'], 'llm')
        self.assertNotIn('2 现场安全检查', sections_by_title)
        self.assertNotIn('1.1 建设过程', sections_by_title)
        self.assertNotIn('2.1 检查结果', sections_by_title)
        combined_text = '\n'.join(unit['text_normalized'] for unit in output.report_units)
        self.assertIn('1.1 建设过程', combined_text)
        self.assertIn('2 现场安全检查', combined_text)

    def test_title_planner_fails_fast_on_incomplete_llm_output(self) -> None:
        config = get_config().model_copy(deep=True)
        config.llm.enabled = True
        config.llm.api_key = 'test-key'
        planner = ReportOutlinePlannerService(config=config, client=StubLLMClient({'items': [], 'warnings': []}))

        titles = [
            {'title_id': 'p001-b001', 'title_index': 1, 'page_idx': 1, 'page_role': 'body', 'text': '1 工程概况'},
            {'title_id': 'p001-b002', 'title_index': 2, 'page_idx': 1, 'page_role': 'body', 'text': '1.1 基本情况'},
        ]

        with self.assertRaises(ResponseAPIError):
            planner.plan_titles('report-doc', titles)

    def test_title_planner_stops_immediately_on_request_retry_exhaustion(self) -> None:
        config = get_config().model_copy(deep=True)
        config.llm.enabled = True
        config.llm.api_key = 'test-key'
        client = FailingRequestLLMClient()
        planner = ReportOutlinePlannerService(config=config, client=client)

        titles = [
            {'title_id': f'p001-b{index:03d}', 'title_index': index, 'page_idx': 1, 'page_role': 'body', 'text': f'{index} 标题'}
            for index in range(1, 25)
        ]

        with self.assertRaises(ResponseAPIRequestError):
            planner.plan_titles('report-doc', titles)
        self.assertEqual(client.call_count, 1)

    def test_title_planner_accepts_compact_role_mapping_output(self) -> None:
        config = get_config().model_copy(deep=True)
        config.llm.enabled = True
        config.llm.api_key = 'test-key'
        planner = ReportOutlinePlannerService(
            config=config,
            client=StubLLMClient({'p001-b001': 'chapter', 'p001-b002': 'unit'}),
        )

        titles = [
            {'title_id': 'p001-b001', 'title_index': 1, 'page_idx': 1, 'page_role': 'body', 'text': '1 工程概况'},
            {'title_id': 'p001-b002', 'title_index': 2, 'page_idx': 1, 'page_role': 'body', 'text': '1.1 基本情况'},
        ]

        result = planner.plan_titles('report-doc', titles)

        self.assertEqual([item['section_kind'] for item in result.items], ['chapter', 'unit'])

    def test_title_planner_accepts_title_plan_list_output(self) -> None:
        config = get_config().model_copy(deep=True)
        config.llm.enabled = True
        config.llm.api_key = 'test-key'
        planner = ReportOutlinePlannerService(
            config=config,
            client=StubLLMClient(
                {
                    'title_plan': [
                        {'title_id': 'p001-b001', 'role': 'chapter', 'ref': '1'},
                        {'title_id': 'p001-b002', 'role': 'unit', 'ref': '1.1'},
                    ]
                }
            ),
        )

        titles = [
            {'title_id': 'p001-b001', 'title_index': 1, 'page_idx': 1, 'page_role': 'body', 'text': '1 工程概况'},
            {'title_id': 'p001-b002', 'title_index': 2, 'page_idx': 1, 'page_role': 'body', 'text': '1.1 基本情况'},
        ]

        result = planner.plan_titles('report-doc', titles)

        self.assertEqual([item['section_kind'] for item in result.items], ['chapter', 'unit'])

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
                        'role': 'chapter',
                        'section_kind': 'chapter',
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
        self.assertIn('本报告对工程安全情况进行综合评价。', output.report_units[0]['text_normalized'])

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
                        'role': 'unit',
                        'section_kind': 'unit',
                        'hierarchy_level': 2,
                        'is_structural': False,
                        'ref': '1.1',
                    },
                    {
                        'title_id': 'p002-b004',
                        'role': 'unit',
                        'section_kind': 'unit',
                        'hierarchy_level': 2,
                        'is_structural': False,
                        'ref': '1.2',
                    },
                ]
            ),
        )
        output = service.run(self.artifact_dir, 'report-doc')

        design_unit = next(unit for unit in output.report_units if unit.get('title') == '1.1 工程设计及审批过程')

        self.assertEqual(design_unit['parent_section_uid'], output.sections[0]['section_uid'])
        self.assertEqual(design_unit['source_page_span'], [1, 2])
        self.assertEqual(
            design_unit['source_block_ids'],
            ['p001-b002', 'p001-b003', 'p001-b004', 'p001-b005', 'p002-b001', 'p002-b002', 'p002-b003'],
        )
        self.assertIn('第六段审批过程。', design_unit['text_normalized'])

    def test_toc_titles_are_kept_in_current_unit_when_planner_marks_only_toc(self) -> None:
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
        self.assertEqual(len(output.report_units), 1)
        self.assertEqual(output.report_units[0]['source_block_ids'], ['p001-b002', 'p001-b003-i001', 'p001-b003-i002', 'p001-b004', 'p001-b005-i001'])
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
                'role': 'ignore',
                'section_kind': 'ignore',
                'hierarchy_level': 0,
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
                'role': 'unit',
                'section_kind': 'unit',
                'hierarchy_level': 2,
                'is_structural': False,
                'ref': '1.1',
            },
            {
                'title_id': 'p002-b004',
                'role': 'ignore',
                'section_kind': 'ignore',
                'hierarchy_level': 0,
                'is_structural': False,
                'ref': '1',
            },
            {
                'title_id': 'p002-b006',
                'role': 'ignore',
                'section_kind': 'ignore',
                'hierarchy_level': 0,
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
                'role': 'unit',
                'section_kind': 'unit',
                'hierarchy_level': 2,
                'is_structural': False,
                'ref': '2.1',
            },
        ]


if __name__ == '__main__':
    unittest.main()
