from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from typing import Any, Sequence

from adapters.llm_client import ResponseAPIError, ResponsesAPIClient
from core.config import AppConfig
from prompts import LLM_REPORT_TITLE_PLANNING_SYSTEM_PROMPT, build_report_title_planning_prompt


logger = logging.getLogger(__name__)

REPORT_TITLE_BATCH_SIZE = 36
REPORT_TITLE_CONTEXT_SIZE = 8

TITLE_ROLE_TO_SPEC = {
    'front_matter': {'section_kind': 'front_matter', 'hierarchy_level': 1, 'is_structural': False},
    'toc': {'section_kind': 'toc', 'hierarchy_level': 1, 'is_structural': False},
    'chapter': {'section_kind': 'chapter', 'hierarchy_level': 1, 'is_structural': True},
    'section': {'section_kind': 'section', 'hierarchy_level': 2, 'is_structural': True},
    'subsection': {'section_kind': 'subsection', 'hierarchy_level': 3, 'is_structural': True},
    'topic': {'section_kind': 'topic', 'hierarchy_level': 4, 'is_structural': False},
    'subtopic': {'section_kind': 'subtopic', 'hierarchy_level': 5, 'is_structural': False},
    'appendix': {'section_kind': 'appendix', 'hierarchy_level': 1, 'is_structural': True},
    'ignore': {'section_kind': 'ignore', 'hierarchy_level': 4, 'is_structural': False},
}


@dataclass
class ReportTitlePlanResult:
    items: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


class ReportOutlinePlannerService:
    def __init__(self, config: AppConfig, client: ResponsesAPIClient) -> None:
        self.config = config
        self.client = client
        schema_path = self.config.schema_dir / 'report_title_outline.schema.json'
        self.schema = json.loads(schema_path.read_text(encoding='utf-8'))

    @property
    def enabled(self) -> bool:
        return self.client.enabled

    def plan_titles(
        self,
        document_id: str,
        title_inventory: Sequence[dict[str, Any]],
    ) -> ReportTitlePlanResult:
        if not title_inventory:
            return ReportTitlePlanResult(
                metrics={
                    'planner_requested_title_count': 0,
                    'planner_batch_count': 0,
                    'planner_successful_title_count': 0,
                    'planner_failed_batch_count': 0,
                }
            )

        if not self.enabled:
            return ReportTitlePlanResult(
                warnings=[f'Title planner skipped because {self.config.llm.api_key_env} is not configured.'],
                metrics={
                    'planner_requested_title_count': len(title_inventory),
                    'planner_batch_count': 0,
                    'planner_successful_title_count': 0,
                    'planner_failed_batch_count': 0,
                },
            )

        items: list[dict[str, Any]] = []
        warnings: list[str] = []
        failed_batch_count = 0
        previous_items: list[dict[str, Any]] = []
        batches = [
            list(title_inventory[index : index + REPORT_TITLE_BATCH_SIZE])
            for index in range(0, len(title_inventory), REPORT_TITLE_BATCH_SIZE)
        ]

        for batch_index, batch in enumerate(batches, start=1):
            try:
                result = self.client.create_structured_output(
                    system_prompt=LLM_REPORT_TITLE_PLANNING_SYSTEM_PROMPT,
                    user_prompt=build_report_title_planning_prompt(
                        document_id=document_id,
                        previous_titles=previous_items[-REPORT_TITLE_CONTEXT_SIZE:],
                        current_titles=batch,
                    ),
                    schema_name='report_title_outline_batch',
                    schema=self.schema,
                )
            except ResponseAPIError as exc:
                failed_batch_count += 1
                message = f'batch_{batch_index}: {exc}'
                logger.warning('LLM title planning failed for %s: %s', document_id, message)
                warnings.append(message)
                break
            except Exception as exc:  # pragma: no cover - defensive path
                failed_batch_count += 1
                message = f'batch_{batch_index}: {exc}'
                logger.exception('Unexpected title planning error for %s', document_id)
                warnings.append(message)
                break

            normalized_items = self._normalize_items(result)
            normalized_by_id = {
                item['title_id']: item
                for item in normalized_items
                if item.get('title_id')
            }
            if len(normalized_by_id) < len(batch):
                failed_batch_count += 1
                warnings.append(
                    f'batch_{batch_index}: returned {len(normalized_by_id)}/{len(batch)} items; falling back to heuristic for this and remaining batches.'
                )
                break
            for title in batch:
                item = normalized_by_id.get(title['title_id'])
                planned_item = {
                    **title,
                    **item,
                    'planner_source': 'llm',
                }
                items.append(planned_item)
                previous_items.append(planned_item)

            result_warnings = result.get('warnings') if isinstance(result, dict) else []
            for warning in result_warnings or []:
                text = str(warning).strip()
                if text:
                    warnings.append(f'batch_{batch_index}: {text}')

        return ReportTitlePlanResult(
            items=items,
            warnings=warnings,
            metrics={
                'planner_requested_title_count': len(title_inventory),
                'planner_batch_count': len(batches),
                'planner_successful_title_count': len(items),
                'planner_failed_batch_count': failed_batch_count,
            },
        )

    def _normalize_items(self, payload: Any) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]]
        if isinstance(payload, dict):
            raw_items = payload.get('items') or payload.get('results') or []
            candidates = raw_items if isinstance(raw_items, list) else []
        elif isinstance(payload, list):
            candidates = payload
        else:
            raise ResponseAPIError(f'Unsupported title planner payload type: {type(payload).__name__}')

        items: list[dict[str, Any]] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            title_id = str(candidate.get('title_id') or '').strip()
            role = str(candidate.get('role') or '').strip().lower()
            if not title_id or role not in TITLE_ROLE_TO_SPEC:
                continue
            spec = TITLE_ROLE_TO_SPEC[role]
            confidence = candidate.get('confidence')
            if isinstance(confidence, int):
                confidence = float(confidence)
            if not isinstance(confidence, float):
                confidence = None
            rationale = candidate.get('rationale')
            rationale = str(rationale).strip() if rationale is not None else None
            if rationale == '':
                rationale = None
            ref = candidate.get('ref')
            ref = str(ref).strip() if ref is not None else None
            if ref == '':
                ref = None
            items.append(
                {
                    'title_id': title_id,
                    'role': role,
                    'section_kind': spec['section_kind'],
                    'hierarchy_level': spec['hierarchy_level'],
                    'is_structural': spec['is_structural'],
                    'ref': ref,
                    'confidence': confidence,
                    'rationale': rationale,
                }
            )
        return items
