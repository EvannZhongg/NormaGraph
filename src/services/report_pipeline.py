from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html import unescape
from pathlib import Path
import hashlib
import json
import re
from typing import Any

from adapters.llm_client import ResponsesAPIClient
from core.config import AppConfig, get_config
from services.report_outline_planner import ReportOutlinePlannerService


CHINESE_SPACED_RE = re.compile(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])')
MULTI_SPACE_RE = re.compile(r'[ \t\r\f\v]+')
DOTTED_TITLE_RE = re.compile(r'^(?P<ref>\d+(?:\.\d+)+)\s*(?P<title>.*)$')
SINGLE_NUMBER_TITLE_RE = re.compile(r'^(?P<num>\d+)\s*(?P<title>\S.*)?$')
ENUMERATED_TITLE_RE = re.compile(r'^(?P<marker>(?:\d+[）\)]|\d+[、.]))\s*(?P<title>.+)$')
CHINESE_ENUMERATED_TITLE_RE = re.compile(r'^(?P<marker>[一二三四五六七八九十]+[、.])\s*(?P<title>.+)$')
APPENDIX_TITLE_RE = re.compile(r'^(?P<label>附录|附件|附表|附图)\s*(?P<ref>[A-Za-z0-9一二三四五六七八九十]+)?\s*(?P<title>.*)$')
TABLE_REF_RE = re.compile(r'(?:表\s*)?(?P<ref>(?:[A-Z])?\d+(?:\.\d+)*(?:-\d+)*)', re.IGNORECASE)
FIGURE_REF_RE = re.compile(r'(?P<label>图|照片|附图)\s*(?P<ref>[A-Za-z0-9+\-.]+)?')

TEXTUAL_BLOCK_TYPES = {'paragraph', 'equation'}


@dataclass
class ReportPipelineOutput:
    normalized_blocks: list[dict[str, Any]]
    title_inventory: list[dict[str, Any]]
    title_plan: list[dict[str, Any]]
    sections: list[dict[str, Any]]
    report_units: list[dict[str, Any]]
    tables: list[dict[str, Any]]
    figures: list[dict[str, Any]]
    report_nodes: list[dict[str, Any]] = field(default_factory=list)
    report_edges: list[dict[str, Any]] = field(default_factory=list)
    embedding_documents: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    report_markdown: str = ''


