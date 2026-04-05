from __future__ import annotations

import concurrent.futures as futures
from dataclasses import dataclass, field
import json
import logging
import time
from typing import Any, Sequence

from adapters.llm_client import ResponseAPIError, ResponsesAPIClient
from core.config import AppConfig
from prompts import LLM_REQUIREMENT_EXTRACTION_SYSTEM_PROMPT, build_clause_extraction_prompt


logger = logging.getLogger(__name__)


@dataclass
class LLMExtractionResult:
    clause_items: dict[str, dict[str, Any]] = field(default_factory=dict)
    failed_clause_uids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class BatchExecutionResult:
    batch_index: int
    batch: list[dict[str, Any]]
    payload: dict[str, list[dict[str, Any]]] | None = None
    error: str | None = None
    retries_used: int = 0


class LLMGraphExtractionService:
    def __init__(self, config: AppConfig, client: ResponsesAPIClient) -> None:
        self.config = config
        self.client = client
        schema_path = self.config.schema_dir / "clause_graph_extraction.schema.json"
        self.schema = json.loads(schema_path.read_text(encoding="utf-8"))

    @property
    def enabled(self) -> bool:
        return self.client.enabled

    def extract_clause_batch(self, standard_uid: str, clauses: Sequence[dict[str, Any]]) -> Any:
        return self.client.create_structured_output(
            system_prompt=LLM_REQUIREMENT_EXTRACTION_SYSTEM_PROMPT,
            user_prompt=build_clause_extraction_prompt(standard_uid, clauses),
            schema_name="clause_graph_extraction_batch",
            schema=self.schema,
        )

    def extract_clauses(self, standard_uid: str, clauses: Sequence[dict[str, Any]]) -> LLMExtractionResult:
        if not clauses:
            return LLMExtractionResult(
                metrics={
                    "requested_clause_count": 0,
                    "batch_count": 0,
                    "successful_clause_count": 0,
                    "failed_clause_count": 0,
                    "retried_batch_count": 0,
                    "retry_attempt_count": 0,
                    "failed_batch_count": 0,
                }
            )

        if not self.enabled:
            warning = f"LLM extraction skipped because {self.config.llm.api_key_env} is not configured."
            logger.warning(warning)
            return LLMExtractionResult(
                failed_clause_uids=[clause["clause_uid"] for clause in clauses],
                warnings=[warning],
                metrics={
                    "requested_clause_count": len(clauses),
                    "batch_count": 0,
                    "successful_clause_count": 0,
                    "failed_clause_count": len(clauses),
                    "retried_batch_count": 0,
                    "retry_attempt_count": 0,
                    "failed_batch_count": 0,
                },
            )

        batch_size = max(1, self.config.llm.clause_batch_size)
        clause_items: dict[str, dict[str, Any]] = {}
        failed_clause_uids: list[str] = []
        warnings: list[str] = []
        batches = [list(clauses[index : index + batch_size]) for index in range(0, len(clauses), batch_size)]
        batch_results = self._run_batches(standard_uid, batches)

        for batch_result in batch_results:
            if batch_result.error is not None:
                failed_clause_uids.extend(clause["clause_uid"] for clause in batch_result.batch)
                warnings.append(f"batch_{batch_result.batch_index}: {batch_result.error}")
                continue

            payload = batch_result.payload or {"items": []}
            returned_items = payload.get("items") or []
            returned_by_uid = {
                item.get("clause_uid"): item
                for item in returned_items
                if isinstance(item, dict) and item.get("clause_uid")
            }
            for clause in batch_result.batch:
                item = returned_by_uid.get(clause["clause_uid"])
                if item is None:
                    failed_clause_uids.append(clause["clause_uid"])
                    warnings.append(f"batch_{batch_result.batch_index}: missing structured output for {clause['clause_uid']}")
                    continue
                clause_items[clause["clause_uid"]] = item

        retried_batch_count = sum(1 for result in batch_results if result.retries_used > 0)
        retry_attempt_count = sum(result.retries_used for result in batch_results)
        failed_batch_count = sum(1 for result in batch_results if result.error is not None)

        return LLMExtractionResult(
            clause_items=clause_items,
            failed_clause_uids=failed_clause_uids,
            warnings=warnings,
            metrics={
                "requested_clause_count": len(clauses),
                "batch_count": len(batches),
                "successful_clause_count": len(clause_items),
                "failed_clause_count": len(failed_clause_uids),
                "retried_batch_count": retried_batch_count,
                "retry_attempt_count": retry_attempt_count,
                "failed_batch_count": failed_batch_count,
                "batch_max_concurrency": max(1, min(self.config.llm.batch_max_concurrency, len(batches))),
            },
        )

    def _run_batches(self, standard_uid: str, batches: list[list[dict[str, Any]]]) -> list[BatchExecutionResult]:
        if not batches:
            return []

        max_concurrency = max(1, min(self.config.llm.batch_max_concurrency, len(batches)))
        logger.info(
            "Running %s LLM extraction batch(es) with concurrency=%s, max_retries=%s.",
            len(batches),
            max_concurrency,
            self.config.llm.batch_max_retries,
        )
        if max_concurrency == 1:
            return [
                self._execute_batch_with_retries(standard_uid=standard_uid, batch_index=batch_index, batch=batch)
                for batch_index, batch in enumerate(batches, start=1)
            ]

        results: list[BatchExecutionResult] = []
        with futures.ThreadPoolExecutor(max_workers=max_concurrency, thread_name_prefix="llm-batch") as executor:
            submitted = [
                executor.submit(self._execute_batch_with_retries, standard_uid=standard_uid, batch_index=batch_index, batch=batch)
                for batch_index, batch in enumerate(batches, start=1)
            ]
            for future in futures.as_completed(submitted):
                results.append(future.result())
        results.sort(key=lambda item: item.batch_index)
        return results

    def _execute_batch_with_retries(
        self,
        *,
        standard_uid: str,
        batch_index: int,
        batch: list[dict[str, Any]],
    ) -> BatchExecutionResult:
        max_retries = max(0, self.config.llm.batch_max_retries)
        max_attempts = max_retries + 1
        for attempt in range(1, max_attempts + 1):
            try:
                raw_payload = self.extract_clause_batch(standard_uid, batch)
                payload = self._normalize_batch_payload(raw_payload)
                if attempt > 1:
                    logger.info(
                        "LLM extraction batch %s succeeded on retry attempt %s/%s.",
                        batch_index,
                        attempt,
                        max_attempts,
                    )
                return BatchExecutionResult(
                    batch_index=batch_index,
                    batch=list(batch),
                    payload=payload,
                    retries_used=attempt - 1,
                )
            except ResponseAPIError as exc:
                if attempt >= max_attempts:
                    logger.warning(
                        "LLM extraction failed for batch %s after %s attempt(s): %s",
                        batch_index,
                        attempt,
                        exc,
                    )
                    return BatchExecutionResult(
                        batch_index=batch_index,
                        batch=list(batch),
                        error=str(exc),
                        retries_used=attempt - 1,
                    )
                logger.warning(
                    "Retrying LLM extraction batch %s after attempt %s/%s: %s",
                    batch_index,
                    attempt,
                    max_attempts,
                    exc,
                )
                self._sleep_before_retry(attempt)
            except Exception as exc:  # pragma: no cover - defensive path for API/runtime errors
                logger.exception(
                    "Unexpected error during LLM extraction batch %s attempt %s/%s",
                    batch_index,
                    attempt,
                    max_attempts,
                )
                return BatchExecutionResult(
                    batch_index=batch_index,
                    batch=list(batch),
                    error=str(exc),
                    retries_used=attempt - 1,
                )

        return BatchExecutionResult(
            batch_index=batch_index,
            batch=list(batch),
            error="Batch execution ended without result.",
            retries_used=max_retries,
        )

    def _sleep_before_retry(self, attempt: int) -> None:
        delay_seconds = max(0.0, self.config.llm.batch_retry_backoff_seconds) * attempt
        if delay_seconds > 0:
            time.sleep(delay_seconds)

    def _normalize_batch_payload(self, payload: Any) -> dict[str, list[dict[str, Any]]]:
        if isinstance(payload, dict):
            for key in ("items", "results", "clauses", "extracted_requirements"):
                value = payload.get(key)
                if isinstance(value, list):
                    if key != "items":
                        logger.info("Using alternate structured output key '%s' for LLM extraction payload.", key)
                    return {"items": self._normalize_clause_items(value)}
                if isinstance(value, dict):
                    if key != "items":
                        logger.info("Using alternate structured output key '%s' (single object) for LLM extraction payload.", key)
                    return {"items": self._normalize_clause_items([value])}

            if payload.get("clause_uid"):
                return {"items": self._normalize_clause_items([payload])}

            data = payload.get("data")
            if isinstance(data, list):
                return {"items": self._normalize_clause_items(data)}
            if isinstance(data, dict):
                for key in ("items", "results", "clauses", "extracted_requirements"):
                    value = data.get(key)
                    if isinstance(value, list):
                        logger.info("Using nested structured output key 'data.%s' for LLM extraction payload.", key)
                        return {"items": self._normalize_clause_items(value)}
                    if isinstance(value, dict):
                        logger.info("Using nested structured output key 'data.%s' (single object) for LLM extraction payload.", key)
                        return {"items": self._normalize_clause_items([value])}

            preview = self._payload_preview(payload)
            raise ResponseAPIError(
                f"Unsupported structured output object shape. keys={sorted(payload.keys())} payload_preview={preview}"
            )

        if isinstance(payload, list):
            return {"items": self._normalize_clause_items(payload)}

        raise ResponseAPIError(
            f"Unsupported structured output root type: {type(payload).__name__} payload_preview={self._payload_preview(payload)}"
        )

    def _normalize_clause_items(self, items: list[Any]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            concepts = self._coerce_string_list(item.get("concepts") or item.get("domain_tags"))
            requirements_value = item.get("requirements") or item.get("items") or []
            if isinstance(requirements_value, dict):
                requirements_value = [requirements_value]
            if not isinstance(requirements_value, list):
                requirements_value = []
            normalized.append(
                {
                    "clause_uid": self._coerce_string(item.get("clause_uid")),
                    "clause_ref": self._coerce_string(item.get("clause_ref")),
                    "clause_summary": self._coerce_optional_string(item.get("clause_summary") or item.get("summary")),
                    "concepts": concepts,
                    "requirements": [
                        self._normalize_requirement_item(candidate)
                        for candidate in requirements_value
                        if isinstance(candidate, dict)
                    ],
                }
            )
        return normalized

    def _normalize_requirement_item(self, item: dict[str, Any]) -> dict[str, Any]:
        cited_targets = item.get("cited_targets") or []
        if isinstance(cited_targets, dict):
            cited_targets = [cited_targets]
        elif not isinstance(cited_targets, list):
            cited_targets = []

        normalized_targets: list[dict[str, Any]] = []
        for target in cited_targets:
            if isinstance(target, dict) and target.get("standard_code"):
                normalized_targets.append(
                    {
                        "standard_code": self._coerce_string(target.get("standard_code")),
                        "clause_ref": self._coerce_optional_string(target.get("clause_ref")),
                        "citation_type": self._coerce_string(target.get("citation_type") or "unknown"),
                    }
                )
            elif isinstance(target, str) and target.strip():
                normalized_targets.append(
                    {
                        "standard_code": target.strip(),
                        "clause_ref": None,
                        "citation_type": "unknown",
                    }
                )

        return {
            "requirement_text": self._coerce_string(item.get("requirement_text")),
            "modality": self._coerce_string(item.get("modality") or "must"),
            "subject": self._coerce_optional_string(item.get("subject")),
            "action": self._coerce_string_list(item.get("action")),
            "object": self._coerce_string_list(item.get("object")),
            "applicability_rule": self._coerce_optional_string(item.get("applicability_rule")),
            "judgement_criteria": self._coerce_string_list(item.get("judgement_criteria")),
            "evidence_expected": self._coerce_string_list(item.get("evidence_expected")),
            "domain_tags": self._coerce_string_list(item.get("domain_tags") or item.get("concepts")),
            "cited_targets": normalized_targets,
            "confidence": item.get("confidence"),
        }

    def _coerce_string(self, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def _coerce_optional_string(self, value: Any) -> str | None:
        text = self._coerce_string(value)
        return text or None

    def _coerce_string_list(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            text = value.strip()
            return [text] if text else []
        if isinstance(value, list):
            result: list[str] = []
            for item in value:
                text = self._coerce_string(item)
                if text:
                    result.append(text)
            return result
        if isinstance(value, tuple):
            return self._coerce_string_list(list(value))
        text = self._coerce_string(value)
        return [text] if text else []

    def _payload_preview(self, payload: Any, max_chars: int = 3000) -> str:
        try:
            preview = json.dumps(payload, ensure_ascii=False)
        except TypeError:
            preview = repr(payload)
        if len(preview) > max_chars:
            preview = preview[:max_chars] + "...<truncated>"
        return preview

