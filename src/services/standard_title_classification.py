from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
import logging
from typing import Any, Sequence

from adapters.llm_client import ResponseAPIError, ResponsesAPIClient
from core.config import AppConfig
from prompts import (
    LLM_STANDARD_TITLE_CLASSIFICATION_SYSTEM_PROMPT,
    build_standard_title_classification_prompt,
)


logger = logging.getLogger(__name__)

STANDARD_TITLE_BATCH_SIZE = 12
STANDARD_TITLE_CONTEXT_SIZE = 4
STANDARD_TITLE_LABELS = {"clause", "section", "reference_standard", "chapter", "appendix", "none"}


@dataclass
class StandardTitleClassificationResult:
    items: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


class StandardTitleClassificationService:
    def __init__(self, config: AppConfig, client: ResponsesAPIClient) -> None:
        self.config = config
        self.client = client
        schema_path = self.config.schema_dir / "standard_title_classification.schema.json"
        self.schema = json.loads(schema_path.read_text(encoding="utf-8"))

    @property
    def enabled(self) -> bool:
        return self.config.llm.enabled

    def classify_titles(
        self,
        *,
        standard_uid: str,
        title_inventory: Sequence[dict[str, Any]],
    ) -> StandardTitleClassificationResult:
        if not title_inventory:
            return StandardTitleClassificationResult(
                metrics={
                    "title_classifier_requested_count": 0,
                    "title_classifier_batch_count": 0,
                    "title_classifier_successful_count": 0,
                    "title_classifier_label_counts": {},
                }
            )
        if not self.client.enabled:
            raise ResponseAPIError(
                f"Standard title classification is enabled but {self.config.llm.api_key_env} is not configured."
            )

        items: list[dict[str, Any]] = []
        warnings: list[str] = []
        previous_items: list[dict[str, Any]] = []
        batches = [
            list(title_inventory[index : index + STANDARD_TITLE_BATCH_SIZE])
            for index in range(0, len(title_inventory), STANDARD_TITLE_BATCH_SIZE)
        ]

        for batch_index, batch in enumerate(batches, start=1):
            result = self.client.create_structured_output(
                system_prompt=LLM_STANDARD_TITLE_CLASSIFICATION_SYSTEM_PROMPT,
                user_prompt=build_standard_title_classification_prompt(
                    standard_uid=standard_uid,
                    previous_titles=previous_items[-STANDARD_TITLE_CONTEXT_SIZE:],
                    current_titles=batch,
                ),
                schema_name="standard_title_classification_batch",
                schema=self.schema,
            )
            normalized_items = self._normalize_items(result)
            normalized_by_id = {item["title_id"]: item for item in normalized_items if item.get("title_id")}
            missing = [item["title_id"] for item in batch if item["title_id"] not in normalized_by_id]
            if missing:
                raise ResponseAPIError(
                    f"Standard title classification batch {batch_index} did not return all title ids: {', '.join(missing[:8])}"
                )
            for title in batch:
                item = normalized_by_id[title["title_id"]]
                merged = {**title, **item}
                items.append(merged)
                previous_items.append(merged)

        label_counts = Counter(item["label"] for item in items)
        return StandardTitleClassificationResult(
            items=items,
            warnings=warnings,
            metrics={
                "title_classifier_requested_count": len(title_inventory),
                "title_classifier_batch_count": len(batches),
                "title_classifier_successful_count": len(items),
                "title_classifier_label_counts": dict(sorted(label_counts.items())),
            },
        )

    def _normalize_items(self, payload: Any) -> list[dict[str, Any]]:
        items: Any = None
        if isinstance(payload, dict):
            for key in ("items", "results", "titles", "classifications"):
                value = payload.get(key)
                if isinstance(value, list):
                    items = value
                    break
            if items is None and isinstance(payload.get("data"), dict):
                for key in ("items", "results", "titles", "classifications"):
                    value = payload["data"].get(key)
                    if isinstance(value, list):
                        items = value
                        break
            if items is None and payload.get("title_id"):
                items = [payload]
            if items is None and payload and all(isinstance(key, str) for key in payload):
                items = [
                    {
                        "title_id": key,
                        "label": value if isinstance(value, str) else str((value or {}).get("label") or ""),
                        "confidence": 0.0 if isinstance(value, str) else float((value or {}).get("confidence") or 0.0),
                        "rationale": "" if isinstance(value, str) else str((value or {}).get("rationale") or ""),
                    }
                    for key, value in payload.items()
                ]
        elif isinstance(payload, list):
            items = payload

        if not isinstance(items, list):
            raise ResponseAPIError(f"Unsupported standard title classification payload: {payload!r}")

        normalized: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict) or not item.get("title_id"):
                continue
            label = str(item.get("label") or item.get("category") or "").strip().lower()
            if label not in STANDARD_TITLE_LABELS:
                continue
            confidence = item.get("confidence")
            if isinstance(confidence, int):
                confidence = float(confidence)
            if not isinstance(confidence, float):
                confidence = 0.0
            normalized.append(
                {
                    "title_id": str(item.get("title_id")),
                    "label": label,
                    "confidence": max(0.0, min(1.0, confidence)),
                    "rationale": str(item.get("rationale") or item.get("reason") or "").strip(),
                }
            )
        return normalized
