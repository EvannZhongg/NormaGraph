from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from typing import Any, Sequence

from adapters.llm_client import ResponseAPIError, ResponsesAPIClient
from core.config import AppConfig
from prompts import LLM_REPORT_SECTION_SUMMARY_SYSTEM_PROMPT, build_report_section_summary_prompt


logger = logging.getLogger(__name__)


@dataclass
class ReportSectionSummaryResult:
    section_items: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


class ReportSectionSummaryService:
    MAX_PROMPT_UNITS = 80
    MAX_PROMPT_TOTAL_CHARS = 18000
    MAX_PROMPT_UNIT_CHARS = 1200

    def __init__(self, config: AppConfig, client: ResponsesAPIClient) -> None:
        self.config = config
        self.client = client
        schema_path = self.config.schema_dir / "report_section_summary.schema.json"
        self.schema = json.loads(schema_path.read_text(encoding="utf-8"))

    @property
    def enabled(self) -> bool:
        return self.client.enabled

    def summarize_sections(
        self,
        *,
        document_id: str,
        sections: Sequence[dict[str, Any]],
        report_units: Sequence[dict[str, Any]],
    ) -> ReportSectionSummaryResult:
        eligible_sections = [
            section
            for section in sections
            if section.get("section_uid") and section.get("section_kind") not in {"toc"}
        ]
        if not eligible_sections:
            return ReportSectionSummaryResult(
                metrics={
                    "report_section_summary_status": "skipped_no_sections",
                    "report_section_summary_discovered_count": 0,
                    "report_section_summary_requested_count": 0,
                    "report_section_summary_completed_count": 0,
                    "report_section_summary_failed_count": 0,
                    "report_section_summary_skipped_count": 0,
                }
            )

        if not self.enabled:
            warning = f"Report section summary generation skipped because {self.config.llm.api_key_env} is not configured."
            logger.warning(warning)
            return ReportSectionSummaryResult(
                warnings=[warning],
                metrics={
                    "report_section_summary_status": f"missing_api_key:{self.config.llm.api_key_env}",
                    "report_section_summary_discovered_count": len(eligible_sections),
                    "report_section_summary_requested_count": 0,
                    "report_section_summary_completed_count": 0,
                    "report_section_summary_failed_count": 0,
                    "report_section_summary_skipped_count": len(eligible_sections),
                },
            )

        units_by_uid = {
            str(unit.get("unit_uid") or ""): unit
            for unit in report_units
            if unit.get("unit_uid")
        }

        section_items: dict[str, dict[str, Any]] = {}
        warnings: list[str] = []
        requested_count = 0
        completed_count = 0
        failed_count = 0
        skipped_count = 0

        for section in eligible_sections:
            section_uid = str(section.get("section_uid") or "")
            section_units = self._section_units(section, units_by_uid)
            if not section_units:
                skipped_count += 1
                warnings.append(f"report_section_summary_skipped:{section_uid}:no_units")
                continue

            prompt_units, was_truncated = self._build_prompt_units(section_units)
            if not prompt_units:
                skipped_count += 1
                warnings.append(f"report_section_summary_skipped:{section_uid}:empty_prompt")
                continue

            requested_count += 1
            try:
                payload = self.client.create_structured_output(
                    system_prompt=LLM_REPORT_SECTION_SUMMARY_SYSTEM_PROMPT,
                    user_prompt=build_report_section_summary_prompt(document_id, section, prompt_units),
                    schema_name="report_section_summary",
                    schema=self.schema,
                )
                item = self._normalize_summary_payload(payload, prompt_units)
            except ResponseAPIError as exc:
                failed_count += 1
                warnings.append(f"report_section_summary_failed:{section_uid}:{exc}")
                continue

            summary_text = self._format_summary_text(section_units, item)
            if not summary_text:
                failed_count += 1
                warnings.append(f"report_section_summary_failed:{section_uid}:empty_summary")
                continue

            section_items[section_uid] = {
                "summary": summary_text,
                "summary_overall": item["overall_summary"],
                "unit_summaries": item["unit_summaries"],
                "summary_source_unit_count": len(section_units),
                "summary_source_truncated": was_truncated,
            }
            completed_count += 1

        status = "completed"
        if requested_count == 0:
            status = "skipped_no_eligible_sections"
        elif completed_count == 0 and failed_count > 0:
            status = "failed"
        elif failed_count > 0:
            status = "partial"

        return ReportSectionSummaryResult(
            section_items=section_items,
            warnings=warnings,
            metrics={
                "report_section_summary_status": status,
                "report_section_summary_discovered_count": len(eligible_sections),
                "report_section_summary_requested_count": requested_count,
                "report_section_summary_completed_count": completed_count,
                "report_section_summary_failed_count": failed_count,
                "report_section_summary_skipped_count": skipped_count,
            },
        )

    def _section_units(
        self,
        section: dict[str, Any],
        units_by_uid: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        member_uids = [str(uid) for uid in section.get("member_uids") or [] if uid]
        units = [units_by_uid[uid] for uid in member_uids if uid in units_by_uid]
        return sorted(units, key=lambda item: (int(item.get("order_index") or 0), str(item.get("unit_uid") or "")))

    def _build_prompt_units(self, units: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
        prompt_units: list[dict[str, Any]] = []
        total_chars = 0
        truncated = False

        for index, unit in enumerate(units):
            if len(prompt_units) >= self.MAX_PROMPT_UNITS:
                truncated = True
                break

            raw_text = self._unit_text(unit)
            if not raw_text:
                continue

            unit_text = raw_text[: self.MAX_PROMPT_UNIT_CHARS].strip()
            if len(unit_text) < len(raw_text):
                truncated = True

            if total_chars >= self.MAX_PROMPT_TOTAL_CHARS:
                truncated = True
                break

            remaining_chars = self.MAX_PROMPT_TOTAL_CHARS - total_chars
            if len(unit_text) > remaining_chars:
                unit_text = unit_text[:remaining_chars].strip()
                truncated = True
            if not unit_text:
                break

            prompt_units.append(
                {
                    "unit_uid": unit.get("unit_uid"),
                    "title": unit.get("title"),
                    "unit_type": unit.get("unit_type"),
                    "local_heading_path": unit.get("local_heading_path") or [],
                    "source_page_span": unit.get("source_page_span") or [],
                    "text_for_summary": unit_text,
                }
            )
            total_chars += len(unit_text)

            if index < len(units) - 1 and total_chars >= self.MAX_PROMPT_TOTAL_CHARS:
                truncated = True
                break

        return prompt_units, truncated

    def _unit_text(self, unit: dict[str, Any]) -> str:
        if unit.get("unit_type") == "table":
            value = unit.get("html") or unit.get("text_normalized") or unit.get("text")
        else:
            value = unit.get("text_normalized") or unit.get("text") or unit.get("html")
        return str(value or "").strip()

    def _normalize_summary_payload(
        self,
        payload: Any,
        prompt_units: Sequence[dict[str, Any]],
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ResponseAPIError(f"Report section summary returned unsupported payload type: {type(payload).__name__}.")

        overall_summary = str(payload.get("overall_summary") or payload.get("summary") or "").strip()
        if not overall_summary:
            raise ResponseAPIError("Report section summary response did not include a non-empty overall_summary.")

        prompt_unit_ids = [str(unit.get("unit_uid") or "") for unit in prompt_units if unit.get("unit_uid")]
        prompt_unit_id_set = set(prompt_unit_ids)
        unit_summaries: list[dict[str, str]] = []
        seen_ids: set[str] = set()
        raw_unit_summaries = payload.get("unit_summaries") or []
        if isinstance(raw_unit_summaries, list):
            for raw_item in raw_unit_summaries:
                if not isinstance(raw_item, dict):
                    continue
                unit_uid = str(raw_item.get("unit_uid") or "").strip()
                summary = str(raw_item.get("summary") or "").strip()
                if not unit_uid or unit_uid not in prompt_unit_id_set or not summary or unit_uid in seen_ids:
                    continue
                seen_ids.add(unit_uid)
                unit_summaries.append({"unit_uid": unit_uid, "summary": summary})

        if len(unit_summaries) < len(prompt_unit_ids):
            missing_ids = [unit_uid for unit_uid in prompt_unit_ids if unit_uid not in seen_ids]
            raise ResponseAPIError(
                f"Report section summary response missed {len(missing_ids)} unit summary item(s): {', '.join(missing_ids[:5])}."
            )

        return {
            "overall_summary": overall_summary,
            "unit_summaries": unit_summaries,
        }

    def _format_summary_text(self, section_units: Sequence[dict[str, Any]], item: dict[str, Any]) -> str:
        unit_titles = {
            str(unit.get("unit_uid") or ""): str(unit.get("title") or unit.get("unit_uid") or "").strip()
            for unit in section_units
        }
        lines = [
            f"总概括：{item['overall_summary']}",
            "",
            "单元概括：",
        ]
        for unit_summary in item["unit_summaries"]:
            unit_uid = str(unit_summary.get("unit_uid") or "")
            title = unit_titles.get(unit_uid) or unit_uid
            lines.append(f"- {title}: {unit_summary.get('summary')}")
        return "\n".join(line.rstrip() for line in lines).strip()
