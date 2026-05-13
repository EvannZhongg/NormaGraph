from __future__ import annotations

from dataclasses import dataclass
import logging
import re
import time
from typing import Any

from adapters.llm_client import ResponseAPIError, ResponsesAPIClient
from core.config import AppConfig


logger = logging.getLogger(__name__)


CHAPTER_ROUTING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "chapter_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
        "reasoning": {"type": "string"},
    },
    "required": ["chapter_ids", "reasoning"],
}

SECTION_ROUTING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "section_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
        "reasoning": {"type": "string"},
    },
    "required": ["section_ids", "reasoning"],
}

CLAUSE_DISCOVERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "matched_items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "clause_id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["covered", "violated"],
                    },
                    "reason": {"type": "string"},
                    "report_evidence": {"type": ["string", "null"]},
                },
                "required": ["clause_id", "status", "reason", "report_evidence"],
            },
        },
        "explored_clause_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["summary", "matched_items", "explored_clause_ids"],
}

VIOLATED_SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
    },
    "required": ["summary"],
}


def build_report_chapter_routing_system_prompt() -> str:
    return """你是水利水电报告比对代理中的章节路由器。

任务：
1. 根据报告分块文本，从候选规范 chapter 中选择最相关的章节。
2. 只选择后续值得深入比对的章节，通常 1 到 4 个。
3. 优先依据语义主题、工程对象、安全类别、检查事项来选择，不要机械依赖关键词单字重合。
4. 候选 chapter 中如果提供 `summary` 字段，应把它作为章节内容摘要参考，与标题一起综合判断。
5. 如果报告分块涉及缺陷、措施、结论、监测、复核等内容，也要映射到真正约束该内容的规范章节。
6. 输出中的 chapter id 必须直接复制自输入 `candidate_chapters[].id`，不得改写、缩写、解释或生成新 id。
7. `reasoning` 只写简短中文说明，不要粘贴原文，不要使用双引号，不要输出嵌套对象。
8. 输出必须严格满足给定 JSON Schema。"""


def build_report_section_routing_system_prompt() -> str:
    return """你是水利水电报告比对代理中的节路由器。

任务：
1. 在已选 chapter 范围内，从候选 section 中选择最适合进入条款评估的节。
2. 只保留真正相关的节，通常 1 到 6 个。
3. 如果某个 chapter 没有显式 section，而候选中出现 chapter_scope，表示直接在该 chapter 下比对条款，可正常选择。
4. 输出中的 section id 必须直接复制自输入 `candidate_sections[].id`，不得改写、缩写、解释或生成新 id。
5. `reasoning` 只写简短中文说明，不要粘贴原文，不要使用双引号，不要输出嵌套对象。
6. 输出必须严格满足给定 JSON Schema。"""


def build_report_clause_discovery_system_prompt() -> str:
    return """你是水利水电报告规范条款发现代理。

任务：
1. 在候选规范条款中，只发现当前报告分块有明确证据命中的 clause。
2. `covered` 表示报告文本明确满足或覆盖该 clause 的核心要求。
3. `violated` 表示报告文本明确与该 clause 要求冲突、相反或明显不满足。
4. 证据不足、弱相关、无关、仅部分暗示或无法判断时，不要输出该 clause。
5. 不要为无关 clause 输出 missing，也不要输出 partial 或 not_applicable。
6. `selected_chapters` 中如果提供 `summary` 字段，可将其作为章节范围和规范主题的参考背景，但不得用它替代对候选条款文本本身的判断。
7. 必须尽量引用报告分块中的具体语句作为 `report_evidence`；没有明确证据时可返回 null。
8. `clause_id` 必须直接复制自输入 `candidate_clauses[].id`，不得改写。
9. `explored_clause_ids` 记录本次实际查看过的候选 clause id，必须来自 `candidate_clauses[].id`。
10. `summary` 和 `reason` 都只写简短中文说明，不要粘贴带双引号的原文；如需引用原文，优先放在 `report_evidence`，且避免使用双引号。
11. 输出必须严格满足给定 JSON Schema。"""


