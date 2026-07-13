from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path
import json
import logging
import re
from typing import Any

from adapters.llm_client import EmbeddingsAPIClient, ResponseAPIError, ResponsesAPIClient
from core.config import AppConfig, get_config
from prompts import LLM_KG_SPACE_PROFILE_SYSTEM_PROMPT, build_kg_space_profile_prompt
from repositories.postgres_graph_store import PostgresGraphStore
from services.chapter_summary_service import ChapterSummaryService
from services.graph_materialization import GraphMaterializationService
from services.llm_extraction import LLMGraphExtractionService
from services.standard_outline_planner import StandardOutlinePlannerService


logger = logging.getLogger(__name__)

KG_SPACE_PROFILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "scope_summary": {"type": "string"},
        "keywords": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["scope_summary", "keywords"],
}

CHINESE_SPACED_RE = re.compile(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])')
MULTI_SPACE_RE = re.compile(r'\s+')
CHAPTER_TITLE_RE = re.compile(r'^(?P<ref>\d+)\s+(?P<title>.+)$')
SECTION_TITLE_RE = re.compile(r'^(?P<ref>\d+\.\d+)\s+(?P<title>.+)$')
APPENDIX_TITLE_RE = re.compile(r'^(?P<label>附录|附件|附表|附图)\s*(?P<ref>[A-ZＡ-ＺA-Za-z0-9一二三四五六七八九十]+)?\s*(?P<title>.*)$')
CLAUSE_START_RE = re.compile(r'^(?P<ref>\d+\.\d+\.\d+)\s*(?P<text>.*)$')
LIST_ITEM_RE = re.compile(r'^(?P<ref>(?:\(?\d+\)?(?=\s|$)|\d+[）]|(?:\d+\.(?!\d))))\s*(?P<text>.*)$')
PARAGRAPH_LIST_ITEM_RE = re.compile(r'^(?P<ref>(?:\d+[）]|(?:\d+\.(?!\d))))\s*(?P<text>.*)$')
STANDARD_REF_RE = re.compile(r'\b(?P<code>(?:GB/T|GB|SL|DL/T|SDJ|SLJ|CECS|JGJ/T|JGJ))\s*(?P<number>\d+(?:/\w+)?)(?:[-—](?P<year>\d{2,4}))?')
TABLE_REF_RE = re.compile(r'表\s*(?P<ref>(?:[A-Z](?:\.\s*)?)?\d+(?:\.\d+)*(?:-\d+)*)', re.IGNORECASE)
REFERENCE_STANDARD_TITLE_KEYWORDS = ('规范性引用文件', '引用标准', '引用文件')
MUST_WORDS = ('应当', '应', '必须')
SHOULD_WORDS = ('宜',)
MAY_WORDS = ('可',)
FORBIDDEN_WORDS = ('不得', '严禁', '禁止')
CONDITIONAL_PREFIXES = ('当', '若', '对', '凡', '必要时', '出现', '对于')
INHERITED_LIST_PATTERNS = ('包括下列', '包括以下', '应包括', '宜包括', '应遵守下列', '应符合下列', '如下', '应按下列')
EVIDENCE_RULES = [
    ('监测', ['监测资料', '监测报告', '监测记录']),
    ('检查', ['检查记录', '现场检查表', '检查报告']),
    ('检测', ['检测报告', '检测记录']),
    ('试验', ['试验报告', '试验记录']),
    ('勘察', ['勘察资料', '勘察报告']),
    ('计算', ['计算书', '复核计算成果']),
    ('审批', ['审批文件', '报批记录']),
    ('预案', ['预案正文', '审批或备案材料']),
    ('报告', ['专项报告', '总报告']),
]


@dataclass
class PipelineOutput:
    normalized_blocks: list[dict[str, Any]]
    title_inventory: list[dict[str, Any]]
    title_plan: list[dict[str, Any]]
    structure_nodes: list[dict[str, Any]]
    clauses: list[dict[str, Any]]
    requirements: list[dict[str, Any]]
    kg_space_profile: dict[str, Any] = field(default_factory=dict)
    graph_nodes: list[dict[str, Any]] = field(default_factory=list)
    graph_edges: list[dict[str, Any]] = field(default_factory=list)
    embedding_documents: list[dict[str, Any]] = field(default_factory=list)
    embedding_vectors: dict[str, list[float]] = field(default_factory=dict)
    extraction_warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    report_markdown: str = ''