class ReportPipelineService:
    def __init__(
        self,
        config: AppConfig | None = None,
        outline_planner: ReportOutlinePlannerService | None = None,
    ) -> None:
        self.config = config or get_config()
        self.outline_planner = outline_planner or ReportOutlinePlannerService(
            self.config,
            ResponsesAPIClient(self.config),
        )

    def run(self, artifact_dir: Path, document_id: str) -> ReportPipelineOutput:
        content_list_path = artifact_dir / 'content_list_v2.json'
        if not content_list_path.exists():
            raise FileNotFoundError(f'content_list_v2.json was not found in {artifact_dir}')

        data = json.loads(content_list_path.read_text(encoding='utf-8'))
        normalized_blocks = self._flatten_content_list(data)
        page_roles = self._detect_page_roles(normalized_blocks)
        for block in normalized_blocks:
            block['page_role'] = page_roles.get(block['page_idx'], 'body')

        title_inventory = self._build_title_inventory(normalized_blocks)
        title_plan, title_plan_by_block_id, title_plan_warnings, title_plan_metrics = self._resolve_title_plan(
            document_id=document_id,
            title_inventory=title_inventory,
        )
        sections, report_units, tables, figures, metrics = self._build_report_structure(
            normalized_blocks=normalized_blocks,
            document_id=document_id,
            title_plan_by_block_id=title_plan_by_block_id,
        )
        metrics['title_count'] = len(title_inventory)
        metrics['title_plan_count'] = len(title_plan)
        metrics.update(title_plan_metrics)
        metrics['title_plan_warning_count'] = len(title_plan_warnings)
        if title_plan_warnings:
            metrics['title_plan_warnings'] = title_plan_warnings
        report_nodes, report_edges, embedding_documents = self._materialize_report_graph(
            document_id=document_id,
            sections=sections,
            report_units=report_units,
            tables=tables,
            figures=figures,
        )
        metrics['report_node_count'] = len(report_nodes)
        metrics['report_edge_count'] = len(report_edges)
        metrics['embedding_document_count'] = len(embedding_documents)
        report_markdown = self._build_report(
            artifact_dir=artifact_dir,
            document_id=document_id,
            metrics=metrics,
            sections=sections,
            report_units=report_units,
            tables=tables,
            figures=figures,
        )
        return ReportPipelineOutput(
            normalized_blocks=normalized_blocks,
            title_inventory=title_inventory,
            title_plan=title_plan,
            sections=sections,
            report_units=report_units,
            tables=tables,
            figures=figures,
            report_nodes=report_nodes,
            report_edges=report_edges,
            embedding_documents=embedding_documents,
            metrics=metrics,
            report_markdown=report_markdown,
        )

    def write_outputs(
        self,
        report_space_dir: Path,
        output: ReportPipelineOutput,
        *,
        artifact_dir: Path | None = None,
        document_id: str | None = None,
        source_path: Path | None = None,
    ) -> dict[str, Path]:
        report_space_dir.mkdir(parents=True, exist_ok=True)
        files = {
            'manifest': report_space_dir / 'space_manifest.json',
            'normalized_blocks': report_space_dir / 'normalized_blocks.json',
            'title_inventory': report_space_dir / 'title_inventory.json',
            'title_plan': report_space_dir / 'title_plan.json',
            'sections': report_space_dir / 'sections.json',
            'report_units': report_space_dir / 'report_units.json',
            'tables': report_space_dir / 'tables.json',
            'figures': report_space_dir / 'figures.json',
            'report_nodes': report_space_dir / 'report_nodes.json',
            'report_edges': report_space_dir / 'report_edges.json',
            'embedding_inputs': report_space_dir / 'embedding_inputs.jsonl',
            'metrics': report_space_dir / 'segmentation_metrics.json',
            'report': report_space_dir / 'segmentation_report.md',
        }
        manifest = {
            'space_type': 'report_space',
            'document_id': document_id,
            'artifact_dir': str(artifact_dir) if artifact_dir else None,
            'report_space_dir': str(report_space_dir),
            'source_path': str(source_path) if source_path else None,
            'generated_at': datetime.now(UTC).isoformat(),
        }
        files['manifest'].write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        files['normalized_blocks'].write_text(json.dumps(output.normalized_blocks, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        files['title_inventory'].write_text(json.dumps(output.title_inventory, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        files['title_plan'].write_text(json.dumps(output.title_plan, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        files['sections'].write_text(json.dumps(output.sections, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        files['report_units'].write_text(json.dumps(output.report_units, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        files['tables'].write_text(json.dumps(output.tables, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        files['figures'].write_text(json.dumps(output.figures, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        files['report_nodes'].write_text(json.dumps(output.report_nodes, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        files['report_edges'].write_text(json.dumps(output.report_edges, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        lines = [json.dumps(item, ensure_ascii=False) for item in output.embedding_documents]
        files['embedding_inputs'].write_text(('\n'.join(lines) + ('\n' if lines else '')), encoding='utf-8')
        files['metrics'].write_text(json.dumps(output.metrics, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        files['report'].write_text(output.report_markdown, encoding='utf-8')
        return files

    def _flatten_content_list(self, pages: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for page_idx, page in enumerate(pages, start=1):
            for block_idx, item in enumerate(page, start=1):
                block_type = item.get('type', 'unknown')
                bbox = item.get('bbox') or []
                if block_type == 'title':
                    text = self._join_text_fragments(item.get('content', {}).get('title_content', []))
                    if text:
                        blocks.append(
                            self._make_block(
                                page_idx,
                                block_idx,
                                None,
                                'title',
                                text,
                                bbox,
                                item,
                                extra={'raw_title_level': item.get('content', {}).get('level')},
                            )
                        )
                elif block_type == 'paragraph':
                    text = self._join_text_fragments(item.get('content', {}).get('paragraph_content', []))
                    if text:
                        blocks.append(self._make_block(page_idx, block_idx, None, 'paragraph', text, bbox, item))
                elif block_type == 'list':
                    for item_idx, list_item in enumerate(item.get('content', {}).get('list_items', []), start=1):
                        text = self._join_text_fragments(list_item.get('item_content', []))
                        if text:
                            blocks.append(self._make_block(page_idx, block_idx, item_idx, 'list_item', text, bbox, item))
                elif block_type == 'table':
                    table_payload = self._table_to_payload(item.get('content', {}))
                    if table_payload is None:
                        continue
                    table_text = table_payload.get('text', '')
                    if table_text or table_payload.get('table_html') or table_payload.get('image_path'):
                        blocks.append(self._make_block(page_idx, block_idx, None, 'table', table_text, bbox, item, extra=table_payload))
                elif block_type == 'image':
                    image_payload = self._image_to_payload(item.get('content', {}))
                    if image_payload is None:
                        continue
                    blocks.append(self._make_block(page_idx, block_idx, None, 'image', image_payload.get('text', ''), bbox, item, extra=image_payload))
                elif block_type == 'equation_interline':
                    equation_text = self._join_rich_fragments(item.get('content'))
                    if equation_text:
                        blocks.append(self._make_block(page_idx, block_idx, None, 'equation', equation_text, bbox, item))
        return blocks

    def _build_title_inventory(self, normalized_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        title_blocks = [block for block in normalized_blocks if block['source_type'] == 'title']
        next_title_by_block_id: dict[str, str | None] = {}
        following_title_text: str | None = None
        for block in reversed(title_blocks):
            next_title_by_block_id[block['block_id']] = following_title_text
            following_title_text = block['text_normalized']

        inventory: list[dict[str, Any]] = []
        previous_title_text: str | None = None
        for title_index, block in enumerate(title_blocks, start=1):
            inventory.append(
                {
                    'title_id': block['block_id'],
                    'title_index': title_index,
                    'block_id': block['block_id'],
                    'page_idx': block['page_idx'],
                    'page_role': block.get('page_role'),
                    'text': block['text'],
                    'text_normalized': block['text_normalized'],
                    'raw_title_level': block.get('raw_title_level'),
                    'previous_title': previous_title_text,
                    'next_title': next_title_by_block_id.get(block['block_id']),
                    'preceding_text_preview': self._nearest_text_preview(normalized_blocks, block['block_id'], direction='backward'),
                    'following_text_preview': self._nearest_text_preview(normalized_blocks, block['block_id'], direction='forward'),
                    'numbering_pattern': self._title_numbering_pattern(block['text_normalized']),
                    'looks_structural': self._looks_structural_title(block['text_normalized']),
                }
            )
            previous_title_text = block['text_normalized']
        return inventory

    def _resolve_title_plan(
        self,
        *,
        document_id: str,
        title_inventory: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[str], dict[str, Any]]:
        heuristic_plan = self._build_heuristic_title_plan(title_inventory)
        plan_by_block_id = {item['title_id']: item for item in heuristic_plan}
        warnings: list[str] = []
        metrics: dict[str, Any] = {
            'title_plan_source': 'heuristic',
            'title_planner_enabled': bool(getattr(self.outline_planner, 'enabled', False)),
            'title_plan_llm_item_count': 0,
        }

        if getattr(self.outline_planner, 'enabled', False):
            try:
                planner_result = self.outline_planner.plan_titles(document_id=document_id, title_inventory=heuristic_plan)
            except Exception as exc:  # pragma: no cover - defensive fallback
                warnings.append(f'planner_error: {exc}')
                planner_result = None
            if planner_result is not None:
                warnings.extend(planner_result.warnings)
                metrics.update(planner_result.metrics)
                llm_item_count = 0
                for item in planner_result.items:
                    title_id = item.get('title_id')
                    if not title_id or title_id not in plan_by_block_id:
                        continue
                    base_item = plan_by_block_id[title_id]
                    merged_item = {**base_item, **item}
                    for key in ('ref', 'confidence', 'rationale'):
                        if item.get(key) is None:
                            merged_item[key] = base_item.get(key)
                    merged_item['planner_source'] = item.get('planner_source') or 'llm'
                    plan_by_block_id[title_id] = merged_item
                    llm_item_count += 1
                metrics['title_plan_llm_item_count'] = llm_item_count
                if llm_item_count >= len(title_inventory) and title_inventory:
                    metrics['title_plan_source'] = 'llm'
                elif llm_item_count > 0:
                    metrics['title_plan_source'] = 'hybrid'

        plan = [plan_by_block_id[item['title_id']] for item in title_inventory if item['title_id'] in plan_by_block_id]
        return plan, plan_by_block_id, warnings, metrics

    def _build_heuristic_title_plan(self, title_inventory: list[dict[str, Any]]) -> list[dict[str, Any]]:
        title_plan: list[dict[str, Any]] = []
        title_stack: list[dict[str, Any]] = []
        first_structural_seen = False
        current_chapter_num: int | None = None

        for title in title_inventory:
            normalized = title['text_normalized']
            if title.get('page_role') == 'toc':
                if normalized in {'目录', '目 录'}:
                    spec = {'section_kind': 'toc', 'hierarchy_level': 1, 'ref': None, 'is_structural': False}
                else:
                    spec = {'section_kind': 'ignore', 'hierarchy_level': 4, 'ref': None, 'is_structural': False}
            else:
                spec = self._classify_title(
                    title['text'],
                    first_structural_seen=first_structural_seen,
                    current_chapter_num=current_chapter_num,
                    current_structural_depth=max(
                        (item['hierarchy_level'] for item in title_stack if item.get('is_structural')),
                        default=0,
                    ),
                    current_topic_ref=next(
                        (item.get('ref') for item in reversed(title_stack) if not item.get('is_structural') and item.get('ref')),
                        None,
                    ),
                    next_title_text=title.get('next_title'),
                )

            plan_item = {
                **title,
                **spec,
                'role': spec['section_kind'],
                'planner_source': 'heuristic',
                'confidence': None,
                'rationale': None,
                'heuristic_section_kind': spec['section_kind'],
                'heuristic_hierarchy_level': spec['hierarchy_level'],
                'heuristic_is_structural': spec['is_structural'],
                'heuristic_ref': spec.get('ref'),
            }
            title_plan.append(plan_item)

            if spec['section_kind'] == 'ignore':
                continue

            self._trim_hierarchy_stack(title_stack, spec['hierarchy_level'])
            title_stack.append(spec)
            if spec['is_structural']:
                first_structural_seen = True
                if spec['section_kind'] == 'chapter' and spec.get('ref') and str(spec['ref']).isdigit():
                    current_chapter_num = int(str(spec['ref']))

        return title_plan

    def _build_report_structure(
        self,
        *,
        normalized_blocks: list[dict[str, Any]],
        document_id: str,
        title_plan_by_block_id: dict[str, dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        sections: list[dict[str, Any]] = []
        report_units: list[dict[str, Any]] = []
        tables: list[dict[str, Any]] = []
        figures: list[dict[str, Any]] = []
        metrics = {
            'normalized_block_count': len(normalized_blocks),
            'page_count': max((block['page_idx'] for block in normalized_blocks), default=0),
            'front_matter_page_count': len({block['page_idx'] for block in normalized_blocks if block.get('page_role') == 'front_matter'}),
            'toc_page_count': len({block['page_idx'] for block in normalized_blocks if block.get('page_role') == 'toc'}),
            'body_page_count': len({block['page_idx'] for block in normalized_blocks if block.get('page_role') == 'body'}),
            'section_count': 0,
            'section_kind_counts': {},
            'report_unit_count': 0,
            'table_count': 0,
            'figure_count': 0,
            'front_matter_unit_count': 0,
        }

        section_map: dict[str, dict[str, Any]] = {}
        section_stack: list[dict[str, Any]] = []
        text_buffer: list[dict[str, Any]] = []
        order_index = 0

        def next_order() -> int:
            nonlocal order_index
            order_index += 1
            return order_index

        def active_section() -> dict[str, Any]:
            if section_stack:
                return section_stack[-1]
            return self._open_section(
                sections=sections,
                section_map=section_map,
                section_stack=section_stack,
                document_id=document_id,
                spec={
                    'section_kind': 'front_matter',
                    'hierarchy_level': 1,
                    'ref': None,
                    'is_structural': False,
                },
                title='前置内容',
                block={
                    'page_idx': 1,
                    'block_id': 'auto-front-matter',
                    'page_role': 'front_matter',
                },
                order_index=next_order(),
            )

        def register_member(member_uid: str, text: str | None, block: dict[str, Any]) -> None:
            if not section_stack:
                active_section()
            for section in section_stack:
                section['member_uids'].append(member_uid)
                section['content_block_count'] += 1
                if not section.get('content_page_span'):
                    section['content_page_span'] = [block['page_idx'], block['page_idx']]
                else:
                    section['content_page_span'][0] = min(section['content_page_span'][0], block['page_idx'])
                    section['content_page_span'][1] = max(section['content_page_span'][1], block['page_idx'])
                if not section.get('last_content_block_id'):
                    section['first_content_block_id'] = block['block_id']
                section['last_content_block_id'] = block['block_id']
                preview = section['content_preview']
                candidate = self._normalize_text(text or '')
                if candidate and len(preview) < 500:
                    preview = f'{preview}\n{candidate}'.strip() if preview else candidate
                    section['content_preview'] = preview[:500]

        def flush_text_unit() -> None:
            if not text_buffer:
                return
            parent = active_section()
            raw_text = '\n'.join(block['text'].strip() for block in text_buffer if block.get('text')).strip()
            normalized_text = '\n'.join(block['text_normalized'] for block in text_buffer if block.get('text_normalized')).strip()
            if not normalized_text:
                text_buffer.clear()
                return
            unit_uid = f'report:{document_id}:unit:{len(report_units) + 1}'
            section_path = [section['title'] for section in section_stack]
            structural_path = [section['title'] for section in section_stack if section.get('is_structural')]
            local_heading_path = [section['title'] for section in section_stack if section.get('hierarchy_level', 0) >= 4]
            page_values = [block['page_idx'] for block in text_buffer]
            unit = {
                'unit_uid': unit_uid,
                'document_id': document_id,
                'parent_section_uid': parent['section_uid'],
                'unit_type': 'text' if len(text_buffer) > 1 or text_buffer[0]['source_type'] in TEXTUAL_BLOCK_TYPES else text_buffer[0]['source_type'],
                'section_path': section_path,
                'structural_path': structural_path,
                'local_heading_path': local_heading_path,
                'text': raw_text,
                'text_normalized': normalized_text,
                'source_block_ids': [block['block_id'] for block in text_buffer],
                'source_page_span': [min(page_values), max(page_values)],
                'source_bboxes': [block.get('bbox') or [] for block in text_buffer],
                'order_index': next_order(),
                'page_role': text_buffer[0].get('page_role'),
            }
            report_units.append(unit)
            register_member(unit_uid, normalized_text, text_buffer[-1])
            text_buffer.clear()

        for block in normalized_blocks:
            source_type = block['source_type']

            if source_type == 'title':
                flush_text_unit()
                spec = title_plan_by_block_id.get(block['block_id'])
                if spec is None:
                    continue
                if spec['section_kind'] == 'ignore':
                    continue

                self._open_section(
                    sections=sections,
                    section_map=section_map,
                    section_stack=section_stack,
                    document_id=document_id,
                    spec=spec,
                    title=block['text'],
                    block=block,
                    order_index=next_order(),
                    plan_entry=spec,
                )
                continue

            if block.get('page_role') == 'toc':
                continue

            if source_type in TEXTUAL_BLOCK_TYPES:
                text_buffer.append(block)
                if sum(len(item['text_normalized']) for item in text_buffer) >= 900 or len(text_buffer) >= 4:
                    flush_text_unit()
                continue

            if source_type == 'list_item':
                flush_text_unit()
                text_buffer.append(block)
                flush_text_unit()
                continue

            if source_type == 'table':
                flush_text_unit()
                parent = active_section()
                section_path = [section['title'] for section in section_stack]
                structural_path = [section['title'] for section in section_stack if section.get('is_structural')]
                table_uid = f'report:{document_id}:table:{len(tables) + 1}'
                table = {
                    'table_uid': table_uid,
                    'document_id': document_id,
                    'parent_section_uid': parent['section_uid'],
                    'section_path': section_path,
                    'structural_path': structural_path,
                    'table_ref': block.get('table_ref'),
                    'table_caption': block.get('table_caption'),
                    'table_title': block.get('table_title') or block.get('table_caption'),
                    'table_html': block.get('table_html'),
                    'table_text': block.get('text_normalized') or block.get('text'),
                    'image_path': block.get('image_path'),
                    'source_block_id': block['block_id'],
                    'source_page_idx': block['page_idx'],
                    'source_bbox': block.get('bbox') or [],
                    'order_index': next_order(),
                    'page_role': block.get('page_role'),
                }
                tables.append(table)
                register_member(table_uid, table.get('table_text') or table.get('table_caption'), block)
                continue

            if source_type == 'image':
                flush_text_unit()
                figure_text = block.get('text_normalized') or block.get('text')
                if not figure_text and not block.get('image_path'):
                    continue
                parent = active_section()
                section_path = [section['title'] for section in section_stack]
                structural_path = [section['title'] for section in section_stack if section.get('is_structural')]
                figure_uid = f'report:{document_id}:figure:{len(figures) + 1}'
                figure = {
                    'figure_uid': figure_uid,
                    'document_id': document_id,
                    'parent_section_uid': parent['section_uid'],
                    'section_path': section_path,
                    'structural_path': structural_path,
                    'figure_ref': block.get('figure_ref'),
                    'figure_caption': block.get('figure_caption'),
                    'figure_footnote': block.get('figure_footnote'),
                    'figure_text': figure_text,
                    'image_path': block.get('image_path'),
                    'source_block_id': block['block_id'],
                    'source_page_idx': block['page_idx'],
                    'source_bbox': block.get('bbox') or [],
                    'order_index': next_order(),
                    'page_role': block.get('page_role'),
                }
                figures.append(figure)
                register_member(figure_uid, figure_text, block)

        flush_text_unit()

        section_kind_counts: dict[str, int] = defaultdict(int)
        for section in sections:
            section['page_span'] = section.get('content_page_span') or [section['source_page_idx'], section['source_page_idx']]
            section['member_count'] = len(section.get('member_uids', []))
            section.pop('content_page_span', None)
            section_kind_counts[section['section_kind']] += 1

        metrics['section_count'] = len(sections)
        metrics['section_kind_counts'] = dict(sorted(section_kind_counts.items(), key=lambda item: item[0]))
        metrics['report_unit_count'] = len(report_units)
        metrics['table_count'] = len(tables)
        metrics['figure_count'] = len(figures)
        metrics['front_matter_unit_count'] = sum(1 for unit in report_units if unit.get('page_role') == 'front_matter')
        return sections, report_units, tables, figures, metrics

    def _materialize_report_graph(
        self,
        *,
        document_id: str,
        sections: list[dict[str, Any]],
        report_units: list[dict[str, Any]],
        tables: list[dict[str, Any]],
        figures: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        report_uid = f'report:{document_id}'
        report_nodes: list[dict[str, Any]] = []
        report_edges: list[dict[str, Any]] = []
        embedding_documents: list[dict[str, Any]] = []
        children_by_parent: dict[str, list[tuple[int, str]]] = defaultdict(list)
        seen_nodes: set[str] = set()
        seen_edges: set[str] = set()

        def add_node(node: dict[str, Any], *, embedding_text: str | None = None) -> None:
            node_uid = node['node_uid']
            if node_uid in seen_nodes:
                return
            seen_nodes.add(node_uid)
            report_nodes.append(node)
            text = self._normalize_text(embedding_text or node.get('text_content') or '')
            if text:
                embedding_documents.append(
                    {
                        'node_uid': node_uid,
                        'document_id': document_id,
                        'node_type': node['node_type'],
                        'text': text,
                    }
                )

        def add_edge(edge_type: str, source_uid: str, target_uid: str, properties: dict[str, Any] | None = None) -> None:
            edge_uid = self._edge_uid(edge_type, source_uid, target_uid)
            if edge_uid in seen_edges:
                return
            seen_edges.add(edge_uid)
            report_edges.append(
                {
                    'edge_uid': edge_uid,
                    'document_id': document_id,
                    'edge_type': edge_type,
                    'source_uid': source_uid,
                    'target_uid': target_uid,
                    'properties': properties or {},
                }
            )

        add_node(
            {
                'node_uid': report_uid,
                'document_id': document_id,
                'node_type': 'report',
                'label': document_id,
                'text_content': document_id,
                'properties': {'document_id': document_id},
            }
        )

        for section in sections:
            text_content = '\n'.join(
                part for part in [' > '.join(section.get('structural_path', [])), section.get('title'), section.get('content_preview')] if part
            ).strip()
            add_node(
                {
                    'node_uid': section['section_uid'],
                    'document_id': document_id,
                    'node_type': 'report_section',
                    'label': section['title'],
                    'text_content': text_content or section['title'],
                    'properties': section,
                }
            )
            parent_uid = section.get('parent_section_uid') or report_uid
            add_edge('CONTAINS', parent_uid, section['section_uid'])
            children_by_parent[parent_uid].append((section['order_index'], section['section_uid']))

        for unit in report_units:
            add_node(
                {
                    'node_uid': unit['unit_uid'],
                    'document_id': document_id,
                    'node_type': 'report_unit',
                    'label': unit['text_normalized'][:80],
                    'text_content': unit['text_normalized'],
                    'properties': unit,
                }
            )
            parent_uid = unit['parent_section_uid']
            add_edge('CONTAINS', parent_uid, unit['unit_uid'])
            children_by_parent[parent_uid].append((unit['order_index'], unit['unit_uid']))

        for table in tables:
            text_content = '\n'.join(part for part in [table.get('table_caption'), table.get('table_text')] if part).strip()
            add_node(
                {
                    'node_uid': table['table_uid'],
                    'document_id': document_id,
                    'node_type': 'report_table',
                    'label': table.get('table_caption') or table.get('table_ref') or table['table_uid'],
                    'text_content': text_content or table['table_uid'],
                    'properties': table,
                }
            )
            parent_uid = table['parent_section_uid']
            add_edge('CONTAINS', parent_uid, table['table_uid'])
            children_by_parent[parent_uid].append((table['order_index'], table['table_uid']))

        for figure in figures:
            text_content = '\n'.join(
                part for part in [figure.get('figure_caption'), figure.get('figure_footnote'), figure.get('figure_text')] if part
            ).strip()
            add_node(
                {
                    'node_uid': figure['figure_uid'],
                    'document_id': document_id,
                    'node_type': 'report_figure',
                    'label': figure.get('figure_caption') or figure.get('figure_ref') or figure['figure_uid'],
                    'text_content': text_content or figure['figure_uid'],
                    'properties': figure,
                }
            )
            parent_uid = figure['parent_section_uid']
            add_edge('CONTAINS', parent_uid, figure['figure_uid'])
            children_by_parent[parent_uid].append((figure['order_index'], figure['figure_uid']))

        for siblings in children_by_parent.values():
            ordered = [node_uid for _, node_uid in sorted(siblings, key=lambda item: item[0])]
            for left, right in zip(ordered, ordered[1:]):
                add_edge('NEXT', left, right)

        return report_nodes, report_edges, embedding_documents

    def _detect_page_roles(self, blocks: list[dict[str, Any]]) -> dict[int, str]:
        blocks_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for block in blocks:
            blocks_by_page[block['page_idx']].append(block)

        title_texts_by_page = {
            page_idx: [block['text_normalized'] for block in page_blocks if block['source_type'] == 'title']
            for page_idx, page_blocks in blocks_by_page.items()
        }
        toc_start_page = next(
            (page_idx for page_idx, titles in sorted(title_texts_by_page.items()) if any(title in {'目录', '目 录'} for title in titles)),
            None,
        )
        first_structural_page = next(
            (
                page_idx
                for page_idx in sorted(blocks_by_page)
                if any(self._looks_structural_title(block['text_normalized']) for block in blocks_by_page[page_idx] if block['source_type'] == 'title')
                and page_idx != toc_start_page
            ),
            None,
        )

        page_roles: dict[int, str] = {}
        toc_pages: set[int] = set()
        if toc_start_page is not None:
            for page_idx in sorted(blocks_by_page):
                if page_idx < toc_start_page:
                    continue
                if first_structural_page is not None and page_idx >= first_structural_page:
                    break
                toc_pages.add(page_idx)

        for page_idx in sorted(blocks_by_page):
            if page_idx in toc_pages:
                page_roles[page_idx] = 'toc'
            elif first_structural_page is not None and page_idx < first_structural_page:
                page_roles[page_idx] = 'front_matter'
            else:
                page_roles[page_idx] = 'body'
        return page_roles

    def _looks_structural_title(self, text: str) -> bool:
        normalized = self._normalize_text(text)
        if normalized in {'目录', '目 录', '内容提要', '内容摘要', '摘要'}:
            return False
        if APPENDIX_TITLE_RE.match(normalized):
            return True
        if DOTTED_TITLE_RE.match(normalized):
            return True
        return bool(SINGLE_NUMBER_TITLE_RE.match(normalized))

    def _classify_title(
        self,
        text: str,
        *,
        first_structural_seen: bool,
        current_chapter_num: int | None,
        current_structural_depth: int,
        current_topic_ref: str | None,
        next_title_text: str | None,
    ) -> dict[str, Any]:
        normalized = self._normalize_text(text)
        if normalized in {'目录', '目 录'}:
            return {'section_kind': 'toc', 'hierarchy_level': 1, 'ref': None, 'is_structural': False}
        if normalized in {'内容提要', '内容摘要', '摘要'}:
            return {'section_kind': 'front_matter', 'hierarchy_level': 1, 'ref': None, 'is_structural': False}

        appendix_match = APPENDIX_TITLE_RE.match(normalized)
        if appendix_match:
            ref = appendix_match.group('ref') or appendix_match.group('label')
            return {'section_kind': 'appendix', 'hierarchy_level': 1, 'ref': ref, 'is_structural': True}

        dotted_match = DOTTED_TITLE_RE.match(normalized)
        if dotted_match:
            ref = dotted_match.group('ref')
            segments = ref.split('.')
            hierarchy_level = 3 if len(segments) >= 3 else 2
            section_kind = 'subsection' if hierarchy_level == 3 else 'section'
            return {'section_kind': section_kind, 'hierarchy_level': hierarchy_level, 'ref': ref, 'is_structural': True}

        enumerated_match = ENUMERATED_TITLE_RE.match(normalized)
        if enumerated_match:
            return {
                'section_kind': 'subtopic',
                'hierarchy_level': 5,
                'ref': enumerated_match.group('marker'),
                'is_structural': False,
            }

        chinese_enumerated_match = CHINESE_ENUMERATED_TITLE_RE.match(normalized)
        if chinese_enumerated_match:
            return {
                'section_kind': 'topic',
                'hierarchy_level': 4,
                'ref': chinese_enumerated_match.group('marker'),
                'is_structural': False,
            }

        single_number_match = SINGLE_NUMBER_TITLE_RE.match(normalized)
        if single_number_match and not normalized.startswith('20'):
            num = int(single_number_match.group('num'))
            next_major_supported = bool(next_title_text and next_title_text.startswith(f'{num}.'))
            topic_ref_num = int(current_topic_ref) if current_topic_ref and str(current_topic_ref).isdigit() else None
            if current_structural_depth > 1 and not next_major_supported:
                if topic_ref_num is None or num <= topic_ref_num + 1:
                    return {'section_kind': 'topic', 'hierarchy_level': 4, 'ref': str(num), 'is_structural': False}
            if current_chapter_num is None or not first_structural_seen or num == current_chapter_num + 1 or next_major_supported:
                return {'section_kind': 'chapter', 'hierarchy_level': 1, 'ref': str(num), 'is_structural': True}
            return {'section_kind': 'topic', 'hierarchy_level': 4, 'ref': str(num), 'is_structural': False}

        if first_structural_seen:
            return {'section_kind': 'topic', 'hierarchy_level': 4, 'ref': None, 'is_structural': False}
        return {'section_kind': 'front_matter', 'hierarchy_level': 1, 'ref': None, 'is_structural': False}

    def _open_section(
        self,
        *,
        sections: list[dict[str, Any]],
        section_map: dict[str, dict[str, Any]],
        section_stack: list[dict[str, Any]],
        document_id: str,
        spec: dict[str, Any],
        title: str,
        block: dict[str, Any],
        order_index: int,
        plan_entry: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        hierarchy_level = spec['hierarchy_level']
        self._trim_hierarchy_stack(section_stack, hierarchy_level)

        parent_section_uid = section_stack[-1]['section_uid'] if section_stack else None
        path = [section['title'] for section in section_stack] + [title]
        structural_path = [section['title'] for section in section_stack if section.get('is_structural')]
        if spec.get('is_structural'):
            structural_path = [*structural_path, title]
        section_uid = f'report:{document_id}:section:{len(sections) + 1}'
        section = {
            'section_uid': section_uid,
            'document_id': document_id,
            'parent_section_uid': parent_section_uid,
            'title': title,
            'title_normalized': self._normalize_text(title),
            'ref': spec.get('ref'),
            'section_kind': spec['section_kind'],
            'hierarchy_level': hierarchy_level,
            'is_structural': bool(spec.get('is_structural')),
            'page_role': block.get('page_role'),
            'source_page_idx': block['page_idx'],
            'source_block_id': block['block_id'],
            'source_bbox': block.get('bbox') or [],
            'title_index': plan_entry.get('title_index') if plan_entry else None,
            'title_planner_source': plan_entry.get('planner_source') if plan_entry else None,
            'title_plan_confidence': plan_entry.get('confidence') if plan_entry else None,
            'title_plan_rationale': plan_entry.get('rationale') if plan_entry else None,
            'path': path,
            'structural_path': structural_path,
            'member_uids': [],
            'content_block_count': 0,
            'content_preview': '',
            'first_content_block_id': None,
            'last_content_block_id': None,
            'order_index': order_index,
        }
        sections.append(section)
        section_map[section_uid] = section
        section_stack.append(section)
        return section

    def _trim_hierarchy_stack(self, stack: list[dict[str, Any]], hierarchy_level: int) -> None:
        if hierarchy_level <= 3:
            while stack and stack[-1]['hierarchy_level'] >= hierarchy_level:
                stack.pop()
        elif hierarchy_level == 4:
            while stack and stack[-1]['hierarchy_level'] >= 4:
                stack.pop()
        else:
            while stack and stack[-1]['hierarchy_level'] >= 5:
                stack.pop()

    def _nearest_text_preview(
        self,
        normalized_blocks: list[dict[str, Any]],
        anchor_block_id: str,
        *,
        direction: str,
        max_chars: int = 180,
    ) -> str | None:
        block_index = next((index for index, block in enumerate(normalized_blocks) if block['block_id'] == anchor_block_id), None)
        if block_index is None:
            return None

        if direction == 'backward':
            indexes = range(block_index - 1, -1, -1)
        else:
            indexes = range(block_index + 1, len(normalized_blocks))

        previews: list[str] = []
        for index in indexes:
            block = normalized_blocks[index]
            if direction == 'forward' and block['source_type'] == 'title':
                break
            if block.get('page_role') == 'toc':
                continue
            if block['source_type'] == 'title':
                continue
            text = self._preview_text(block.get('text_normalized') or block.get('text'), max_chars=max_chars)
            if not text:
                continue
            previews.append(text)
            if direction == 'backward' or len(' '.join(previews)) >= max_chars:
                break

        if not previews:
            return None
        if direction == 'backward':
            return previews[0]
        return self._preview_text('\n'.join(previews), max_chars=max_chars)

    def _preview_text(self, text: str | None, *, max_chars: int = 180) -> str | None:
        normalized = self._normalize_text(text)
        if not normalized:
            return None
        if len(normalized) <= max_chars:
            return normalized
        return normalized[: max_chars - 3].rstrip() + '...'

    def _title_numbering_pattern(self, text: str | None) -> str:
        normalized = self._normalize_text(text)
        if normalized in {'目录', '目 录'}:
            return 'toc'
        if APPENDIX_TITLE_RE.match(normalized):
            return 'appendix'
        if DOTTED_TITLE_RE.match(normalized):
            return 'dotted'
        if ENUMERATED_TITLE_RE.match(normalized):
            return 'enumerated'
        if CHINESE_ENUMERATED_TITLE_RE.match(normalized):
            return 'chinese_enumerated'
        if SINGLE_NUMBER_TITLE_RE.match(normalized) and not normalized.startswith('20'):
            return 'single_number'
        return 'plain'

    def _make_block(
        self,
        page_idx: int,
        block_idx: int,
        item_idx: int | None,
        source_type: str,
        text: str,
        bbox: list[Any],
        raw_item: dict[str, Any],
        *,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        block_id = f'p{page_idx:03d}-b{block_idx:03d}'
        if item_idx is not None:
            block_id = f'{block_id}-i{item_idx:03d}'
        normalized_text = self._normalize_text(text)
        payload = {
            'block_id': block_id,
            'page_idx': page_idx,
            'block_idx': block_idx,
            'item_idx': item_idx,
            'source_type': source_type,
            'text': text.strip(),
            'text_normalized': normalized_text,
            'bbox': bbox or [],
            'raw_type': raw_item.get('type'),
        }
        if extra:
            payload.update(extra)
        return payload

    def _table_to_payload(self, content: dict[str, Any]) -> dict[str, Any] | None:
        caption = self._join_rich_fragments(content.get('table_caption') or [])
        footnote = self._join_rich_fragments(content.get('table_footnote') or [], separator='\n')
        html = str(content.get('html') or '').strip() or None
        text_body = self._join_rich_fragments(content.get('table_body') or [], separator='\n')
        image_path = ((content.get('image_source') or {}).get('path') or '').strip() or None
        if image_path and image_path.endswith('/'):
            image_path = None
        table_text = '\n'.join(
            part
            for part in [
                caption,
                self._table_html_to_text(html) if html else '',
                text_body,
                footnote,
            ]
            if part
        ).strip()
        if not any([caption, html, text_body, footnote, image_path]):
            return None
        ref_match = TABLE_REF_RE.search(caption or text_body or '')
        return {
            'table_ref': ref_match.group('ref') if ref_match else None,
            'table_caption': caption or None,
            'table_title': caption or None,
            'table_html': html,
            'table_footnote': footnote or None,
            'image_path': image_path,
            'text': table_text,
        }

    def _image_to_payload(self, content: dict[str, Any]) -> dict[str, Any] | None:
        caption = self._join_rich_fragments(content.get('image_caption') or [])
        footnote = self._join_rich_fragments(content.get('image_footnote') or [], separator='\n')
        image_path = ((content.get('image_source') or {}).get('path') or '').strip() or None
        if image_path and image_path.endswith('/'):
            image_path = None
        text = '\n'.join(part for part in [caption, footnote] if part).strip()
        if not any([caption, footnote, image_path]):
            return None
        ref_match = FIGURE_REF_RE.search(caption or '')
        figure_ref = ref_match.group('ref') if ref_match else None
        return {
            'figure_ref': figure_ref,
            'figure_caption': caption or None,
            'figure_footnote': footnote or None,
            'image_path': image_path,
            'text': text,
        }

    def _join_text_fragments(self, fragments: list[dict[str, Any]]) -> str:
        return ''.join(fragment.get('content', '') for fragment in fragments if fragment.get('type') == 'text').strip()

    def _join_rich_fragments(self, fragments: Any, *, separator: str = '') -> str:
        if isinstance(fragments, dict):
            return self._join_rich_fragments(fragments.get('content'), separator=separator)
        if isinstance(fragments, list):
            parts = [self._join_rich_fragments(item, separator='') for item in fragments]
            filtered = [part for part in parts if part]
            return separator.join(filtered).strip()
        if fragments is None:
            return ''
        if isinstance(fragments, str):
            return fragments.strip()
        return str(fragments).strip()

    def _normalize_text(self, text: str | None) -> str:
        value = unescape(str(text or '')).replace('\u3000', ' ').replace('\xa0', ' ')
        value = value.replace('\r\n', '\n').replace('\r', '\n')
        lines = []
        for line in value.split('\n'):
            stripped = CHINESE_SPACED_RE.sub('', line)
            stripped = MULTI_SPACE_RE.sub(' ', stripped).strip()
            if stripped:
                lines.append(stripped)
        return '\n'.join(lines)

    def _table_html_to_text(self, html: str | None) -> str:
        if not html:
            return ''
        value = unescape(html)
        value = re.sub(r'(?i)</(?:td|th)>', ' | ', value)
        value = re.sub(r'(?i)</tr>', '\n', value)
        value = re.sub(r'(?i)<br\s*/?>', '\n', value)
        value = re.sub(r'(?is)<[^>]+>', ' ', value)
        lines = []
        for line in value.split('\n'):
            stripped = MULTI_SPACE_RE.sub(' ', line).strip(' |')
            if stripped:
                lines.append(stripped)
        return '\n'.join(lines)

    def _edge_uid(self, edge_type: str, source_uid: str, target_uid: str) -> str:
        digest = hashlib.sha1(f'{edge_type}|{source_uid}|{target_uid}'.encode('utf-8')).hexdigest()[:16]
        return f'edge:{digest}'

    def _build_report(
        self,
        *,
        artifact_dir: Path,
        document_id: str,
        metrics: dict[str, Any],
        sections: list[dict[str, Any]],
        report_units: list[dict[str, Any]],
        tables: list[dict[str, Any]],
        figures: list[dict[str, Any]],
    ) -> str:
        lines = [
            f'# Report Space Summary: {document_id}',
            '',
            f'- Artifact dir: {artifact_dir}',
            f'- Normalized blocks: {metrics.get("normalized_block_count", 0)}',
            f'- Titles: {metrics.get("title_count", 0)}',
            f'- Title plan items: {metrics.get("title_plan_count", 0)}',
            f'- Title plan source: {metrics.get("title_plan_source", "heuristic")}',
            f'- Sections: {metrics.get("section_count", 0)}',
            f'- Report units: {metrics.get("report_unit_count", 0)}',
            f'- Tables: {metrics.get("table_count", 0)}',
            f'- Figures: {metrics.get("figure_count", 0)}',
            f'- Report nodes: {metrics.get("report_node_count", 0)}',
            f'- Report edges: {metrics.get("report_edge_count", 0)}',
            f'- Embedding inputs: {metrics.get("embedding_document_count", 0)}',
            '',
            '## Title Planning',
            '',
            f'- Planner enabled: {metrics.get("title_planner_enabled")}',
            f'- LLM planned titles: {metrics.get("title_plan_llm_item_count", 0)}',
            f'- Warning count: {metrics.get("title_plan_warning_count", 0)}',
            '',
            '## Section Kinds',
            '',
        ]
        for key, value in sorted((metrics.get('section_kind_counts') or {}).items()):
            lines.append(f'- {key}: {value}')
        lines.extend(['', '## Sample Sections', ''])
        for section in sections[:12]:
            lines.extend(
                [
                    f'### {section["title"]}',
                    f'- Kind: {section["section_kind"]}',
                    f'- Path: {" > ".join(section.get("path", []))}',
                    f'- Page span: {section.get("page_span")}',
                    f'- Member count: {section.get("member_count", 0)}',
                    f'- Preview: {section.get("content_preview") or "n/a"}',
                    '',
                ]
            )
        if report_units:
            lines.extend(['## Sample Units', ''])
            for unit in report_units[:8]:
                lines.append(f'- {unit["unit_uid"]} | {unit["unit_type"]} | {unit["text_normalized"][:180]}')
            lines.append('')
        if tables:
            lines.extend(['## Sample Tables', ''])
            for table in tables[:8]:
                lines.append(
                    f'- {table["table_uid"]} | {table.get("table_caption") or table.get("table_ref") or "untitled"} | page {table["source_page_idx"]}'
                )
            lines.append('')
        if figures:
            lines.extend(['## Sample Figures', ''])
            for figure in figures[:8]:
                lines.append(
                    f'- {figure["figure_uid"]} | {figure.get("figure_caption") or figure.get("figure_ref") or "untitled"} | page {figure["source_page_idx"]}'
                )
            lines.append('')
        return '\n'.join(lines).rstrip() + '\n'