def build_report_violated_summary_system_prompt() -> str:
    return """你是水利水电报告规范比对中的缺陷摘要器。

任务：
1. 当当前报告分块已经被判定存在 `violated` 条款时，根据报告文本、已选规范范围和违反项，生成一段简短中文缺陷摘要。
2. 摘要要优先指出违反了什么类型的具体规范要求、报告里体现出的主要缺陷是什么，以及可能带来的直接风险或管理问题。
3. 只能依据输入内容总结，不能补写输入中不存在的事实、结论、数值或整改措施。
4. 摘要控制在 60 到 160 字之间，不要输出项目符号、编号列表或 JSON 片段。
5. 如果存在 `report_evidence`，可据此概括缺陷，但不要大段照抄原文。
6. 输出必须严格满足给定 JSON Schema。"""


def build_report_violated_summary_text_system_prompt() -> str:
    return """你是水利水电报告规范比对中的缺陷摘要器。

任务：
1. 当当前报告分块已经被判定存在 `violated` 条款时，根据报告文本、已选规范范围和违反项，生成一段简短中文缺陷摘要。
2. 摘要要优先指出违反了什么类型的具体规范要求、报告里体现出的主要缺陷是什么，以及可能带来的直接风险或管理问题。
3. 只能依据输入内容总结，不能补写输入中不存在的事实、结论、数值或整改措施。
4. 摘要控制在 60 到 160 字之间。
5. 直接输出摘要正文，不要输出 JSON、代码块、项目符号、编号列表或额外说明。"""


def build_report_chapter_routing_prompt(report_unit: dict[str, Any], chapters: list[dict[str, Any]]) -> str:
    return _json_payload(
        {
            "task": "从候选章节中选择与该报告分块最相关的规范 chapter。",
            "report_unit": _report_scope_payload(report_unit),
            "candidate_chapters": chapters,
        }
    )


def build_report_section_routing_prompt(
    report_unit: dict[str, Any],
    chapters: list[dict[str, Any]],
    sections: list[dict[str, Any]],
) -> str:
    return _json_payload(
        {
            "task": "在已选章节范围内，选择最相关的规范 section。",
            "report_unit": _report_scope_payload(report_unit),
            "selected_chapters": chapters,
            "candidate_sections": sections,
        }
    )


def build_report_clause_discovery_prompt(
    report_unit: dict[str, Any],
    chapters: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    clauses: list[dict[str, Any]],
) -> str:
    return _json_payload(
        {
            "task": "在候选规范条款中发现报告分块明确覆盖或明确违反的 clause；不要输出 missing、partial 或 not_applicable。",
            "report_unit": _report_scope_payload(report_unit),
            "selected_chapters": chapters,
            "selected_sections": sections,
            "candidate_clauses": clauses,
        }
    )


def build_report_clause_assessment_system_prompt() -> str:
    return build_report_clause_discovery_system_prompt()


def build_report_clause_assessment_prompt(
    report_unit: dict[str, Any],
    chapters: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    clauses: list[dict[str, Any]],
) -> str:
    return build_report_clause_discovery_prompt(report_unit, chapters, sections, clauses)


def build_report_violated_summary_prompt(
    report_unit: dict[str, Any],
    chapters: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    violated_items: list[dict[str, Any]],
) -> str:
    return _json_payload(
        {
            "task": "根据当前报告分块中的 violated 条款，生成指出主要缺陷的简短摘要。",
            "report_unit": _report_scope_payload(report_unit),
            "selected_chapters": chapters,
            "selected_sections": sections,
            "violated_items": violated_items,
        }
    )


