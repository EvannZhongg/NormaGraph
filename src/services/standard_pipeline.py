from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from dataclasses import dataclass, field
from pathlib import Path
import json
import logging
import re
from typing import Any

from adapters.llm_client import EmbeddingsAPIClient, ResponsesAPIClient
from core.config import AppConfig, get_config
from repositories.postgres_graph_store import PostgresGraphStore
from services.graph_materialization import GraphMaterializationService
from services.llm_extraction import LLMGraphExtractionService


logger = logging.getLogger(__name__)

CHINESE_SPACED_RE = re.compile(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])')
MULTI_SPACE_RE = re.compile(r'\s+')
CHAPTER_TITLE_RE = re.compile(r'^(?P<ref>\d+)\s+(?P<title>.+)$')
SECTION_TITLE_RE = re.compile(r'^(?P<ref>\d+\.\d+)\s+(?P<title>.+)$')
APPENDIX_TITLE_RE = re.compile(r'^附录(?P<ref>[A-ZＡ-Ｚ])\s*(?P<title>.*)$')
CLAUSE_START_RE = re.compile(r'^(?P<ref>\d+\.\d+\.\d+)\s*(?P<text>.*)$')
LIST_ITEM_RE = re.compile(r'^(?P<ref>(?:\(?\d+\)?|\d+[）\.]))\s*(?P<text>.*)$')
STANDARD_REF_RE = re.compile(r'\b(?P<code>(?:GB/T|GB|SL|DL/T|SDJ|SLJ|CECS|JGJ/T|JGJ))\s*(?P<number>\d+(?:/\w+)?)(?:[-—](?P<year>\d{2,4}))?')
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
    structure_nodes: list[dict[str, Any]]
    clauses: list[dict[str, Any]]
    requirements: list[dict[str, Any]]
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
        graph_materialization_service: GraphMaterializationService | None = None,
        embedding_client: EmbeddingsAPIClient | None = None,
        postgres_graph_store: PostgresGraphStore | None = None,
    ) -> None:
        self.config = config or get_config()
        self.llm_extraction_service = llm_extraction_service or LLMGraphExtractionService(self.config, ResponsesAPIClient(self.config))
        self.graph_materialization_service = graph_materialization_service or GraphMaterializationService(self.config)
        self.embedding_client = embedding_client or EmbeddingsAPIClient(self.config)
        self.postgres_graph_store = postgres_graph_store or PostgresGraphStore(self.config)

    def run(self, artifact_dir: Path, standard_uid: str) -> PipelineOutput:
        content_list_path = artifact_dir / 'content_list_v2.json'
        if not content_list_path.exists():
            raise FileNotFoundError(f'content_list_v2.json was not found in {artifact_dir}')

        data = json.loads(content_list_path.read_text(encoding='utf-8'))
        normalized_blocks = self._flatten_content_list(data)
        structure_nodes, clauses, metrics = self._build_structure(normalized_blocks, standard_uid)
        requirements, extraction_metrics, extraction_warnings = self._extract_requirements(clauses, standard_uid)
        metrics.update(extraction_metrics)
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
            structure_nodes=structure_nodes,
            clauses=clauses,
            requirements=requirements,
            graph_nodes=graph_nodes,
            graph_edges=graph_edges,
            embedding_documents=embedding_documents,
            embedding_vectors=embedding_vectors,
            extraction_warnings=extraction_warnings,
            metrics=metrics,
            report_markdown=report_markdown,
        )

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
            'normalized_structure': graph_space_dir / 'normalized_structure.json',
            'clauses': graph_space_dir / 'clauses.json',
            'requirements': graph_space_dir / 'requirements.json',
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
        files['normalized_structure'].write_text(json.dumps({'nodes': output.structure_nodes}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        files['clauses'].write_text(json.dumps(output.clauses, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        files['requirements'].write_text(json.dumps(output.requirements, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
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

    def _flatten_content_list(self, pages: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for page_idx, page in enumerate(pages, start=1):
            for block_idx, item in enumerate(page, start=1):
                block_type = item.get('type', 'unknown')
                bbox = item.get('bbox') or []
                if block_type == 'title':
                    text = self._join_text_fragments(item.get('content', {}).get('title_content', []))
                    blocks.append(self._make_block(page_idx, block_idx, None, 'title', text, bbox, item))
                elif block_type == 'paragraph':
                    text = self._join_text_fragments(item.get('content', {}).get('paragraph_content', []))
                    blocks.append(self._make_block(page_idx, block_idx, None, 'paragraph', text, bbox, item))
                elif block_type == 'list':
                    for item_idx, list_item in enumerate(item.get('content', {}).get('list_items', []), start=1):
                        text = self._join_text_fragments(list_item.get('item_content', []))
                        blocks.append(self._make_block(page_idx, block_idx, item_idx, 'list_item', text, bbox, item))
                elif block_type == 'table':
                    text = self._table_to_text(item.get('content', {}))
                    if text:
                        blocks.append(self._make_block(page_idx, block_idx, None, 'table', text, bbox, item))
        return blocks

    def _build_structure(self, blocks: list[dict[str, Any]], standard_uid: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        nodes: list[dict[str, Any]] = []
        clauses: list[dict[str, Any]] = []
        metrics = {
            'normalized_block_count': len(blocks),
            'structure_node_count': 0,
            'clause_count': 0,
            'main_clause_count': 0,
            'appendix_clause_count': 0,
            'orphan_text_block_count': 0,
            'continuation_block_count': 0,
            'title_classification': {},
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
            current_clause['requirement_count'] = 0
            current_clause['concepts'] = []
            clauses.append(current_clause)
            current_clause = None

        for block in blocks:
            title_info = self._classify_title(block['text_normalized']) if block['source_type'] == 'title' else None
            if title_info:
                finalize_clause()
                title_counter[title_info['node_type']] += 1
                if title_info['node_type'] == 'appendix':
                    current_body_kind = 'appendix'
                    appendix_title_seen = True
                    current_appendix = self._make_structure_node(standard_uid, title_info, block, parent_uid=None)
                    current_chapter = None
                    current_section = None
                    nodes.append(current_appendix)
                    continue
                if title_info['node_type'] == 'chapter':
                    if not appendix_title_seen:
                        current_body_kind = 'main'
                    current_chapter = self._make_structure_node(
                        standard_uid,
                        title_info,
                        block,
                        parent_uid=current_appendix['node_uid'] if current_body_kind == 'appendix' and current_appendix else None,
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
                    )
                    nodes.append(current_section)
                    continue
                nodes.append(self._make_structure_node(standard_uid, title_info, block, parent_uid=None))
                continue

            clause_match = CLAUSE_START_RE.match(block['text_normalized'])
            if clause_match:
                finalize_clause()
                clause_ref = clause_match.group('ref')
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
                    '_normalized_parts': [block['text_normalized']],
                    '_pages': {block['page_idx']},
                    '_bboxes': [block['bbox']],
                    'list_items': [],
                    'notes': [],
                }
                continue

            if current_clause is None:
                if block['text_normalized']:
                    metrics['orphan_text_block_count'] += 1
                continue

            current_clause['source_block_ids'].append(block['block_id'])
            current_clause['_pages'].add(block['page_idx'])
            current_clause['_bboxes'].append(block['bbox'])
            list_match = LIST_ITEM_RE.match(block['text_normalized'])
            if list_match and block['source_type'] == 'list_item':
                current_clause['list_items'].append(
                    {
                        'item_ref': list_match.group('ref'),
                        'text': block['text'],
                        'text_normalized': list_match.group('text').strip() or block['text_normalized'],
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
        metrics['title_classification'] = dict(title_counter)
        ref_counter = Counter(clause['clause_uid'] for clause in clauses)
        metrics['duplicate_clause_refs'] = [ref for ref, count in ref_counter.items() if count > 1]
        return nodes, clauses, metrics
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

        llm_result = self.llm_extraction_service.extract_clauses(standard_uid, eligible_clauses)
        metrics['llm_requested_clause_count'] = llm_result.metrics.get('requested_clause_count', 0)
        metrics['llm_failed_clause_count'] = llm_result.metrics.get('failed_clause_count', 0)
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
        try:
            for batch in batches:
                vectors = self.embedding_client.embed_texts([item['text'] for item in batch])
                for item, vector in zip(batch, vectors):
                    embeddings[item['node_uid']] = vector
        except Exception as exc:
            logger.exception('Embedding generation failed')
            metrics['embedding_generation_status'] = f'failed:{exc}'
            return {}
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

    def _make_block(self, page_idx: int, block_idx: int, item_idx: int | None, source_type: str, text: str, bbox: list[int], raw: dict[str, Any]) -> dict[str, Any]:
        block_id = f'p{page_idx:03d}-b{block_idx:03d}' + (f'-i{item_idx:02d}' if item_idx is not None else '')
        return {
            'block_id': block_id,
            'page_idx': page_idx,
            'source_type': source_type,
            'text': text.strip(),
            'text_normalized': self._normalize_text(text),
            'bbox': bbox,
            'raw_type': raw.get('type'),
        }

    def _join_text_fragments(self, fragments: list[dict[str, Any]]) -> str:
        return ''.join(fragment.get('content', '') for fragment in fragments if fragment.get('type') == 'text').strip()

    def _normalize_text(self, text: str) -> str:
        normalized = text.replace('\u3000', ' ')
        normalized = CHINESE_SPACED_RE.sub('', normalized)
        normalized = MULTI_SPACE_RE.sub(' ', normalized)
        return normalized.strip()

    def _table_to_text(self, content: dict[str, Any]) -> str:
        pieces: list[str] = []
        for row in content.get('table_body') or []:
            if not isinstance(row, list):
                continue
            row_text = []
            for cell in row:
                row_text.append(str(cell.get('text', '')).strip() if isinstance(cell, dict) else str(cell).strip())
            pieces.append(' | '.join(part for part in row_text if part))
        return '\n'.join(piece for piece in pieces if piece).strip()

    def _classify_title(self, text: str) -> dict[str, str] | None:
        appendix = APPENDIX_TITLE_RE.match(text)
        if appendix:
            return {'node_type': 'appendix', 'ref': appendix.group('ref'), 'title': appendix.group('title').strip() or f'附录{appendix.group("ref")}', 'raw_text': text}
        section = SECTION_TITLE_RE.match(text)
        if section and text.count('.') == 1:
            return {'node_type': 'section', 'ref': section.group('ref'), 'title': section.group('title').strip(), 'raw_text': text}
        chapter = CHAPTER_TITLE_RE.match(text)
        if chapter and '.' not in chapter.group('ref'):
            return {'node_type': 'chapter', 'ref': chapter.group('ref'), 'title': chapter.group('title').strip(), 'raw_text': text}
        return None

    def _make_structure_node(self, standard_uid: str, title_info: dict[str, str], block: dict[str, Any], parent_uid: str | None) -> dict[str, Any]:
        suffix = title_info['ref'].lower().replace('附录', 'appendix-')
        return {
            'node_uid': f"{standard_uid}:{title_info['node_type']}:{suffix}",
            'node_type': title_info['node_type'],
            'ref': title_info['ref'],
            'title': title_info['title'],
            'raw_text': title_info['raw_text'],
            'parent_uid': parent_uid,
            'page_idx': block['page_idx'],
            'bbox': block['bbox'],
            'source_block_id': block['block_id'],
        }

    def _make_clause_uid(self, standard_uid: str, body_kind: str, appendix: dict[str, Any] | None, clause_ref: str) -> str:
        scope = 'main' if body_kind == 'main' else f"appendix-{appendix['ref'].lower()}" if appendix else 'front'
        return f"{standard_uid}:{scope}:{clause_ref}"
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
        lines = [
            f'# Segmentation Report: {standard_uid}',
            '',
            f'- Artifact dir: `{artifact_dir}`',
            f'- Extraction mode requested: {metrics.get("extraction_mode_requested")}',
            f'- Extraction mode effective: {metrics.get("extraction_mode_effective")}',
            f'- Normalized blocks: {metrics["normalized_block_count"]}',
            f'- Structure nodes: {metrics["structure_node_count"]}',
            f'- Clauses: {metrics["clause_count"]}',
            f'- Main clauses: {metrics["main_clause_count"]}',
            f'- Appendix clauses: {metrics["appendix_clause_count"]}',
            f'- Requirements: {metrics["requirement_count"]}',
            f'- Clauses with requirements: {metrics["clauses_with_requirements"]}',
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

        lines.extend(['## Sample Clauses', ''])
        wanted_refs = {'3.1.1', '3.1.2', '3.1.3', '3.2.1', '3.2.2', '3.4.1', '3.4.10'}
        sample_clauses = [clause for clause in clauses if clause['body_kind'] == 'main' and clause['clause_ref'] in wanted_refs]
        if not sample_clauses:
            sample_clauses = clauses[:8]
        req_map: dict[str, list[dict[str, Any]]] = {}
        for requirement in requirements:
            req_map.setdefault(requirement['parent_clause_uid'], []).append(requirement)
        for clause in sample_clauses:
            lines.extend([
                f'### {clause["clause_ref"]}',
                '',
                f'- Section: {clause.get("section_ref")}',
                f'- Pages: {clause["source_page_span"][0]}-{clause["source_page_span"][1]}',
                f'- Segmentation confidence: {clause["segmentation_confidence"]}',
                f'- Requirement count: {clause.get("requirement_count", 0)}',
                f'- Concepts: {", ".join(clause.get("concepts", [])) or "n/a"}',
                '- Text:',
                '',
                clause['source_text_normalized'],
                '',
            ])
            for requirement in req_map.get(clause['clause_uid'], [])[:6]:
                lines.append(f"- {requirement['requirement_uid']}: {requirement['requirement_text']}")
            if req_map.get(clause['clause_uid']):
                lines.append('')
        return '\n'.join(lines).strip() + '\n'