class StandardPipelineService:
    def __init__(
        self,
        config: AppConfig | None = None,
        llm_extraction_service: LLMGraphExtractionService | None = None,
        outline_planner: StandardOutlinePlannerService | None = None,
        chapter_summary_service: ChapterSummaryService | None = None,
        graph_materialization_service: GraphMaterializationService | None = None,
        embedding_client: EmbeddingsAPIClient | None = None,
        postgres_graph_store: PostgresGraphStore | None = None,
    ) -> None:
        self.config = config or get_config()
        llm_client = ResponsesAPIClient(self.config)
        self.llm_client = llm_client
        self.llm_extraction_service = llm_extraction_service or LLMGraphExtractionService(self.config, llm_client)
        self.chapter_summary_service = chapter_summary_service or ChapterSummaryService(self.config, llm_client)
        self.outline_planner = outline_planner or StandardOutlinePlannerService(self.config, llm_client)
        self.graph_materialization_service = graph_materialization_service or GraphMaterializationService(self.config)
        self.embedding_client = embedding_client or EmbeddingsAPIClient(self.config)
        self.postgres_graph_store = postgres_graph_store or PostgresGraphStore(self.config)

    def run(self, artifact_dir: Path, standard_uid: str) -> PipelineOutput:
        content_list_path = self._resolve_content_list_path(artifact_dir)

        data = json.loads(content_list_path.read_text(encoding='utf-8'))
        normalized_blocks = self._flatten_content_list(data)
        page_roles = self._detect_page_roles(normalized_blocks)
        for block in normalized_blocks:
            block['page_role'] = page_roles.get(block['page_idx'], 'body')
        title_inventory = self._build_title_inventory(normalized_blocks)
        title_plan, title_plan_by_block_id, title_plan_warnings, title_plan_metrics = self._resolve_title_plan(
            normalized_blocks,
            standard_uid,
            title_inventory=title_inventory,
        )
        structure_nodes, clauses, metrics, structure_warnings = self._build_structure(
            normalized_blocks,
            standard_uid,
            title_plan_by_block_id=title_plan_by_block_id,
        )
        metrics['title_count'] = len(title_inventory)
        metrics['title_plan_count'] = len(title_plan)
        metrics.update(title_plan_metrics)
        metrics['title_plan_warning_count'] = len(title_plan_warnings)
        if title_plan_warnings:
            metrics['title_plan_warnings'] = title_plan_warnings
        requirements, extraction_metrics, extraction_warnings = self._extract_requirements(clauses, standard_uid)
        chapter_summary_metrics, chapter_summary_warnings = self._generate_chapter_summaries(structure_nodes, clauses, standard_uid)
        kg_space_profile = self._build_kg_space_profile(structure_nodes, standard_uid)
        extraction_warnings = [*title_plan_warnings, *structure_warnings, *extraction_warnings, *chapter_summary_warnings]
        metrics.update(extraction_metrics)
        metrics.update(chapter_summary_metrics)
        metrics['kg_space_profile_status'] = kg_space_profile.get('status') or 'completed'
        metrics['kg_space_profile_chapter_count'] = len(kg_space_profile.get('chapters') or [])
        metrics['requirement_count'] = len(requirements)
        metrics['clauses_with_requirements'] = sum(1 for clause in clauses if clause.get('requirement_count', 0) > 0)

        graph_nodes: list[dict[str, Any]] = []
        graph_edges: list[dict[str, Any]] = []
        embedding_documents: list[dict[str, Any]] = []
        embedding_vectors: dict[str, list[float]] = {}
        if self.config.knowledge_graph.materialize_graph:
            graph_result = self.graph_materialization_service.build(
                standard_uid=standard_uid,
                structure_nodes=structure_nodes,
                clauses=clauses,
                requirements=requirements,
            )
            graph_nodes = graph_result.nodes
            graph_edges = graph_result.edges
            embedding_documents = graph_result.embedding_documents
            embedding_documents.extend(self._build_kg_scope_embedding_documents(kg_space_profile, standard_uid))
            embedding_documents.extend(self._build_chapter_summary_embedding_documents(structure_nodes, standard_uid))
            metrics['graph_node_count'] = len(graph_nodes)
            metrics['graph_edge_count'] = len(graph_edges)
            metrics['embedding_document_count'] = len(embedding_documents)
            embedding_vectors = self._generate_embeddings(embedding_documents, metrics)
            self._persist_graph(graph_nodes, graph_edges, embedding_vectors, metrics)
        else:
            metrics['graph_node_count'] = 0
            metrics['graph_edge_count'] = 0
            metrics['embedding_document_count'] = 0

        report_markdown = self._build_report(artifact_dir, standard_uid, metrics, clauses, requirements, extraction_warnings)
        return PipelineOutput(
            normalized_blocks=normalized_blocks,
            title_inventory=title_inventory,
            title_plan=title_plan,
            structure_nodes=structure_nodes,
            clauses=clauses,
            requirements=requirements,
            kg_space_profile=kg_space_profile,
            graph_nodes=graph_nodes,
            graph_edges=graph_edges,
            embedding_documents=embedding_documents,
            embedding_vectors=embedding_vectors,
            extraction_warnings=extraction_warnings,
            metrics=metrics,
            report_markdown=report_markdown,
        )

    def _resolve_content_list_path(self, artifact_dir: Path) -> Path:
        content_list_path = artifact_dir / 'content_list_v2.json'
        if content_list_path.exists():
            return content_list_path
        prefixed_matches = sorted(artifact_dir.glob('*_content_list_v2.json'))
        if prefixed_matches:
            return prefixed_matches[0]
        raise FileNotFoundError(f'content_list_v2.json was not found in {artifact_dir}')

    def write_outputs(
        self,
        graph_space_dir: Path,
        output: PipelineOutput,
        *,
        artifact_dir: Path | None = None,
        standard_uid: str | None = None,
        document_id: str | None = None,
    ) -> dict[str, Path]:
        graph_space_dir.mkdir(parents=True, exist_ok=True)
        files = {
            'manifest': graph_space_dir / 'space_manifest.json',
            'normalized_blocks': graph_space_dir / 'normalized_blocks.json',
            'title_inventory': graph_space_dir / 'title_inventory.json',
            'title_plan': graph_space_dir / 'title_plan.json',
            'normalized_structure': graph_space_dir / 'normalized_structure.json',
            'clauses': graph_space_dir / 'clauses.json',
            'requirements': graph_space_dir / 'requirements.json',
            'kg_space_profile': graph_space_dir / 'kg_space_profile.json',
            'graph_nodes': graph_space_dir / 'graph_nodes.json',
            'graph_edges': graph_space_dir / 'graph_edges.json',
            'embedding_inputs': graph_space_dir / 'embedding_inputs.jsonl',
            'embedding_store': graph_space_dir / 'embedding_store.jsonl',
            'metrics': graph_space_dir / 'segmentation_metrics.json',
            'report': graph_space_dir / 'segmentation_report.md',
        }
        manifest = {
            'space_type': 'standard_graph',
            'standard_id': standard_uid,
            'document_id': document_id,
            'artifact_dir': str(artifact_dir) if artifact_dir else None,
            'graph_space_dir': str(graph_space_dir),
            'generated_at': datetime.now(UTC).isoformat(),
        }
        files['manifest'].write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        files['normalized_blocks'].write_text(json.dumps(output.normalized_blocks, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        files['title_inventory'].write_text(json.dumps(output.title_inventory, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        files['title_plan'].write_text(json.dumps(output.title_plan, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        files['normalized_structure'].write_text(json.dumps({'nodes': output.structure_nodes}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        files['clauses'].write_text(json.dumps(output.clauses, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        files['requirements'].write_text(json.dumps(output.requirements, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        files['kg_space_profile'].write_text(json.dumps(output.kg_space_profile, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        files['graph_nodes'].write_text(json.dumps(output.graph_nodes, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        files['graph_edges'].write_text(json.dumps(output.graph_edges, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        lines = [json.dumps(item, ensure_ascii=False) for item in output.embedding_documents]
        files['embedding_inputs'].write_text(('\n'.join(lines) + ('\n' if lines else '')), encoding='utf-8')

        embedding_store_records = self._build_local_embedding_store_records(output.embedding_documents, output.embedding_vectors)
        if not self.postgres_graph_store.enabled:
            if embedding_store_records:
                store_lines = [json.dumps(item, ensure_ascii=False) for item in embedding_store_records]
                files['embedding_store'].write_text(('\n'.join(store_lines) + '\n'), encoding='utf-8')
                output.metrics['local_embedding_store_status'] = 'completed'
                output.metrics['local_embedding_store_record_count'] = len(embedding_store_records)
            else:
                output.metrics['local_embedding_store_status'] = 'skipped_no_vectors'
                files.pop('embedding_store')
        else:
            output.metrics['local_embedding_store_status'] = 'skipped_postgres_enabled'
            files.pop('embedding_store')

        files['metrics'].write_text(json.dumps(output.metrics, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        files['report'].write_text(output.report_markdown, encoding='utf-8')
        return files

    def _build_local_embedding_store_records(
        self,
        embedding_documents: list[dict[str, Any]],
        embedding_vectors: dict[str, list[float]],
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for item in embedding_documents:
            node_uid = item.get('node_uid')
            if not node_uid:
                continue
            vector = embedding_vectors.get(node_uid)
            if vector is None:
                continue
            records.append(
                {
                    'node_uid': node_uid,
                    'standard_uid': item.get('standard_uid'),
                    'node_type': item.get('node_type'),
                    'text': item.get('text'),
                    'embedding_model': self.config.embedding.model,
                    'embedding_dimensions': len(vector),
                    'embedding': vector,
                }
            )
        return records

    def _build_kg_space_profile(self, structure_nodes: list[dict[str, Any]], standard_uid: str) -> dict[str, Any]:
        chapters = [
            {
                'node_uid': node.get('node_uid'),
                'ref': node.get('ref'),
                'title': node.get('title'),
                'summary': node.get('summary') or '',
                'summary_source_clause_count': node.get('summary_source_clause_count', 0),
                'summary_source_truncated': bool(node.get('summary_source_truncated')),
            }
            for node in structure_nodes
            if node.get('node_type') == 'chapter'
        ]
        scope_parts = []
        for chapter in chapters:
            heading = ' '.join(str(part).strip() for part in [chapter.get('ref'), chapter.get('title')] if str(part or '').strip())
            summary = str(chapter.get('summary') or '').strip()
            if summary:
                scope_parts.append(f'{heading}：{summary}' if heading else summary)
            elif heading:
                scope_parts.append(heading)
        scope_summary = self._normalize_text('；'.join(scope_parts))
        if len(scope_summary) > 1400:
            scope_summary = scope_summary[:1400].rstrip() + '...'
        keywords = self._extract_kg_space_keywords(chapters, scope_summary)
        status = 'completed' if scope_summary else 'empty'
        summary_source = 'local'

        prompt_chapters = [chapter for chapter in chapters if str(chapter.get('summary') or '').strip()]
        if self.llm_client.enabled and prompt_chapters:
            try:
                payload = self.llm_client.create_structured_output(
                    system_prompt=LLM_KG_SPACE_PROFILE_SYSTEM_PROMPT,
                    user_prompt=build_kg_space_profile_prompt(standard_uid, prompt_chapters),
                    schema_name='kg_space_profile',
                    schema=KG_SPACE_PROFILE_SCHEMA,
                )
                llm_scope_summary = self._normalize_text(str(payload.get('scope_summary') or ''))
                if llm_scope_summary:
                    scope_summary = llm_scope_summary
                    keywords = self._dedupe_strings(payload.get('keywords') or keywords)[:24]
                    status = 'completed'
                    summary_source = 'llm'
            except ResponseAPIError as exc:
                logger.warning('KG space profile generation failed for %s; using local fallback: %s', standard_uid, exc)

        return {
            'profile_uid': f'{standard_uid}:scope_summary',
            'standard_id': standard_uid,
            'status': status,
            'summary_source': summary_source,
            'scope_summary': scope_summary,
            'keywords': keywords,
            'chapters': chapters,
            'generated_at': datetime.now(UTC).isoformat(),
        }

    def _build_kg_scope_embedding_documents(self, profile: dict[str, Any], standard_uid: str) -> list[dict[str, Any]]:
        scope_summary = str(profile.get('scope_summary') or '').strip()
        if not scope_summary:
            return []
        return [
            {
                'node_uid': profile.get('profile_uid') or f'{standard_uid}:scope_summary',
                'standard_uid': standard_uid,
                'node_type': 'kg_scope_summary',
                'text': scope_summary,
            }
        ]

    def _build_chapter_summary_embedding_documents(
        self,
        structure_nodes: list[dict[str, Any]],
        standard_uid: str,
    ) -> list[dict[str, Any]]:
        documents: list[dict[str, Any]] = []
        for node in structure_nodes:
            if node.get('node_type') != 'chapter':
                continue
            node_uid = str(node.get('node_uid') or '').strip()
            summary = str(node.get('summary') or '').strip()
            if not node_uid or not summary:
                continue
            heading = ' '.join(
                str(part).strip()
                for part in [node.get('ref'), node.get('title')]
                if str(part or '').strip()
            )
            text = '\n'.join(part for part in [heading, summary] if part).strip()
            documents.append(
                {
                    'node_uid': f'{node_uid}#summary',
                    'source_node_uid': node_uid,
                    'standard_uid': standard_uid,
                    'node_type': 'chapter_summary',
                    'text': text,
                }
            )
        return documents

    def _extract_kg_space_keywords(self, chapters: list[dict[str, Any]], scope_summary: str) -> list[str]:
        values = [chapter.get('title') for chapter in chapters]
        for keyword in [
            '大坝',
            '水库',
            '安全鉴定',
            '基础资料',
            '现场检查',
            '安全检测',
            '监测',
            '防洪',
            '渗流',
            '结构安全',
            '抗震',
            '金属结构',
            '运行管理',
            '评价报告',
        ]:
            if keyword in scope_summary:
                values.append(keyword)
        return self._dedupe_strings(values)[:24]

    def _flatten_content_list(self, pages: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for page_idx, page in enumerate(pages, start=1):
            for block_idx, item in enumerate(page, start=1):
                block_type = item.get('type', 'unknown')
                bbox = item.get('bbox') or []
                if block_type == 'title':
                    text = self._join_rich_fragments(item.get('content', {}).get('title_content', []))
                    blocks.append(
                        self._make_block(
                            page_idx,
                            block_idx,
                            None,
                            'title',
                            text,
                            bbox,
                            item,
                            extra={'raw_title_level': (item.get('content') or {}).get('level')},
                        )
                    )
                elif block_type == 'paragraph':
                    text = self._join_rich_fragments(item.get('content', {}).get('paragraph_content', []))
                    blocks.append(self._make_block(page_idx, block_idx, None, 'paragraph', text, bbox, item))
                elif block_type == 'list':
                    for item_idx, list_item in enumerate(item.get('content', {}).get('list_items', []), start=1):
                        text = self._join_rich_fragments(list_item.get('item_content', []))
                        blocks.append(self._make_block(page_idx, block_idx, item_idx, 'list_item', text, bbox, item))
                elif block_type == 'table':
                    table_payload = self._table_to_payload(item.get('content', {}))
                    text = table_payload.get('text', '')
                    if text:
                        blocks.append(self._make_block(page_idx, block_idx, None, 'table', text, bbox, item, extra=table_payload))
                elif block_type in {'equation', 'equation_interline', 'interline_equation'}:
                    text = self._join_rich_fragments(item.get('content', {}))
                    if text:
                        blocks.append(self._make_block(page_idx, block_idx, None, 'equation', text, bbox, item))
        return blocks

    def _build_structure(
        self,
        blocks: list[dict[str, Any]],
        standard_uid: str,
        *,
        title_plan_by_block_id: dict[str, dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], list[str]]:
        nodes: list[dict[str, Any]] = []
        clauses: list[dict[str, Any]] = []
        warnings: list[str] = []
        metrics = {
            'normalized_block_count': len(blocks),
            'structure_node_count': 0,
            'clause_count': 0,
            'main_clause_count': 0,
            'appendix_clause_count': 0,
            'table_count': 0,
            'rejected_title_count': 0,
            'orphan_text_block_count': 0,
            'continuation_block_count': 0,
            'title_plan_role_counts': {},
            'duplicate_clause_refs': [],
        }
        title_counter: Counter[str] = Counter()
        current_body_kind = 'front_matter'
        current_appendix: dict[str, Any] | None = None
        current_chapter: dict[str, Any] | None = None
        current_section: dict[str, Any] | None = None
        current_clause: dict[str, Any] | None = None
        appendix_title_seen = False

        def finalize_clause() -> None:
            nonlocal current_clause
            if current_clause is None:
                return
            current_clause['source_text'] = '\n'.join(current_clause.pop('_text_parts'))
            current_clause['source_text_normalized'] = '\n'.join(current_clause.pop('_normalized_parts'))
            pages = current_clause.pop('_pages')
            current_clause['source_page_span'] = [min(pages), max(pages)]
            current_clause['source_bbox'] = current_clause.pop('_bboxes')
            current_clause['segmentation_confidence'] = self._score_clause_segmentation(current_clause)
            current_clause['heading_path'] = [
                title
                for title in [
                    current_appendix['title'] if current_appendix else None,
                    current_chapter['title'] if current_chapter else None,
                    current_section['title'] if current_section else None,
                ]
                if title
            ]
            current_clause['table_count'] = len(current_clause.get('tables', []))
            current_clause['requirement_count'] = 0
            current_clause['concepts'] = []
            clauses.append(current_clause)
            current_clause = None

        for index, block in enumerate(blocks):
            prev_block = blocks[index - 1] if index > 0 else None
            next_block = blocks[index + 1] if index + 1 < len(blocks) else None
            title_plan = title_plan_by_block_id.get(block['block_id']) if block['source_type'] == 'title' else None
            title_info = self._title_plan_to_structure_info(title_plan, block) if title_plan else None
            should_accept = bool(title_info)
            if title_info and title_plan and title_plan.get('planner_source') != 'llm':
                should_accept = self._should_accept_title_candidate(
                    title_info=title_info,
                    block=block,
                    current_clause=current_clause,
                    current_chapter=current_chapter,
                    current_section=current_section,
                    prev_block=prev_block,
                    next_block=next_block,
                )
            if title_info and should_accept:
                finalize_clause()
                title_counter[title_info['node_type']] += 1
                if title_info['node_type'] == 'toc':
                    nodes.append(self._make_structure_node(standard_uid, title_info, block, parent_uid=None, title_plan=title_plan))
                    continue
                if title_info['node_type'] == 'appendix':
                    current_body_kind = 'appendix'
                    appendix_title_seen = True
                    current_appendix = self._make_structure_node(standard_uid, title_info, block, parent_uid=None, title_plan=title_plan)
                    current_chapter = None
                    current_section = None
                    nodes.append(current_appendix)
                    continue
                if title_info['node_type'] in {'chapter', 'reference_standard'}:
                    if not appendix_title_seen:
                        current_body_kind = 'main'
                    current_chapter = self._make_structure_node(
                        standard_uid,
                        title_info,
                        block,
                        parent_uid=current_appendix['node_uid'] if current_body_kind == 'appendix' and current_appendix else None,
                        title_plan=title_plan,
                    )
                    current_section = None
                    nodes.append(current_chapter)
                    continue
                if title_info['node_type'] == 'section':
                    current_section = self._make_structure_node(
                        standard_uid,
                        title_info,
                        block,
                        parent_uid=current_chapter['node_uid'] if current_chapter else (current_appendix['node_uid'] if current_appendix else None),
                        title_plan=title_plan,
                    )
                    nodes.append(current_section)
                    continue
            if title_info:
                metrics['rejected_title_count'] += 1

            clause_match = self._extract_clause_match(block, title_plan)
            if clause_match:
                finalize_clause()
                clause_ref, clause_text = clause_match
                current_clause = {
                    'clause_uid': self._make_clause_uid(standard_uid, current_body_kind, current_appendix, clause_ref),
                    'standard_uid': standard_uid,
                    'body_kind': current_body_kind,
                    'appendix_ref': current_appendix['ref'] if current_body_kind == 'appendix' and current_appendix else None,
                    'chapter_ref': current_chapter['ref'] if current_chapter else None,
                    'section_ref': current_section['ref'] if current_section else None,
                    'clause_ref': clause_ref,
                    'parent_uid': current_section['node_uid'] if current_section else (current_chapter['node_uid'] if current_chapter else None),
                    'source_block_ids': [block['block_id']],
                    '_text_parts': [block['text']],
                    '_normalized_parts': [clause_text],
                    '_pages': {block['page_idx']},
                    '_bboxes': [block['bbox']],
                    'list_items': [],
                    'tables': [],
                    'notes': ['title_plan_clause'] if title_plan and title_plan.get('role') == 'clause' else [],
                    'title_index': title_plan.get('title_index') if title_plan else None,
                    'title_planner_source': title_plan.get('planner_source') if title_plan else None,
                    'title_plan_confidence': title_plan.get('confidence') if title_plan else None,
                    'title_plan_rationale': title_plan.get('rationale') if title_plan else None,
                }
                continue

            if current_clause is None:
                if block['text_normalized']:
                    metrics['orphan_text_block_count'] += 1
                continue

            current_clause['source_block_ids'].append(block['block_id'])
            current_clause['_pages'].add(block['page_idx'])
            current_clause['_bboxes'].append(block['bbox'])
            if block['source_type'] == 'table':
                table_index = len(current_clause['tables']) + 1
                current_clause['tables'].append(
                    {
                        'table_uid': f"{current_clause['clause_uid']}#table{table_index}",
                        'standard_uid': standard_uid,
                        'parent_clause_uid': current_clause['clause_uid'],
                        'clause_ref': current_clause['clause_ref'],
                        'table_index': table_index,
                        'table_ref': block.get('table_ref'),
                        'table_title': block.get('table_title'),
                        'table_caption': block.get('table_caption'),
                        'table_html': block.get('table_html'),
                        'table_footnote': block.get('table_footnote'),
                        'table_type': block.get('table_type'),
                        'table_nest_level': block.get('table_nest_level'),
                        'image_path': block.get('table_image_path'),
                        'source_page_idx': block['page_idx'],
                        'source_bbox': block['bbox'],
                        'source_block_id': block['block_id'],
                    }
                )
                metrics['table_count'] += 1
                table_text = block.get('text') or block.get('table_html') or block.get('table_caption')
                if table_text:
                    current_clause['_text_parts'].append(table_text)
                    current_clause['_normalized_parts'].append(self._normalize_text(table_text))
                current_clause['notes'].append('table_block')
                continue
            list_match = LIST_ITEM_RE.match(block['text_normalized'])
            paragraph_list_match = PARAGRAPH_LIST_ITEM_RE.match(block['text_normalized']) if block['source_type'] == 'paragraph' else None
            item_match = list_match if block['source_type'] == 'list_item' else paragraph_list_match
            if item_match:
                current_clause['list_items'].append(
                    {
                        'item_ref': item_match.group('ref'),
                        'text': block['text'],
                        'text_normalized': item_match.group('text').strip() or block['text_normalized'],
                        'source_block_id': block['block_id'],
                        'page_idx': block['page_idx'],
                        'bbox': block['bbox'],
                    }
                )
                current_clause['_text_parts'].append(block['text'])
                current_clause['_normalized_parts'].append(block['text_normalized'])
            else:
                metrics['continuation_block_count'] += 1
                current_clause['_text_parts'].append(block['text'])
                current_clause['_normalized_parts'].append(block['text_normalized'])
                current_clause['notes'].append('continuation_block')

        finalize_clause()

        metrics['structure_node_count'] = len(nodes)
        metrics['clause_count'] = len(clauses)
        metrics['main_clause_count'] = sum(1 for clause in clauses if clause['body_kind'] == 'main')
        metrics['appendix_clause_count'] = sum(1 for clause in clauses if clause['body_kind'] == 'appendix')
        metrics['title_plan_role_counts'] = dict(title_counter)
        ref_counter = Counter(clause['clause_uid'] for clause in clauses)
        metrics['duplicate_clause_refs'] = [ref for ref, count in ref_counter.items() if count > 1]
        return nodes, clauses, metrics, warnings
    def _extract_requirements(self, clauses: list[dict[str, Any]], standard_uid: str) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
        mode = self.config.knowledge_graph.extraction_mode
        warnings: list[str] = []
        for clause in clauses:
            clause['requirement_count'] = 0

        metrics = {
            'extraction_mode_requested': mode,
            'extraction_mode_effective': 'heuristic',
            'llm_requested_clause_count': 0,
            'llm_failed_clause_count': 0,
            'llm_clause_call_count': 0,
            'llm_batch_count': 0,
        }
        eligible_clauses = [
            clause
            for clause in clauses
            if clause.get('body_kind') == 'main' or (self.config.knowledge_graph.include_appendix_requirements and clause.get('body_kind') == 'appendix')
        ]
        if mode == 'heuristic':
            requirements = self._extract_requirements_heuristic(eligible_clauses, standard_uid)
            return requirements, metrics, warnings

        llm_result = self.llm_extraction_service.extract_clauses_individually(standard_uid, eligible_clauses)
        metrics['llm_requested_clause_count'] = llm_result.metrics.get('requested_clause_count', 0)
        metrics['llm_failed_clause_count'] = llm_result.metrics.get('failed_clause_count', 0)
        metrics['llm_clause_call_count'] = llm_result.metrics.get('clause_call_count', 0)
        metrics['llm_retried_clause_count'] = llm_result.metrics.get('retried_clause_count', 0)
        metrics['llm_failed_clause_call_count'] = llm_result.metrics.get('failed_clause_call_count', 0)
        metrics['llm_batch_count'] = llm_result.metrics.get('batch_count', 0)
        metrics['llm_retried_batch_count'] = llm_result.metrics.get('retried_batch_count', 0)
        metrics['llm_retry_attempt_count'] = llm_result.metrics.get('retry_attempt_count', 0)
        metrics['llm_failed_batch_count'] = llm_result.metrics.get('failed_batch_count', 0)
        metrics['llm_batch_max_concurrency'] = llm_result.metrics.get('batch_max_concurrency', 1)
        warnings.extend(llm_result.warnings)

        llm_requirements: list[dict[str, Any]] = []
        for clause in eligible_clauses:
            item = llm_result.clause_items.get(clause['clause_uid'])
            if item is None:
                continue
            clause_requirements = self._requirements_from_llm(clause, item, standard_uid)
            clause['requirement_count'] = len(clause_requirements)
            llm_requirements.extend(clause_requirements)

        if llm_requirements:
            metrics['extraction_mode_effective'] = 'llm'

        failed_uids = set(llm_result.failed_clause_uids)
        should_fallback = self.config.knowledge_graph.fallback_to_heuristic_on_llm_error and bool(failed_uids)
        if mode == 'hybrid':
            should_fallback = True
            failed_uids = {clause['clause_uid'] for clause in eligible_clauses if clause['clause_uid'] not in llm_result.clause_items} | failed_uids
        if should_fallback:
            fallback_clauses = [clause for clause in eligible_clauses if clause['clause_uid'] in failed_uids]
            fallback_requirements = self._extract_requirements_heuristic(fallback_clauses, standard_uid)
            llm_requirements.extend(fallback_requirements)
            warnings.append(f'heuristic_fallback_clause_count={len(fallback_clauses)}')
            metrics['extraction_mode_effective'] = 'hybrid' if llm_requirements else 'heuristic'
        if not llm_requirements and mode == 'llm' and not should_fallback:
            raise RuntimeError('LLM extraction produced no requirements and fallback is disabled.')
        if not llm_requirements and should_fallback:
            metrics['extraction_mode_effective'] = 'heuristic'
        return llm_requirements, metrics, warnings

    def _generate_chapter_summaries(
        self,
        structure_nodes: list[dict[str, Any]],
        clauses: list[dict[str, Any]],
        standard_uid: str,
    ) -> tuple[dict[str, Any], list[str]]:
        result = self.chapter_summary_service.summarize_chapters(
            standard_uid=standard_uid,
            structure_nodes=structure_nodes,
            clauses=clauses,
        )
        for node in structure_nodes:
            if node.get('node_type') != 'chapter':
                continue
            item = result.chapter_items.get(node['node_uid'])
            if item is None:
                continue
            node['summary'] = item.get('summary', '')
            node['summary_source_clause_count'] = item.get('summary_source_clause_count', 0)
            node['summary_source_truncated'] = bool(item.get('summary_source_truncated'))
        return result.metrics, result.warnings

    def _extract_requirements_heuristic(self, clauses: list[dict[str, Any]], standard_uid: str) -> list[dict[str, Any]]:
        requirements: list[dict[str, Any]] = []
        for clause in clauses:
            extracted = self._requirements_from_clause(clause, standard_uid)
            clause['requirement_count'] = len(extracted)
            requirements.extend(extracted)
        return requirements

    def _requirements_from_llm(self, clause: dict[str, Any], item: dict[str, Any], standard_uid: str) -> list[dict[str, Any]]:
        clause['concepts'] = self._dedupe_strings([*(item.get('concepts') or []), *self._domain_tags(clause)])
        if item.get('clause_summary'):
            clause['clause_summary'] = item['clause_summary']
        extracted_requirements = item.get('requirements') or []
        requirements: list[dict[str, Any]] = []
        for candidate in extracted_requirements:
            requirement_text = self._normalize_text(candidate.get('requirement_text', ''))
            if not requirement_text:
                continue
            llm_confidence = self._clamp_float(candidate.get('confidence'), default=0.82)
            cited_targets = self._merge_cited_targets(
                candidate.get('cited_targets') or [],
                self._extract_cited_targets(requirement_text),
                self._extract_cited_targets(clause['source_text_normalized']),
            )
            actions = self._dedupe_strings(candidate.get('action') or [self._extract_action_text(requirement_text, candidate.get('modality') or 'must')])
            requirements.append(
                {
                    'requirement_uid': f"{clause['clause_uid']}#r{len(requirements) + 1}",
                    'standard_uid': standard_uid,
                    'clause_ref': clause['clause_ref'],
                    'parent_clause_uid': clause['clause_uid'],
                    'source_text': clause['source_text'],
                    'source_text_normalized': clause['source_text_normalized'],
                    'source_page_span': clause['source_page_span'],
                    'source_bbox': clause['source_bbox'],
                    'is_soft_split': len(extracted_requirements) > 1 or bool(clause.get('list_items')),
                    'clause_segmentation_confidence': clause['segmentation_confidence'],
                    'requirement_split_confidence': round(llm_confidence, 2),
                    'requirement_text': requirement_text,
                    'modality': candidate.get('modality') or self._detect_modality(requirement_text) or 'must',
                    'subject': candidate.get('subject') or self._extract_subject(requirement_text, candidate.get('modality') or 'must'),
                    'action': actions or [requirement_text],
                    'object': self._dedupe_strings(candidate.get('object') or self._extract_objects(requirement_text)),
                    'applicability_rule': candidate.get('applicability_rule') or self._extract_applicability(requirement_text),
                    'judgement_criteria': self._dedupe_strings(candidate.get('judgement_criteria') or [f'应在报告或资料中体现：{requirement_text}']),
                    'evidence_expected': self._dedupe_strings(candidate.get('evidence_expected') or self._infer_evidence(requirement_text)),
                    'domain_tags': self._dedupe_strings([*(candidate.get('domain_tags') or []), *clause.get('concepts', [])]),
                    'cited_targets': cited_targets,
                    'confidence': round(min(clause['segmentation_confidence'], llm_confidence), 2),
                }
            )
        return requirements

    def _generate_embeddings(self, embedding_documents: list[dict[str, Any]], metrics: dict[str, Any]) -> dict[str, list[float]]:
        if not embedding_documents:
            metrics['embedding_generation_status'] = 'skipped_no_documents'
            return {}
        if not self.config.embedding.enabled:
            metrics['embedding_generation_status'] = 'disabled'
            return {}
        if not self.embedding_client.enabled:
            metrics['embedding_generation_status'] = f'missing_api_key:{self.config.embedding.api_key_env}'
            return {}
        embeddings: dict[str, list[float]] = {}
        batch_size = max(1, self.config.embedding.batch_size)
        batches = [embedding_documents[index : index + batch_size] for index in range(0, len(embedding_documents), batch_size)]
        metrics['embedding_batch_count'] = len(batches)
        if hasattr(self.embedding_client, 'reset_stats'):
            self.embedding_client.reset_stats()
        try:
            for batch in batches:
                vectors = self.embedding_client.embed_texts([item['text'] for item in batch])
                for item, vector in zip(batch, vectors):
                    embeddings[item['node_uid']] = vector
        except Exception as exc:
            logger.exception('Embedding generation failed')
            if hasattr(self.embedding_client, 'snapshot_stats'):
                stats = self.embedding_client.snapshot_stats()
                metrics['embedding_request_attempt_count'] = stats.get('request_attempt_count', 0)
                metrics['embedding_retry_attempt_count'] = stats.get('retry_attempt_count', 0)
                metrics['embedding_retried_batch_count'] = stats.get('retried_call_count', 0)
            metrics['embedding_generation_status'] = f'failed:{exc}'
            return {}
        if hasattr(self.embedding_client, 'snapshot_stats'):
            stats = self.embedding_client.snapshot_stats()
            metrics['embedding_request_attempt_count'] = stats.get('request_attempt_count', 0)
            metrics['embedding_retry_attempt_count'] = stats.get('retry_attempt_count', 0)
            metrics['embedding_retried_batch_count'] = stats.get('retried_call_count', 0)
        metrics['embedding_generation_status'] = 'completed'
        metrics['embedding_vector_count'] = len(embeddings)
        return embeddings

    def _persist_graph(self, graph_nodes: list[dict[str, Any]], graph_edges: list[dict[str, Any]], embedding_vectors: dict[str, list[float]], metrics: dict[str, Any]) -> None:
        if not self.postgres_graph_store.enabled:
            metrics['postgres_persist_status'] = 'disabled'
            return
        try:
            result = self.postgres_graph_store.persist_graph(nodes=graph_nodes, edges=graph_edges, embedding_map=embedding_vectors)
        except Exception as exc:
            logger.exception('PostgreSQL graph persistence failed')
            metrics['postgres_persist_status'] = f'failed:{exc}'
            return
        metrics['postgres_persist_status'] = 'completed'
        metrics.update(result)
    def _requirements_from_clause(self, clause: dict[str, Any], standard_uid: str) -> list[dict[str, Any]]:
        text = clause['source_text_normalized']
        list_items = clause.get('list_items', [])
        inherited_modality = self._detect_modality(text)
        clause_has_normative_signal = inherited_modality is not None or any(pattern in text for pattern in INHERITED_LIST_PATTERNS)
        candidates: list[dict[str, Any]] = []
        if list_items and clause_has_normative_signal:
            intro = text.split('\n', 1)[0]
            for item in list_items:
                requirement_text = self._compose_requirement_from_list_item(intro, item['text_normalized'])
                candidates.append(
                    self._build_requirement(
                        standard_uid=standard_uid,
                        clause=clause,
                        requirement_index=len(candidates) + 1,
                        requirement_text=requirement_text,
                        source_text=item['text'],
                        source_text_normalized=item['text_normalized'],
                        is_soft_split=True,
                        split_confidence=0.82,
                        modality_override=inherited_modality,
                    )
                )
            clause['concepts'] = self._domain_tags(clause)
            return candidates
        for segment in self._split_clause_text(text):
            modality = self._detect_modality(segment)
            if modality is None:
                continue
            candidates.append(
                self._build_requirement(
                    standard_uid=standard_uid,
                    clause=clause,
                    requirement_index=len(candidates) + 1,
                    requirement_text=segment,
                    source_text=clause['source_text'],
                    source_text_normalized=text,
                    is_soft_split='；' in text or '。' in text,
                    split_confidence=0.74 if '；' in text else 0.9,
                    modality_override=modality,
                )
            )
        clause['concepts'] = self._domain_tags(clause)
        return candidates

    def _build_requirement(
        self,
        *,
        standard_uid: str,
        clause: dict[str, Any],
        requirement_index: int,
        requirement_text: str,
        source_text: str,
        source_text_normalized: str,
        is_soft_split: bool,
        split_confidence: float,
        modality_override: str | None,
    ) -> dict[str, Any]:
        modality = modality_override or self._detect_modality(requirement_text) or 'must'
        subject = self._extract_subject(requirement_text, modality)
        action_text = self._extract_action_text(requirement_text, modality)
        return {
            'requirement_uid': f"{clause['clause_uid']}#r{requirement_index}",
            'standard_uid': standard_uid,
            'clause_ref': clause['clause_ref'],
            'parent_clause_uid': clause['clause_uid'],
            'source_text': source_text,
            'source_text_normalized': source_text_normalized,
            'source_page_span': clause['source_page_span'],
            'source_bbox': clause['source_bbox'],
            'is_soft_split': is_soft_split,
            'clause_segmentation_confidence': clause['segmentation_confidence'],
            'requirement_split_confidence': round(split_confidence, 2),
            'requirement_text': requirement_text,
            'modality': modality,
            'subject': subject,
            'action': [action_text] if action_text else [requirement_text],
            'object': self._extract_objects(requirement_text),
            'applicability_rule': self._extract_applicability(requirement_text),
            'judgement_criteria': [f'应在报告或资料中体现：{requirement_text}'],
            'evidence_expected': self._infer_evidence(requirement_text),
            'domain_tags': self._domain_tags(clause),
            'cited_targets': self._extract_cited_targets(requirement_text),
            'confidence': round(min(clause['segmentation_confidence'], split_confidence), 2),
        }

    def _make_block(
        self,
        page_idx: int,
        block_idx: int,
        item_idx: int | None,
        source_type: str,
        text: str,
        bbox: list[int],
        raw: dict[str, Any],
        *,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        block_id = f'p{page_idx:03d}-b{block_idx:03d}' + (f'-i{item_idx:02d}' if item_idx is not None else '')
        block = {
            'block_id': block_id,
            'page_idx': page_idx,
            'source_type': source_type,
            'text': text.strip(),
            'text_normalized': self._normalize_text(text),
            'bbox': bbox,
            'raw_type': raw.get('type'),
        }
        if extra:
            block.update(extra)
        return block

    def _join_text_fragments(self, fragments: list[dict[str, Any]]) -> str:
        return self._join_rich_fragments(fragments)

    def _normalize_text(self, text: str) -> str:
        normalized = text.replace('\u3000', ' ')
        normalized = CHINESE_SPACED_RE.sub('', normalized)
        normalized = MULTI_SPACE_RE.sub(' ', normalized)
        return normalized.strip()

    def _table_to_payload(self, content: dict[str, Any]) -> dict[str, Any]:
        caption = self._join_rich_fragments(content.get('table_caption') or [])
        footnote = self._join_rich_fragments(content.get('table_footnote') or [], separator='\n')
        html = str(content.get('html') or '').strip()
        body_text = self._table_html_to_text(html) or self._table_body_to_text(content.get('table_body') or [])
        title = self._normalize_text(caption) if caption else None
        table_ref = self._extract_table_ref(title or '')
        parts = [part for part in [caption, body_text, footnote] if part]
        return {
            'text': '\n'.join(parts).strip(),
            'table_ref': table_ref,
            'table_title': title or table_ref or '表格',
            'table_caption': caption.strip() or None,
            'table_html': html or None,
            'table_footnote': footnote.strip() or None,
            'table_image_path': str(((content.get('image_source') or {}).get('path') or '')).strip() or None,
            'table_type': content.get('table_type'),
            'table_nest_level': content.get('table_nest_level'),
        }

    def _table_to_text(self, content: dict[str, Any]) -> str:
        return self._table_to_payload(content).get('text', '')

    def _table_body_to_text(self, table_body: list[Any]) -> str:
        pieces: list[str] = []
        for row in table_body:
            if not isinstance(row, list):
                continue
            row_text = []
            for cell in row:
                row_text.append(str(cell.get('text', '')).strip() if isinstance(cell, dict) else str(cell).strip())
            pieces.append(' | '.join(part for part in row_text if part))
        return '\n'.join(piece for piece in pieces if piece).strip()

    def _table_html_to_text(self, html: str) -> str:
        if not html:
            return ''
        text = re.sub(r'<\s*br\s*/?\s*>', '\n', html, flags=re.IGNORECASE)
        text = re.sub(r'</\s*tr\s*>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'<\s*tr\b[^>]*>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'</\s*t[dh]\s*>', ' | ', text, flags=re.IGNORECASE)
        text = re.sub(r'<\s*t[dh]\b[^>]*>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'<[^>]+>', '', text)
        text = unescape(text)
        lines: list[str] = []
        for raw_line in text.splitlines():
            cells = [self._normalize_text(cell) for cell in raw_line.split('|')]
            cells = [cell for cell in cells if cell]
            if cells:
                lines.append(' | '.join(cells))
        return '\n'.join(lines).strip()

    def _join_rich_fragments(self, fragments: Any, *, separator: str = '') -> str:
        if isinstance(fragments, dict):
            fragment_type = str(fragments.get('type') or '').strip().lower()
            if fragment_type in {'equation_inline', 'inline_equation'}:
                return self._wrap_inline_math(fragments.get('math_content') or fragments.get('content'))
            if fragment_type in {'equation_interline', 'interline_equation', 'equation'}:
                return self._wrap_display_math(fragments.get('math_content') or fragments.get('content'))
            if fragments.get('math_content'):
                return self._wrap_display_math(fragments.get('math_content'))
            for key in ('text', 'latex'):
                if fragments.get(key):
                    return str(fragments.get(key) or '').strip()
            return self._join_rich_fragments(fragments.get('content'), separator=separator)
        if isinstance(fragments, list):
            parts = [self._join_rich_fragments(item, separator='') for item in fragments]
            return separator.join(part for part in parts if part).strip()
        if fragments is None:
            return ''
        return str(fragments).strip()

    def _wrap_inline_math(self, value: Any) -> str:
        text = str(value or '').strip()
        if not text:
            return ''
        return f'\\({text}\\)'

    def _wrap_display_math(self, value: Any) -> str:
        text = str(value or '').strip()
        if not text:
            return ''
        return f'\\[{text}\\]'

    def _extract_table_ref(self, text: str) -> str | None:
        if not text:
            return None
        match = TABLE_REF_RE.search(text)
        if not match:
            return None
        return re.sub(r'\s+', '', match.group('ref'))

    def _classify_title(self, text: str) -> dict[str, str] | None:
        appendix = APPENDIX_TITLE_RE.match(text)
        if appendix:
            ref = appendix.group('ref') or appendix.group('label')
            title = appendix.group('title').strip() or f'{appendix.group("label")}{ref or ""}'
            return {'node_type': 'appendix', 'ref': ref, 'title': title, 'raw_text': text}
        section = SECTION_TITLE_RE.match(text)
        if section and text.count('.') == 1:
            return {'node_type': 'section', 'ref': section.group('ref'), 'title': section.group('title').strip(), 'raw_text': text}
        chapter = CHAPTER_TITLE_RE.match(text)
        if chapter and '.' not in chapter.group('ref'):
            title = chapter.group('title').strip()
            node_type = 'reference_standard' if any(keyword in title for keyword in REFERENCE_STANDARD_TITLE_KEYWORDS) else 'chapter'
            return {'node_type': node_type, 'ref': chapter.group('ref'), 'title': title, 'raw_text': text}
        return None

    def _resolve_title_plan(
        self,
        blocks: list[dict[str, Any]],
        standard_uid: str,
        *,
        title_inventory: list[dict[str, Any]] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[str], dict[str, Any]]:
        title_inventory = title_inventory if title_inventory is not None else self._build_title_inventory(blocks)
        if not title_inventory:
            return [], {}, [], {
                'title_plan_source': 'heuristic',
                'title_planner_enabled': bool(getattr(self.outline_planner, 'enabled', False)),
                'title_plan_llm_item_count': 0,
                'title_plan_heuristic_item_count': 0,
                'title_plan_missing_item_count': 0,
                'title_planner_requested_count': 0,
                'title_planner_batch_count': 0,
                'title_planner_successful_count': 0,
                'title_planner_failed_batch_count': 0,
                'title_planner_role_counts': {},
            }

        heuristic_plan = self._build_heuristic_title_plan(title_inventory)
        heuristic_by_block_id = {item['title_id']: item for item in heuristic_plan}
        plan_by_block_id: dict[str, dict[str, Any]] = {}
        warnings: list[str] = []
        metrics: dict[str, Any] = {
            'title_plan_source': 'llm' if getattr(self.outline_planner, 'enabled', False) else 'heuristic',
            'title_planner_enabled': bool(getattr(self.outline_planner, 'enabled', False)),
            'title_plan_llm_item_count': 0,
            'title_plan_heuristic_item_count': 0,
        }
        llm_item_count = 0

        if getattr(self.outline_planner, 'enabled', False):
            planner_result = self.outline_planner.plan_titles(standard_uid=standard_uid, title_inventory=heuristic_plan)
            warnings.extend(planner_result.warnings)
            metrics.update(planner_result.metrics)
            for item in planner_result.items:
                title_id = item.get('title_id')
                if not title_id or title_id not in heuristic_by_block_id:
                    continue
                base_item = heuristic_by_block_id[title_id]
                merged_item = {**base_item, **item}
                merged_item['planner_source'] = item.get('planner_source') or 'llm'
                plan_by_block_id[title_id] = merged_item
                llm_item_count += 1
            metrics['title_plan_llm_item_count'] = llm_item_count

        heuristic_item_count = 0
        if not plan_by_block_id:
            for item in heuristic_plan:
                plan_by_block_id[item['title_id']] = item
                heuristic_item_count += 1

        metrics['title_plan_heuristic_item_count'] = heuristic_item_count
        metrics['title_plan_missing_item_count'] = max(0, len(title_inventory) - len(plan_by_block_id))
        metrics['title_plan_source'] = 'llm' if llm_item_count else 'heuristic'
        if 'title_planner_requested_count' not in metrics:
            role_counts = Counter(item['role'] for item in plan_by_block_id.values())
            metrics.update(
                {
                    'title_planner_requested_count': len(title_inventory),
                    'title_planner_batch_count': 0,
                    'title_planner_successful_count': len(plan_by_block_id),
                    'title_planner_failed_batch_count': 0,
                    'title_planner_role_counts': dict(sorted(role_counts.items())),
                }
            )

        plan = [plan_by_block_id[item['title_id']] for item in title_inventory if item['title_id'] in plan_by_block_id]
        return plan, plan_by_block_id, warnings, metrics

    def _build_title_inventory(self, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        title_indices = [index for index, block in enumerate(blocks) if block.get('source_type') == 'title']
        inventory: list[dict[str, Any]] = []
        previous_title_text: str | None = None
        for order, block_index in enumerate(title_indices, start=1):
            block = blocks[block_index]
            next_title = blocks[title_indices[order]] if order < len(title_indices) else None
            inventory.append(
                {
                    'title_id': block['block_id'],
                    'title_index': order,
                    'block_id': block['block_id'],
                    'page_idx': block['page_idx'],
                    'page_role': block.get('page_role'),
                    'text': block['text'],
                    'text_normalized': block['text_normalized'],
                    'raw_title_level': block.get('raw_title_level'),
                    'previous_title': previous_title_text,
                    'next_title': next_title['text_normalized'] if next_title else None,
                    'preceding_text_preview': self._nearest_text_preview(blocks, block['block_id'], direction='backward'),
                    'following_text_preview': self._nearest_text_preview(blocks, block['block_id'], direction='forward'),
                    'numbering_pattern': self._title_numbering_pattern(block['text_normalized']),
                    'looks_structural': self._looks_structural_title(block['text_normalized']),
                }
            )
            previous_title_text = block['text_normalized']
        return inventory

    def _build_heuristic_title_plan(self, title_inventory: list[dict[str, Any]]) -> list[dict[str, Any]]:
        title_plan: list[dict[str, Any]] = []
        for title in title_inventory:
            title_info = self._classify_title(title['text_normalized'])
            if title.get('page_role') == 'toc':
                role = 'toc' if title['text_normalized'] in {'目录', '目 录'} else 'ignore'
                ref = None
            elif title['text_normalized'] in {'目录', '目 录'}:
                role = 'toc'
                ref = None
            elif CLAUSE_START_RE.match(title['text_normalized']):
                role = 'clause'
                ref = CLAUSE_START_RE.match(title['text_normalized']).group('ref')  # type: ignore[union-attr]
            elif title_info:
                role = title_info['node_type']
                ref = title_info.get('ref')
            else:
                role = 'ignore'
                ref = None

            title_plan.append(
                {
                    **title,
                    'role': role,
                    'node_type': role,
                    'hierarchy_level': self._role_hierarchy_level(role),
                    'is_structural': role in {'appendix', 'reference_standard', 'chapter', 'section'},
                    'ref': ref,
                    'planner_source': 'heuristic',
                    'confidence': None,
                    'rationale': None,
                    'heuristic_role': role,
                    'heuristic_ref': ref,
                }
            )
        return title_plan

    def _title_plan_to_structure_info(
        self,
        title_plan: dict[str, Any],
        block: dict[str, Any],
    ) -> dict[str, Any] | None:
        role = str(title_plan.get('role') or title_plan.get('label') or '').strip().lower()
        if role in {'ignore', 'none', 'clause'}:
            return None
        if role == 'toc':
            return {
                'node_type': 'toc',
                'ref': title_plan.get('ref'),
                'title': block['text_normalized'],
                'raw_text': block['text_normalized'],
            }
        if role == 'appendix':
            appendix = APPENDIX_TITLE_RE.match(block['text_normalized'])
            if appendix:
                ref = title_plan.get('ref') or appendix.group('ref') or appendix.group('label')
                return {
                    'node_type': 'appendix',
                    'ref': ref,
                    'title': appendix.group('title').strip() or f'{appendix.group("label")}{ref or ""}',
                    'raw_text': block['text_normalized'],
                }
            return {
                'node_type': 'appendix',
                'ref': title_plan.get('ref'),
                'title': block['text_normalized'],
                'raw_text': block['text_normalized'],
            }
        if role == 'section':
            section = SECTION_TITLE_RE.match(block['text_normalized'])
            if section:
                return {
                    'node_type': 'section',
                    'ref': title_plan.get('ref') or section.group('ref'),
                    'title': section.group('title').strip(),
                    'raw_text': block['text_normalized'],
                }
            return {
                'node_type': 'section',
                'ref': title_plan.get('ref'),
                'title': block['text_normalized'],
                'raw_text': block['text_normalized'],
            }
        if role in {'chapter', 'reference_standard'}:
            chapter = CHAPTER_TITLE_RE.match(block['text_normalized'])
            ref = title_plan.get('ref') or (chapter.group('ref') if chapter and '.' not in chapter.group('ref') else None)
            title = chapter.group('title').strip() if chapter and '.' not in chapter.group('ref') else block['text_normalized']
            return {
                'node_type': role,
                'ref': ref,
                'title': title,
                'raw_text': block['text_normalized'],
            }
        return None

    def _should_accept_title_candidate(
        self,
        *,
        title_info: dict[str, str],
        block: dict[str, Any],
        current_clause: dict[str, Any] | None,
        current_chapter: dict[str, Any] | None,
        current_section: dict[str, Any] | None,
        prev_block: dict[str, Any] | None,
        next_block: dict[str, Any] | None,
    ) -> bool:
        del current_chapter, current_section, prev_block
        if title_info['node_type'] != 'chapter' or current_clause is None:
            return True
        candidate_ref = title_info.get('ref', '')
        current_clause_ref = str(current_clause.get('clause_ref') or '')
        current_top_ref = current_clause_ref.split('.', 1)[0]
        if not candidate_ref.isdigit() or not current_top_ref.isdigit():
            return True
        candidate_num = int(candidate_ref)
        current_num = int(current_top_ref)
        if candidate_num < current_num:
            return False
        if candidate_num != current_num and self._looks_like_clause_inline_title(block['text_normalized'], next_block):
            return False
        return True

    def _looks_like_clause_inline_title(self, text: str, next_block: dict[str, Any] | None) -> bool:
        normalized = self._normalize_text(text)
        if normalized.endswith(('：', ':')) or '如下' in normalized:
            return True
        if next_block is None:
            return False
        next_text = next_block.get('text_normalized') or ''
        if next_block.get('source_type') == 'table':
            return True
        if LIST_ITEM_RE.match(next_text):
            return True
        if next_text.startswith(('表', '式中', '公式')):
            return True
        return False

    def _make_structure_node(
        self,
        standard_uid: str,
        title_info: dict[str, Any],
        block: dict[str, Any],
        parent_uid: str | None,
        title_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ref_value = str(title_info.get('ref') or block['block_id'])
        suffix = ref_value.lower().replace('附录', 'appendix-')
        return {
            'node_uid': f"{standard_uid}:{title_info['node_type']}:{suffix}",
            'node_type': title_info['node_type'],
            'ref': title_info.get('ref'),
            'title': title_info['title'],
            'raw_text': title_info['raw_text'],
            'parent_uid': parent_uid,
            'page_idx': block['page_idx'],
            'bbox': block['bbox'],
            'source_block_id': block['block_id'],
            'title_index': title_plan.get('title_index') if title_plan else None,
            'title_planner_source': title_plan.get('planner_source') if title_plan else None,
            'title_plan_confidence': title_plan.get('confidence') if title_plan else None,
            'title_plan_rationale': title_plan.get('rationale') if title_plan else None,
        }

    def _make_clause_uid(self, standard_uid: str, body_kind: str, appendix: dict[str, Any] | None, clause_ref: str) -> str:
        scope = 'main' if body_kind == 'main' else self._appendix_scope(appendix)
        return f"{standard_uid}:{scope}:{clause_ref}"

    def _appendix_scope(self, appendix: dict[str, Any] | None) -> str:
        if not appendix:
            return 'front'
        ref = str(appendix.get('ref') or '').strip()
        if ref:
            return f'appendix-{ref.lower()}'
        node_uid = str(appendix.get('node_uid') or '').rsplit(':', 1)[-1].strip()
        return f'appendix-{node_uid or "unknown"}'

    def _extract_clause_match(
        self,
        block: dict[str, Any],
        title_plan: dict[str, Any] | None,
    ) -> tuple[str, str] | None:
        normalized = block.get('text_normalized') or ''
        if title_plan and str(title_plan.get('role') or '').strip().lower() == 'clause':
            ref = str(title_plan.get('ref') or '').strip()
            if not ref:
                match = CLAUSE_START_RE.match(normalized)
                ref = match.group('ref') if match else ''
            if ref:
                return ref, normalized
        match = CLAUSE_START_RE.match(normalized)
        if not match:
            return None
        return match.group('ref'), normalized

    def _role_hierarchy_level(self, role: str) -> int:
        if role in {'appendix', 'reference_standard', 'chapter'}:
            return 1
        if role == 'section':
            return 2
        if role == 'clause':
            return 3
        return 0

    def _looks_structural_title(self, text: str) -> bool:
        normalized = self._normalize_text(text)
        if normalized in {'目录', '目 录'}:
            return False
        if APPENDIX_TITLE_RE.match(normalized):
            return True
        if CLAUSE_START_RE.match(normalized):
            return True
        if SECTION_TITLE_RE.match(normalized):
            return True
        return bool(CHAPTER_TITLE_RE.match(normalized))

    def _title_numbering_pattern(self, text: str | None) -> str:
        normalized = self._normalize_text(text or '')
        if normalized in {'目录', '目 录'}:
            return 'toc'
        if APPENDIX_TITLE_RE.match(normalized):
            return 'appendix'
        if CLAUSE_START_RE.match(normalized):
            return 'clause'
        if SECTION_TITLE_RE.match(normalized):
            return 'section'
        if CHAPTER_TITLE_RE.match(normalized):
            return 'chapter'
        if LIST_ITEM_RE.match(normalized):
            return 'list_item'
        return 'plain'

    def _nearest_text_preview(
        self,
        blocks: list[dict[str, Any]],
        anchor_block_id: str,
        *,
        direction: str,
        max_chars: int = 180,
    ) -> str | None:
        block_index = next((index for index, block in enumerate(blocks) if block['block_id'] == anchor_block_id), None)
        if block_index is None:
            return None

        indexes = range(block_index - 1, -1, -1) if direction == 'backward' else range(block_index + 1, len(blocks))
        previews: list[str] = []
        for index in indexes:
            block = blocks[index]
            if direction == 'forward' and block.get('source_type') == 'title':
                break
            if block.get('source_type') == 'title':
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
        normalized = self._normalize_text(text or '')
        if not normalized:
            return None
        if len(normalized) <= max_chars:
            return normalized
        return normalized[: max_chars - 3].rstrip() + '...'

    def _detect_page_roles(self, blocks: list[dict[str, Any]]) -> dict[int, str]:
        blocks_by_page: dict[int, list[dict[str, Any]]] = {}
        for block in blocks:
            blocks_by_page.setdefault(block['page_idx'], []).append(block)

        toc_pages: set[int] = set()
        toc_start_page = next(
            (
                page_idx
                for page_idx in sorted(blocks_by_page)
                if any(self._normalize_text(block.get('text_normalized') or block.get('text')) in {'目录', '目 录'} for block in blocks_by_page[page_idx])
            ),
            None,
        )
        first_structural_page = next(
            (
                page_idx
                for page_idx in sorted(blocks_by_page)
                if any(self._looks_structural_title(block.get('text_normalized') or block.get('text')) for block in blocks_by_page[page_idx])
            ),
            None,
        )

        if toc_start_page is not None:
            for page_idx in sorted(blocks_by_page):
                if page_idx < toc_start_page:
                    continue
                if self._looks_like_toc_page(blocks_by_page[page_idx]):
                    toc_pages.add(page_idx)
                    continue
                if first_structural_page is not None and page_idx >= first_structural_page and page_idx > toc_start_page:
                    break
                if page_idx - toc_start_page <= 3:
                    toc_pages.add(page_idx)

        page_roles: dict[int, str] = {}
        for page_idx in sorted(blocks_by_page):
            if page_idx in toc_pages:
                page_roles[page_idx] = 'toc'
            elif first_structural_page is not None and page_idx < first_structural_page:
                page_roles[page_idx] = 'front_matter'
            else:
                page_roles[page_idx] = 'body'
        return page_roles

    def _looks_like_toc_page(self, page_blocks: list[dict[str, Any]]) -> bool:
        texts = [self._normalize_text(block.get('text_normalized') or block.get('text')) for block in page_blocks]
        texts = [text for text in texts if text]
        if not texts:
            return False
        if any(text in {'目录', '目 录'} for text in texts):
            return True
        toc_like_count = 0
        for text in texts:
            if '……' in text or '.....' in text or re.search(r'\.{2,}\s*\d+\s*$', text):
                toc_like_count += 1
                continue
            if re.match(r'^\d+(?:\.\d+)*\s+.+\s+\d+\s*$', text):
                toc_like_count += 1
        return toc_like_count >= max(2, len(texts) // 2)

    def _split_clause_text(self, text: str) -> list[str]:
        normalized = text.replace('\n', ' ')
        raw_segments = re.split(r'(?<=[；。])', normalized)
        segments: list[str] = []
        for segment in raw_segments:
            value = segment.strip()
            if not value:
                continue
            value = re.sub(r'^\d+\.\d+\.\d+\s*', '', value)
            if segments and self._detect_modality(value) is None and len(value) < 18:
                segments[-1] = segments[-1].rstrip('。') + value
            else:
                segments.append(value)
        return segments

    def _detect_modality(self, text: str) -> str | None:
        stripped = text.strip()
        if any(word in stripped for word in FORBIDDEN_WORDS):
            return 'forbidden'
        if stripped.startswith(CONDITIONAL_PREFIXES) and any(word in stripped for word in MUST_WORDS + SHOULD_WORDS + MAY_WORDS):
            return 'conditional'
        if any(word in stripped for word in MUST_WORDS):
            return 'must'
        if any(word in stripped for word in SHOULD_WORDS):
            return 'should'
        if any(word in stripped for word in MAY_WORDS):
            return 'may'
        return None

    def _extract_subject(self, text: str, modality: str) -> str | None:
        del modality
        markers = FORBIDDEN_WORDS + MUST_WORDS + SHOULD_WORDS + MAY_WORDS
        idxs = [text.find(marker) for marker in markers if marker in text]
        if not idxs:
            return None
        idx = min(value for value in idxs if value >= 0)
        subject = text[:idx].strip(' ：:，,；;。')
        return subject or None

    def _extract_action_text(self, text: str, modality: str) -> str:
        del modality
        markers = FORBIDDEN_WORDS + MUST_WORDS + SHOULD_WORDS + MAY_WORDS
        best_idx = None
        marker_len = 0
        for marker in markers:
            idx = text.find(marker)
            if idx >= 0 and (best_idx is None or idx < best_idx):
                best_idx = idx
                marker_len = len(marker)
        if best_idx is None:
            return text.strip()
        return text[best_idx + marker_len :].strip(' ：:，,；;。') or text.strip()

    def _extract_objects(self, text: str) -> list[str]:
        objects: list[str] = []
        for keyword in ['报告', '资料', '记录', '文件', '预案', '监测', '检测', '试验', '计算', '检查表', '报告书']:
            if keyword in text:
                objects.append(keyword)
        return list(dict.fromkeys(objects))

    def _extract_applicability(self, text: str) -> str | None:
        prefixes = ['对于', '对', '当', '若', '凡', '大型', '中型', '小型', '土石坝', '混凝土坝', '砌石坝']
        for prefix in prefixes:
            if text.startswith(prefix):
                return text.split('，', 1)[0].split('。', 1)[0]
        return None

    def _infer_evidence(self, text: str) -> list[str]:
        evidence: list[str] = []
        for keyword, values in EVIDENCE_RULES:
            if keyword in text:
                evidence.extend(values)
        if not evidence:
            evidence.append('相关说明资料')
        return list(dict.fromkeys(evidence))

    def _domain_tags(self, clause: dict[str, Any]) -> list[str]:
        tags = list(clause.get('heading_path', []))
        for keyword in ['现场安全检查', '安全检测', '监测', '运行管理', '防洪能力', '渗流安全', '结构安全', '抗震安全', '金属结构']:
            if keyword in clause['source_text_normalized']:
                tags.append(keyword)
        return list(dict.fromkeys(tags))

    def _extract_cited_targets(self, text: str) -> list[dict[str, str | None]]:
        targets = []
        for match in STANDARD_REF_RE.finditer(text):
            code = match.group('code').replace(' ', '')
            number = match.group('number')
            year = match.group('year')
            standard_code = f'{code}{number}' + (f'-{year}' if year else '')
            targets.append({'standard_code': standard_code, 'clause_ref': None, 'citation_type': 'mandatory'})
        return self._merge_cited_targets(targets)

    def _merge_cited_targets(self, *groups: list[dict[str, Any]]) -> list[dict[str, str | None]]:
        merged: list[dict[str, str | None]] = []
        seen: set[tuple[str | None, str | None, str | None]] = set()
        for group in groups:
            for target in group or []:
                if not isinstance(target, dict):
                    continue
                normalized = {
                    'standard_code': target.get('standard_code'),
                    'clause_ref': target.get('clause_ref'),
                    'citation_type': target.get('citation_type') or 'unknown',
                }
                key = (normalized['standard_code'], normalized['clause_ref'], normalized['citation_type'])
                if not normalized['standard_code'] or key in seen:
                    continue
                seen.add(key)
                merged.append(normalized)
        return merged

    def _score_clause_segmentation(self, clause: dict[str, Any]) -> float:
        confidence = 0.95
        if 'continuation_block' in clause.get('notes', []):
            confidence -= 0.08
        if clause.get('body_kind') == 'appendix':
            confidence -= 0.04
        if len(clause.get('source_block_ids', [])) > 6:
            confidence -= 0.05
        return round(max(0.55, confidence), 2)

    def _compose_requirement_from_list_item(self, intro: str, item_text: str) -> str:
        clean_item = re.sub(r'^(?:\(?\d+\)?|\d+[）\.])\s*', '', item_text).strip('；;')
        intro_clean = re.sub(r'^\d+\.\d+\.\d+\s*', '', intro.strip().rstrip('：:'))
        if '包括下列' in intro_clean or '包括以下' in intro_clean or '应包括' in intro_clean or '宜包括' in intro_clean:
            subject = self._extract_subject(intro_clean, self._detect_modality(intro_clean) or 'must') or intro_clean
            marker = '宜包括' if '宜包括' in intro_clean else '应包括' if '应包括' in intro_clean else '包括'
            return f'{subject}{marker}{clean_item.rstrip("。")}。'
        if '应遵守下列' in intro_clean:
            subject = self._extract_subject(intro_clean, self._detect_modality(intro_clean) or 'must') or intro_clean
            return f'{subject}应遵守：{clean_item.rstrip("。")}。'
        return clean_item if clean_item.endswith('。') else f'{clean_item}。'

    def _dedupe_strings(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            if not value:
                continue
            item = self._normalize_text(str(value))
            if not item or item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result

    def _clamp_float(self, value: Any, default: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        return max(0.0, min(1.0, parsed))
    def _build_report(
        self,
        artifact_dir: Path,
        standard_uid: str,
        metrics: dict[str, Any],
        clauses: list[dict[str, Any]],
        requirements: list[dict[str, Any]],
        extraction_warnings: list[str],
    ) -> str:
        req_map: dict[str, list[dict[str, Any]]] = {}
        for requirement in requirements:
            req_map.setdefault(requirement['parent_clause_uid'], []).append(requirement)

        def clause_heading(clause: dict[str, Any]) -> str:
            clause_ref = clause.get('clause_ref') or 'n/a'
            appendix_ref = clause.get('appendix_ref')
            body_kind = clause.get('body_kind')
            if body_kind == 'appendix' and appendix_ref:
                return f'附录{appendix_ref} / {clause_ref}'
            if body_kind == 'front_matter':
                return f'前置部分 / {clause_ref}'
            return clause_ref

        lines = [
            f'# Segmentation Report: {standard_uid}',
            '',
            f'- Artifact dir: `{artifact_dir}`',
            f'- Extraction mode requested: {metrics.get("extraction_mode_requested")}',
            f'- Extraction mode effective: {metrics.get("extraction_mode_effective")}',
            f'- Title plan source: {metrics.get("title_plan_source", "heuristic")}',
            f'- Title plan items: {metrics.get("title_plan_count", 0)}',
            f'- Normalized blocks: {metrics["normalized_block_count"]}',
            f'- Structure nodes: {metrics["structure_node_count"]}',
            f'- Clauses: {metrics["clause_count"]}',
            f'- Main clauses: {metrics["main_clause_count"]}',
            f'- Appendix clauses: {metrics["appendix_clause_count"]}',
            f'- Requirements: {metrics["requirement_count"]}',
            f'- Clauses with requirements: {metrics["clauses_with_requirements"]}',
            f'- Chapter summaries: {metrics.get("chapter_summary_completed_count", 0)}/{metrics.get("chapter_summary_requested_count", 0)}',
            f'- Chapter summary status: {metrics.get("chapter_summary_status", "n/a")}',
            f'- Graph nodes: {metrics.get("graph_node_count", 0)}',
            f'- Graph edges: {metrics.get("graph_edge_count", 0)}',
            f'- Embedding docs: {metrics.get("embedding_document_count", 0)}',
            f'- Embedding status: {metrics.get("embedding_generation_status", "n/a")}',
            f'- PostgreSQL persist: {metrics.get("postgres_persist_status", "n/a")}',
            f'- Orphan text blocks: {metrics["orphan_text_block_count"]}',
            f'- Continuation blocks: {metrics["continuation_block_count"]}',
            '',
        ]
        if extraction_warnings:
            lines.extend(['## Extraction Warnings', ''])
            for warning in extraction_warnings:
                lines.append(f'- {warning}')
            lines.append('')

        lines.extend([
            '## Clause Details',
            '',
            f'- Included clauses: {len(clauses)}',
            f'- Included requirements: {len(requirements)}',
            '',
        ])
        for clause in clauses:
            clause_requirements = req_map.get(clause['clause_uid'], [])
            source_page_span = clause.get('source_page_span') or []
            if len(source_page_span) >= 2:
                page_text = f'{source_page_span[0]}-{source_page_span[1]}'
            else:
                page_text = 'n/a'
            lines.extend([
                f'### {clause_heading(clause)}',
                '',
                f'- Clause UID: {clause["clause_uid"]}',
                f'- Body kind: {clause.get("body_kind") or "n/a"}',
                f'- Appendix ref: {clause.get("appendix_ref") or "n/a"}',
                f'- Chapter: {clause.get("chapter_ref") or "n/a"}',
                f'- Section: {clause.get("section_ref") or "n/a"}',
                f'- Pages: {page_text}',
                f'- Segmentation confidence: {clause["segmentation_confidence"]}',
                f'- Requirement count: {clause.get("requirement_count", 0)}',
                f'- Concepts: {", ".join(clause.get("concepts", [])) or "n/a"}',
                '- Text:',
                '',
                clause.get('source_text_normalized') or clause.get('source_text') or '',
                '',
                '#### Requirements',
                '',
            ])
            if clause_requirements:
                for requirement in clause_requirements:
                    confidence = requirement.get('confidence')
                    if isinstance(confidence, (int, float)):
                        confidence_text = f'{float(confidence):.2f}'
                    else:
                        confidence_text = str(confidence) if confidence is not None else 'n/a'
                    lines.append(
                        f'- {requirement["requirement_uid"]} | {requirement.get("modality") or "unknown"} | confidence {confidence_text} | {requirement.get("requirement_text") or ""}'
                    )
            else:
                lines.append('- None')
            lines.append('')
        return '\n'.join(lines).strip() + '\n'