def _report_scope_payload(report_unit: dict[str, Any]) -> dict[str, Any]:
    if report_unit.get("scope_uid"):
        return _report_section_scope_payload(report_unit)

    report_text = (
        report_unit.get("html")
        if report_unit.get("unit_type") == "table" or report_unit.get("unitType") == "table"
        else (report_unit.get("text_normalized") or report_unit.get("textNormalized") or report_unit.get("text"))
    )
    payload = {
        "unit_uid": report_unit.get("unit_uid") or report_unit.get("unitUid") or report_unit.get("scope_uid"),
        "title": report_unit.get("title"),
        "section_path": report_unit.get("section_path") or report_unit.get("sectionPath") or [],
        "structural_path": report_unit.get("structural_path") or report_unit.get("structuralPath") or [],
        "text": report_text,
        "page_span": report_unit.get("source_page_span") or report_unit.get("page_span") or report_unit.get("pageSpan"),
    }
    unit_titles = report_unit.get("unit_titles") or report_unit.get("unitTitles")
    if unit_titles:
        payload["unit_titles"] = unit_titles
    return payload


def _report_section_scope_payload(report_scope: dict[str, Any]) -> dict[str, Any]:
    return {
        "section_title": report_scope.get("title"),
        "section_path": report_scope.get("section_path") or report_scope.get("sectionPath") or [],
        "unit_titles": report_scope.get("unit_titles") or report_scope.get("unitTitles") or [],
        "section_text": (
            report_scope.get("text_summary")
            or report_scope.get("summary")
            or report_scope.get("text_normalized")
            or report_scope.get("textNormalized")
            or report_scope.get("text")
            or ""
        ),
    }


def _json_payload(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2)


