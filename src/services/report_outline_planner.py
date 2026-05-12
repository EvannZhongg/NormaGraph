from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
import logging
import sys
from typing import Any, Sequence

from adapters.llm_client import (
    ResponseAPIError,
    ResponseAPIOutputError,
    ResponseAPIRequestError,
    ResponsesAPIClient,
)
from core.config import AppConfig
from prompts import LLM_REPORT_TITLE_PLANNING_SYSTEM_PROMPT, build_report_title_planning_prompt


logger = logging.getLogger(__name__)

REPORT_TITLE_BATCH_SIZE = 24
REPORT_TITLE_CONTEXT_SIZE = 10
REPORT_TITLE_MIN_RETRY_BATCH_SIZE = 6

TITLE_ROLE_TO_SPEC = {
    'toc': {'section_kind': 'toc', 'hierarchy_level': 1, 'is_structural': False},
    'chapter': {'section_kind': 'chapter', 'hierarchy_level': 1, 'is_structural': True},
    'unit': {'section_kind': 'unit', 'hierarchy_level': 2, 'is_structural': False},
    'appendix': {'section_kind': 'appendix', 'hierarchy_level': 1, 'is_structural': True},
    'ignore': {'section_kind': 'ignore', 'hierarchy_level': 0, 'is_structural': False},
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
            batch_items, batch_warnings, batch_failed_count = self._plan_batch_with_retries(
                document_id=document_id,
                batch_index=batch_index,
                batch=batch,
                previous_items=previous_items,
            )
            warnings.extend(batch_warnings)
            failed_batch_count += batch_failed_count
            items.extend(batch_items)
            previous_items.extend(batch_items)

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

    def _plan_batch_with_retries(
        self,
        *,
        document_id: str,
        batch_index: int,
        batch: list[dict[str, Any]],
        previous_items: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str], int]:
        warnings: list[str] = []
        try:
            return self._plan_exact_batch(
                document_id=document_id,
                batch_index=batch_index,
                batch=batch,
                previous_items=previous_items,
            ), warnings, 0
        except ResponseAPIRequestError as exc:
            message = f'batch_{batch_index}: {exc}'
            logger.warning('LLM title planning request failed for %s: %s', document_id, message)
            self._print_batch_failure(document_id=document_id, batch_index=batch_index, batch=batch, error=message)
            raise ResponseAPIRequestError(message) from exc
        except ResponseAPIOutputError as exc:
            if len(batch) <= REPORT_TITLE_MIN_RETRY_BATCH_SIZE:
                message = f'batch_{batch_index}: {exc}'
                logger.warning('LLM title planning failed for %s: %s', document_id, message)
                self._print_batch_failure(document_id=document_id, batch_index=batch_index, batch=batch, error=message)
                raise ResponseAPIOutputError(message, raw_text=exc.raw_text, payload=exc.payload) from exc

            midpoint = len(batch) // 2
            warnings.append(f'batch_{batch_index}: {exc}; retrying as {midpoint}+{len(batch) - midpoint} titles.')
            left_items, left_warnings, left_failed = self._plan_batch_with_retries(
                document_id=document_id,
                batch_index=batch_index,
                batch=batch[:midpoint],
                previous_items=previous_items,
            )
            warnings.extend(left_warnings)

            merged_previous = [*previous_items, *left_items]
            right_items, right_warnings, right_failed = self._plan_batch_with_retries(
                document_id=document_id,
                batch_index=batch_index,
                batch=batch[midpoint:],
                previous_items=merged_previous,
            )
            warnings.extend(right_warnings)
            return [*left_items, *right_items], warnings, left_failed + right_failed

    def _plan_exact_batch(
        self,
        *,
        document_id: str,
        batch_index: int,
        batch: list[dict[str, Any]],
        previous_items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
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
        except ResponseAPIRequestError as exc:
            raise ResponseAPIRequestError(f'batch_{batch_index}: {exc}') from exc
        except ResponseAPIOutputError as exc:
            self._print_raw_output_failure(
                document_id=document_id,
                batch_index=batch_index,
                batch=batch,
                error=str(exc),
                raw_text=exc.raw_text,
                payload=exc.payload,
            )
            raise ResponseAPIOutputError(f'batch_{batch_index}: {exc}', raw_text=exc.raw_text, payload=exc.payload) from exc
        except Exception as exc:  # pragma: no cover - defensive path
            logger.exception('Unexpected title planning error for %s', document_id)
            raise ResponseAPIRequestError(f'batch_{batch_index}: {exc}') from exc

        try:
            normalized_items = self._normalize_items(result)
        except ResponseAPIOutputError as exc:
            payload = exc.payload if exc.payload is not None else result
            self._print_raw_output_failure(
                document_id=document_id,
                batch_index=batch_index,
                batch=batch,
                error=str(exc),
                raw_text=exc.raw_text,
                payload=payload,
            )
            raise ResponseAPIOutputError(
                f'batch_{batch_index}: {exc}',
                raw_text=exc.raw_text,
                payload=payload,
            ) from exc
        normalized_by_id = {
            item['title_id']: item
            for item in normalized_items
            if item.get('title_id')
        }
        if len(normalized_by_id) < len(batch):
            self._print_payload_failure(
                document_id=document_id,
                batch_index=batch_index,
                batch=batch,
                payload=result,
                normalized_items=normalized_items,
            )
            raise ResponseAPIOutputError(f'batch_{batch_index}: returned {len(normalized_by_id)}/{len(batch)} items')

        planned_items: list[dict[str, Any]] = []
        for title in batch:
            item = normalized_by_id[title['title_id']]
            planned_items.append(
                {
                    **title,
                    **item,
                    'planner_source': 'llm',
                }
            )
        return planned_items

    def _normalize_items(self, payload: Any) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]]
        if isinstance(payload, dict):
            raw_items = (
                payload.get('items')
                or payload.get('results')
                or payload.get('title_plan')
                or payload.get('plan')
            )
            if isinstance(raw_items, list):
                candidates = raw_items
            else:
                candidates = self._items_from_role_mapping(payload)
        elif isinstance(payload, list):
            candidates = payload
        else:
            raise ResponseAPIOutputError(f'Unsupported title planner payload type: {type(payload).__name__}', payload=payload)

        items: list[dict[str, Any]] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            title_id = str(candidate.get('title_id') or '').strip()
            role = str(candidate.get('role') or '').strip().lower()
            if not title_id or role not in TITLE_ROLE_TO_SPEC:
                continue
            spec = TITLE_ROLE_TO_SPEC[role]
            ref = candidate.get('ref')
            ref = str(ref).strip() if ref is not None else None
            if ref == '':
                ref = None
            hierarchy_level = spec['hierarchy_level']
            if role == 'unit' and ref and '.' in ref:
                hierarchy_level = 1 + len([part for part in ref.split('.') if part])
            items.append(
                {
                    'title_id': title_id,
                    'role': role,
                    'section_kind': spec['section_kind'],
                    'hierarchy_level': hierarchy_level,
                    'is_structural': spec['is_structural'],
                    'ref': ref,
                    'confidence': None,
                    'rationale': None,
                }
            )
        return items

    def _items_from_role_mapping(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for title_id, value in payload.items():
            title_id = str(title_id or '').strip()
            if not title_id:
                continue
            if isinstance(value, str):
                items.append({'title_id': title_id, 'role': value, 'ref': None})
            elif isinstance(value, dict):
                items.append({'title_id': title_id, **value})
        return items

    def _print_batch_failure(
        self,
        *,
        document_id: str,
        batch_index: int,
        batch: list[dict[str, Any]],
        error: str,
    ) -> None:
        print(
            f'[report-title-planner] FAILED document={document_id} batch={batch_index} size={len(batch)} error={error}',
            file=sys.stderr,
        )
        print('[report-title-planner] batch titles:', file=sys.stderr)
        for title in batch:
            print(
                f"  - {title.get('title_id')} idx={title.get('title_index')} page={title.get('page_idx')} "
                f"role={title.get('page_role')} text={title.get('text')!r}",
                file=sys.stderr,
            )

    def _print_payload_failure(
        self,
        *,
        document_id: str,
        batch_index: int,
        batch: list[dict[str, Any]],
        payload: Any,
        normalized_items: list[dict[str, Any]],
    ) -> None:
        expected_ids = [str(title.get('title_id') or '') for title in batch]
        returned_ids = [str(item.get('title_id') or '') for item in normalized_items]
        missing_ids = [title_id for title_id in expected_ids if title_id not in set(returned_ids)]
        raw_items: list[Any] = []
        if isinstance(payload, dict):
            value = (
                payload.get('items')
                or payload.get('results')
                or payload.get('title_plan')
                or payload.get('plan')
                or []
            )
            raw_items = value if isinstance(value, list) else []
        elif isinstance(payload, list):
            raw_items = payload
        role_counts = Counter(
            str(item.get('role') or '<missing>')
            for item in raw_items
            if isinstance(item, dict)
        )
        invalid_items = [
            {
                'title_id': item.get('title_id'),
                'role': item.get('role'),
                'ref': item.get('ref'),
            }
            for item in raw_items
            if isinstance(item, dict)
            and (
                not str(item.get('title_id') or '').strip()
                or str(item.get('role') or '').strip().lower() not in TITLE_ROLE_TO_SPEC
            )
        ]
        duplicate_ids = sorted(
            title_id
            for title_id, count in Counter(returned_ids).items()
            if title_id and count > 1
        )
        missing_titles = [
            {
                'title_id': title.get('title_id'),
                'title_index': title.get('title_index'),
                'page_idx': title.get('page_idx'),
                'text': title.get('text'),
            }
            for title in batch
            if str(title.get('title_id') or '') in set(missing_ids)
        ]
        try:
            payload_preview = json.dumps(payload, ensure_ascii=False)[:3000]
        except TypeError:
            payload_preview = repr(payload)[:3000]

        print(
            f'[report-title-planner] OUTPUT ERROR document={document_id} batch={batch_index} '
            f'expected={len(batch)} normalized={len(normalized_items)} raw_items={len(raw_items)}',
            file=sys.stderr,
        )
        print(f'[report-title-planner] role_counts={dict(role_counts)}', file=sys.stderr)
        print(f'[report-title-planner] invalid_items={json.dumps(invalid_items[:20], ensure_ascii=False)}', file=sys.stderr)
        print(f'[report-title-planner] duplicate_ids={json.dumps(duplicate_ids[:20], ensure_ascii=False)}', file=sys.stderr)
        print(f'[report-title-planner] missing_titles={json.dumps(missing_titles, ensure_ascii=False)}', file=sys.stderr)
        print(f'[report-title-planner] payload_preview={payload_preview}', file=sys.stderr)

    def _print_raw_output_failure(
        self,
        *,
        document_id: str,
        batch_index: int,
        batch: list[dict[str, Any]],
        error: str,
        raw_text: str | None,
        payload: Any | None,
    ) -> None:
        print(
            f'[report-title-planner] RAW OUTPUT ERROR document={document_id} batch={batch_index} '
            f'size={len(batch)} error={error}',
            file=sys.stderr,
        )
        print('[report-title-planner] batch titles:', file=sys.stderr)
        for title in batch:
            print(
                f"  - {title.get('title_id')} idx={title.get('title_index')} page={title.get('page_idx')} "
                f"role={title.get('page_role')} text={title.get('text')!r}",
                file=sys.stderr,
            )
        if raw_text is not None:
            print(f'[report-title-planner] raw_text_preview={raw_text[:3000]}', file=sys.stderr)
        if payload is not None:
            try:
                payload_preview = json.dumps(payload, ensure_ascii=False)[:3000]
            except TypeError:
                payload_preview = repr(payload)[:3000]
            print(f'[report-title-planner] response_payload_preview={payload_preview}', file=sys.stderr)