@dataclass
class ReportComparisonAgentService:
    config: AppConfig
    client: ResponsesAPIClient

    def __init__(self, config: AppConfig, client: ResponsesAPIClient | None = None) -> None:
        self.config = config
        self.client = client or ResponsesAPIClient(config)

    def compare_report_unit(
        self,
        *,
        report_unit: dict[str, Any],
        standard_id: str,
        chapter_candidates: list[dict[str, Any]],
        section_candidates: list[dict[str, Any]],
        clause_candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not self.client.enabled:
            raise ResponseAPIError(
                f"Report comparison requires {self.config.llm.api_key_env} to be configured."
            )

        routing_result = self.route_report_scope(
            report_scope=report_unit,
            standard_id=standard_id,
            chapter_candidates=chapter_candidates,
            section_candidates=section_candidates,
        )
        return self.assess_report_unit(
            report_unit=report_unit,
            standard_id=standard_id,
            selected_chapters=routing_result["selected_chapters"],
            selected_sections=routing_result["selected_sections"],
            clause_candidates=clause_candidates,
            chapter_routing_reasoning=routing_result["chapter_routing_reasoning"],
            section_routing_reasoning=routing_result["section_routing_reasoning"],
        )

    def route_report_scope(
        self,
        *,
        report_scope: dict[str, Any],
        standard_id: str,
        chapter_candidates: list[dict[str, Any]],
        section_candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        def resolve_chapter_routing() -> tuple[dict[str, Any], list[str]]:
            chapter_result = self.client.create_structured_output(
                system_prompt=build_report_chapter_routing_system_prompt(),
                user_prompt=build_report_chapter_routing_prompt(report_scope, chapter_candidates),
                schema_name="report_comparison_chapter_routing",
                schema=CHAPTER_ROUTING_SCHEMA,
            )
            chapter_ids = self._normalize_ids(
                self._extract_routing_values(
                    chapter_result,
                    (
                        "chapter_ids",
                        "selected_chapter_ids",
                        "selected_chapters",
                        "chapters",
                        "items",
                        "results",
                    ),
                ),
                chapter_candidates,
                "chapter_id",
            )
            if not chapter_ids:
                raise ResponseAPIError(f"Report comparison returned no chapter candidates for {standard_id}.")
            return self._routing_payload(chapter_result, "chapter_ids", chapter_ids), chapter_ids

        chapter_result, chapter_ids = self._run_stage_with_format_retries(
            stage_name="chapter_routing",
            standard_id=standard_id,
            operation=resolve_chapter_routing,
        )

        selected_chapters = [item for item in chapter_candidates if item["id"] in chapter_ids]
        selected_sections_source = [
            item for item in section_candidates if item.get("chapter_id") in chapter_ids or item["id"] in chapter_ids
        ]
        def resolve_section_routing() -> tuple[dict[str, Any], list[str]]:
            section_result = self.client.create_structured_output(
                system_prompt=build_report_section_routing_system_prompt(),
                user_prompt=build_report_section_routing_prompt(report_scope, selected_chapters, selected_sections_source),
                schema_name="report_comparison_section_routing",
                schema=SECTION_ROUTING_SCHEMA,
            )
            section_ids = self._normalize_ids(
                self._extract_routing_values(
                    section_result,
                    (
                        "section_ids",
                        "selected_section_ids",
                        "selected_sections",
                        "sections",
                        "items",
                        "results",
                    ),
                ),
                selected_sections_source,
                "section_id",
            )
            if not section_ids:
                logger.warning("Report comparison section routing returned no normalized ids for %s. Raw payload: %s", standard_id, section_result)
                raise ResponseAPIError(f"Report comparison returned no section candidates for {standard_id}.")
            return self._routing_payload(section_result, "section_ids", section_ids), section_ids

        section_result, section_ids = self._run_stage_with_format_retries(
            stage_name="section_routing",
            standard_id=standard_id,
            operation=resolve_section_routing,
        )

        selected_sections = [item for item in selected_sections_source if item["id"] in section_ids]
        return {
            "chapter_ids": chapter_ids,
            "section_ids": section_ids,
            "selected_chapters": selected_chapters,
            "selected_sections": selected_sections,
            "chapter_routing_reasoning": str(chapter_result.get("reasoning") or "").strip(),
            "section_routing_reasoning": str(section_result.get("reasoning") or "").strip(),
        }

    def assess_report_unit(
        self,
        *,
        report_unit: dict[str, Any],
        standard_id: str,
        selected_chapters: list[dict[str, Any]],
        selected_sections: list[dict[str, Any]],
        clause_candidates: list[dict[str, Any]],
        chapter_routing_reasoning: str = "",
        section_routing_reasoning: str = "",
    ) -> dict[str, Any]:
        chapter_ids = [str(item["id"]) for item in selected_chapters if item.get("id")]
        section_ids = [str(item["id"]) for item in selected_sections if item.get("id")]
        selected_clauses = [item for item in clause_candidates if item.get("section_id") in section_ids]
        if not selected_clauses:
            raise ResponseAPIError(f"No clause candidates were found under selected sections for {standard_id}.")

        def resolve_clause_assessment() -> tuple[dict[str, Any], list[dict[str, Any]]]:
            assessment_result = self.client.create_structured_output(
                system_prompt=build_report_clause_discovery_system_prompt(),
                user_prompt=build_report_clause_discovery_prompt(
                    report_unit,
                    selected_chapters,
                    selected_sections,
                    selected_clauses,
                ),
                schema_name="report_comparison_clause_discovery",
                schema=CLAUSE_DISCOVERY_SCHEMA,
            )
            items = self._normalize_assessment_items(
                self._extract_assessment_rows(assessment_result),
                selected_clauses,
            )
            return assessment_result, items

        assessment_result, items = self._run_stage_with_format_retries(
            stage_name="clause_discovery",
            standard_id=standard_id,
            operation=resolve_clause_assessment,
        )

        summary = str(
            assessment_result.get("summary")
            or assessment_result.get("overall_summary")
            or self._build_summary_text(items)
        ).strip()
        if not summary:
            summary = self._build_summary_text(items)
        summary = self._maybe_generate_violated_summary(
            report_unit=report_unit,
            standard_id=standard_id,
            selected_chapters=selected_chapters,
            selected_sections=selected_sections,
            selected_clauses=selected_clauses,
            items=items,
            fallback_summary=summary,
        )
        explored_clause_ids = self._normalize_explored_clause_ids(assessment_result, selected_clauses)
        coverage_score = self._compute_items_coverage_score(items)
        return {
            "chapter_ids": chapter_ids,
            "section_ids": section_ids,
            "summary": summary,
            "coverage_score": coverage_score,
            "items": items,
            "matched_clauses": items,
            "explored_clause_ids": explored_clause_ids,
            "chapter_routing_reasoning": chapter_routing_reasoning.strip(),
            "section_routing_reasoning": section_routing_reasoning.strip(),
        }

    def _normalize_ids(
        self,
        values: Any,
        candidates: list[dict[str, Any]],
        field_name: str,
    ) -> list[str]:
        alias_lookup = self._build_candidate_alias_lookup(candidates, field_name)
        items = self._coerce_id_rows(values)
        normalized: list[str] = []
        for item in items:
            resolved_id = self._resolve_candidate_id(item, field_name, alias_lookup)
            if resolved_id:
                normalized.append(resolved_id)
        if not normalized:
            raise ResponseAPIError(f"Structured output did not return valid {field_name} values.")
        return list(dict.fromkeys(normalized))

    def _extract_routing_values(self, payload: Any, keys: tuple[str, ...]) -> Any:
        if not isinstance(payload, dict):
            return payload
        for key in keys:
            value = payload.get(key)
            if value is not None:
                return value
        return payload

    def _routing_payload(self, payload: Any, id_field: str, ids: list[str]) -> dict[str, Any]:
        if isinstance(payload, dict):
            result = dict(payload)
        else:
            result = {"raw_payload_type": type(payload).__name__}
        result[id_field] = ids
        result["reasoning"] = str(result.get("reasoning") or result.get("reason") or "").strip()
        return result

    def _coerce_id_rows(self, values: Any) -> list[Any]:
        if isinstance(values, list):
            return values
        if isinstance(values, dict):
            if any(key in values for key in ("id", "chapter_id", "section_id", "chapter", "section", "ref", "label", "title")):
                return [values]
            rows: list[Any] = []
            for key, value in values.items():
                if isinstance(value, list):
                    for entry in value:
                        if isinstance(entry, dict):
                            rows.append(entry)
                        else:
                            rows.append({"id": key, "value": entry})
                    continue
                if isinstance(value, dict):
                    rows.append({"id": key, **value})
                else:
                    rows.append(key)
            return rows
        if isinstance(values, str) and values.strip():
            return [values]
        return []

    def _extract_assessment_rows(self, payload: Any) -> Any:
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []
        for key in (
            "matched_items",
            "matchedItems",
            "matched_clauses",
            "matchedClauses",
            "items",
            "clause_items",
            "evaluation",
            "evaluation_results",
            "assessments",
            "results",
            "clauses",
            "evaluations",
        ):
            value = payload.get(key)
            if isinstance(value, (list, dict)):
                return value
        return payload

    def _normalize_assessment_items(self, values: Any, clause_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        clause_lookup = self._build_candidate_alias_lookup(clause_candidates, "clause_id")
        items = self._coerce_assessment_rows(values)
        normalized: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            clause_id = self._resolve_candidate_id(item, "clause_id", clause_lookup)
            if not clause_id:
                continue
            status = self._normalize_status(
                item.get("status")
                or item.get("coverage_status")
                or item.get("coverageStatus")
                or item.get("assessment_status")
                or item.get("assessmentStatus")
            )
            if status not in {"covered", "violated"}:
                continue
            evidence = (
                item.get("report_evidence")
                or item.get("reportEvidence")
                or item.get("evidence")
                or item.get("quote")
                or item.get("excerpt")
            )
            normalized.append(
                {
                    "clause_id": clause_id,
                    "status": status,
                    "reason": str(
                        item.get("reason")
                        or item.get("analysis")
                        or item.get("comment")
                        or item.get("justification")
                        or item.get("explanation")
                        or item.get("summary")
                        or ""
                    ).strip(),
                    "report_evidence": str(evidence).strip() if isinstance(evidence, str) and evidence.strip() else None,
                }
            )
        return normalized

    def _normalize_explored_clause_ids(self, payload: dict[str, Any], clause_candidates: list[dict[str, Any]]) -> list[str]:
        values = (
            payload.get("explored_clause_ids")
            or payload.get("exploredClauseIds")
            or payload.get("explored")
            or payload.get("candidate_clause_ids")
        )
        if values is None:
            return [str(item.get("id")) for item in clause_candidates if item.get("id")]
        try:
            return self._normalize_ids(values, clause_candidates, "clause_id")
        except ResponseAPIError:
            logger.warning("Report comparison discovery returned invalid explored clause ids. Raw payload: %s", values)
            return []

    def _coerce_assessment_rows(self, values: Any) -> list[dict[str, Any]]:
        if isinstance(values, list):
            return [item for item in values if isinstance(item, dict)]
        if not isinstance(values, dict):
            return []

        rows: list[dict[str, Any]] = []
        if any(key in values for key in ("clause_id", "clauseId", "status", "coverage_status", "coverageStatus", "reason", "analysis")):
            rows.append(values)
        for key, value in values.items():
            if isinstance(value, list):
                for entry in value:
                    if isinstance(entry, dict):
                        rows.append(entry)
                    elif isinstance(entry, str):
                        rows.append({"clause_id": key, "status": entry})
                continue
            if isinstance(value, dict):
                rows.append({"clause_id": key, **value})
            elif isinstance(value, str):
                rows.append({"clause_id": key, "status": value})
        return rows

    def _build_candidate_alias_lookup(
        self,
        candidates: list[dict[str, Any]],
        field_name: str,
    ) -> dict[str, str]:
        alias_lookup: dict[str, str] = {}
        base_name = field_name.removesuffix("_id")
        alias_keys = (
            "id",
            field_name,
            base_name,
            f"{base_name}_id",
            f"{base_name}_ref",
            "ref",
            "label",
            "title",
        )
        for candidate in candidates:
            candidate_id = str(candidate.get("id") or "").strip()
            if not candidate_id:
                continue
            for key in alias_keys:
                self._register_alias(alias_lookup, candidate.get(key), candidate_id)
        return alias_lookup

    def _resolve_candidate_id(
        self,
        value: Any,
        field_name: str,
        alias_lookup: dict[str, str],
    ) -> str | None:
        base_name = field_name.removesuffix("_id")
        if isinstance(value, dict):
            candidate_values = [
                value.get("id"),
                value.get(field_name),
                value.get(base_name),
                value.get(f"{base_name}_id"),
                value.get(f"{base_name}_ref"),
                value.get("ref"),
                value.get("label"),
                value.get("title"),
            ]
        else:
            candidate_values = [value]

        for candidate_value in candidate_values:
            normalized_value = self._normalize_alias(candidate_value)
            if not normalized_value:
                continue
            resolved_id = alias_lookup.get(normalized_value)
            if resolved_id:
                return resolved_id
            resolved_id = self._resolve_candidate_id_from_text(normalized_value, alias_lookup)
            if resolved_id:
                return resolved_id
        return None

    def _resolve_candidate_id_from_text(self, normalized_value: str, alias_lookup: dict[str, str]) -> str | None:
        for alias, candidate_id in alias_lookup.items():
            if alias == normalized_value:
                return candidate_id
            if not self._is_structural_alias(alias):
                continue
            if normalized_value.startswith(f"{alias} "):
                return candidate_id
            if normalized_value.startswith(f"{alias}:"):
                return candidate_id
            if normalized_value.startswith(f"{alias}-"):
                return candidate_id
            if candidate_id.lower() in normalized_value:
                return candidate_id
        return None

    def _is_structural_alias(self, alias: str) -> bool:
        return ":" in alias or bool(re.search(r"\d", alias))

    def _register_alias(self, alias_lookup: dict[str, str], raw_value: Any, candidate_id: str) -> None:
        normalized_value = self._normalize_alias(raw_value)
        if normalized_value and normalized_value not in alias_lookup:
            alias_lookup[normalized_value] = candidate_id

    def _normalize_alias(self, raw_value: Any) -> str:
        if raw_value is None:
            return ""
        text = str(raw_value).strip()
        if not text:
            return ""
        return re.sub(r"\s+", " ", text).lower()

    def _normalize_status(self, raw_value: Any) -> str:
        normalized = self._normalize_alias(raw_value).replace(" ", "_")
        return {
            "covered": "covered",
            "cover": "covered",
            "covered_fully": "covered",
            "satisfied": "covered",
            "compliant": "covered",
            "满足": "covered",
            "符合": "covered",
            "已覆盖": "covered",
            "覆盖": "covered",
            "violated": "violated",
            "violation": "violated",
            "non_compliant": "violated",
            "not_satisfied": "violated",
            "冲突": "violated",
            "违反": "violated",
            "不满足": "violated",
            "不符合": "violated",
        }.get(normalized, normalized)

    def _compute_items_coverage_score(self, items: list[dict[str, Any]]) -> float:
        if not items:
            return 0.0
        covered_count = sum(1 for item in items if item.get("status") == "covered")
        return round(covered_count / len(items), 4)

    def _build_summary_text(self, items: list[dict[str, Any]]) -> str:
        counts = {
            "covered": 0,
            "violated": 0,
        }
        for item in items:
            status = item.get("status")
            if status in counts:
                counts[status] += 1
        return f"matched covered={counts['covered']}, violated={counts['violated']}"

    def _maybe_generate_violated_summary(
        self,
        *,
        report_unit: dict[str, Any],
        standard_id: str,
        selected_chapters: list[dict[str, Any]],
        selected_sections: list[dict[str, Any]],
        selected_clauses: list[dict[str, Any]],
        items: list[dict[str, Any]],
        fallback_summary: str,
    ) -> str:
        violated_items = [item for item in items if item.get("status") == "violated"]
        if not violated_items:
            return fallback_summary

        clause_by_id = {str(item.get("id") or ""): item for item in selected_clauses if item.get("id")}
        violated_payload = []
        for item in violated_items:
            clause = clause_by_id.get(str(item.get("clause_id") or ""))
            violated_payload.append(
                {
                    "clause_id": item.get("clause_id"),
                    "clause_ref": clause.get("clause_ref") if clause else None,
                    "label": clause.get("label") if clause else None,
                    "status": item.get("status"),
                    "reason": item.get("reason"),
                    "report_evidence": item.get("report_evidence"),
                }
            )

        text_summary = self._try_generate_violated_summary_text(
            report_unit=report_unit,
            standard_id=standard_id,
            selected_chapters=selected_chapters,
            selected_sections=selected_sections,
            violated_payload=violated_payload,
            base_error=None,
        )
        if text_summary:
            return text_summary

        def resolve_violated_summary() -> dict[str, Any]:
            payload = self.client.create_structured_output(
                system_prompt=build_report_violated_summary_system_prompt(),
                user_prompt=build_report_violated_summary_prompt(
                    report_unit,
                    selected_chapters,
                    selected_sections,
                    violated_payload,
                ),
                schema_name="report_comparison_violated_summary",
                schema=VIOLATED_SUMMARY_SCHEMA,
            )
            summary = str(payload.get("summary") or "").strip()
            if not summary:
                raise ResponseAPIError(f"Report comparison violated summary returned no summary for {standard_id}.")
            return payload

        try:
            payload = resolve_violated_summary()
        except ResponseAPIError as exc:
            fallback_text = self._try_generate_violated_summary_text(
                report_unit=report_unit,
                standard_id=standard_id,
                selected_chapters=selected_chapters,
                selected_sections=selected_sections,
                violated_payload=violated_payload,
                base_error=exc,
            )
            if fallback_text:
                return fallback_text
            return fallback_summary
        return str(payload.get("summary") or fallback_summary).strip() or fallback_summary

    def _try_generate_violated_summary_text(
        self,
        *,
        report_unit: dict[str, Any],
        standard_id: str,
        selected_chapters: list[dict[str, Any]],
        selected_sections: list[dict[str, Any]],
        violated_payload: list[dict[str, Any]],
        base_error: ResponseAPIError | None,
    ) -> str | None:
        if not hasattr(self.client, "create_text_output"):
            if base_error is not None:
                logger.warning(
                    "Report comparison violated summary plain text fallback is unavailable for %s; keeping base summary.",
                    standard_id,
                )
            return None
        if base_error is not None:
            logger.warning(
                "Report comparison violated summary structured output failed for %s; retrying with plain text fallback. Error: %s",
                standard_id,
                base_error,
            )
        else:
            logger.debug("Generating report comparison violated summary as plain text for %s.", standard_id)

        def resolve_violated_summary_text() -> str:
            raw_text = self.client.create_text_output(
                system_prompt=build_report_violated_summary_text_system_prompt(),
                user_prompt=build_report_violated_summary_prompt(
                    report_unit,
                    selected_chapters,
                    selected_sections,
                    violated_payload,
                ),
            )
            summary = self._normalize_generated_summary_text(raw_text)
            if not summary:
                raise ResponseAPIError(f"Report comparison violated text summary returned no summary for {standard_id}.")
            return summary

        try:
            return self._run_stage_with_format_retries(
                stage_name="violated_summary_text",
                standard_id=standard_id,
                operation=resolve_violated_summary_text,
            )
        except ResponseAPIError as exc:
            logger.warning(
                "Report comparison violated summary failed for %s; keeping base summary. Error: %s",
                standard_id,
                exc,
            )
            return None

    def _normalize_generated_summary_text(self, raw_text: Any) -> str:
        text = str(raw_text or "").strip()
        if not text:
            return ""
        fenced_match = re.match(r"^```(?:json|text)?\s*(.*?)\s*```$", text, flags=re.DOTALL | re.IGNORECASE)
        if fenced_match:
            text = fenced_match.group(1).strip()
        if text.startswith("{") and text.endswith("}"):
            import json

            try:
                payload = json.loads(text)
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                summary = payload.get("summary")
                if isinstance(summary, str) and summary.strip():
                    text = summary.strip()
        text = re.sub(r"^\s*(?:summary|摘要|总结)\s*[:：]\s*", "", text, flags=re.IGNORECASE).strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
            text = text[1:-1].strip()
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _run_stage_with_format_retries(
        self,
        *,
        stage_name: str,
        standard_id: str,
        operation: Any,
    ) -> Any:
        max_retries = max(0, self.config.llm.batch_max_retries)
        max_attempts = max_retries + 1
        last_error: ResponseAPIError | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return operation()
            except ResponseAPIError as exc:
                last_error = exc
                if attempt >= max_attempts:
                    raise
                delay_seconds = max(0.0, self.config.llm.batch_retry_backoff_seconds) * attempt
                logger.warning(
                    "Retrying report comparison %s for %s after attempt %s/%s due to incompatible structured output: %s",
                    stage_name,
                    standard_id,
                    attempt,
                    max_attempts,
                    exc,
                )
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
        if last_error is not None:
            raise last_error
        raise ResponseAPIError(f"Report comparison {stage_name} failed for {standard_id}.")
