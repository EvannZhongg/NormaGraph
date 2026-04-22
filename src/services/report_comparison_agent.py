from __future__ import annotations

from dataclasses import dataclass
import logging
import re
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

CLAUSE_ASSESSMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "coverage_score": {"type": "number"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "clause_id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["covered", "partial", "missing", "violated", "not_applicable"],
                    },
                    "reason": {"type": "string"},
                    "report_evidence": {"type": ["string", "null"]},
                },
                "required": ["clause_id", "status", "reason", "report_evidence"],
            },
        },
    },
    "required": ["summary", "coverage_score", "items"],
}


def build_report_chapter_routing_system_prompt() -> str:
    return """你是水利水电报告比对代理中的章节路由器。

任务：
1. 根据报告分块文本，从候选规范 chapter 中选择最相关的章节。
2. 只选择后续值得深入比对的章节，通常 1 到 4 个。
3. 优先依据语义主题、工程对象、安全类别、检查事项来选择，不要机械依赖关键词单字重合。
4. 如果报告分块涉及缺陷、措施、结论、监测、复核等内容，也要映射到真正约束该内容的规范章节。
5. 输出中的 chapter id 必须直接复制自输入 `candidate_chapters[].id`，不得改写、缩写、解释或生成新 id。
6. `reasoning` 只写简短中文说明，不要粘贴原文，不要使用双引号，不要输出嵌套对象。
7. 输出必须严格满足给定 JSON Schema。"""


def build_report_section_routing_system_prompt() -> str:
    return """你是水利水电报告比对代理中的节路由器。

任务：
1. 在已选 chapter 范围内，从候选 section 中选择最适合进入条款评估的节。
2. 只保留真正相关的节，通常 1 到 6 个。
3. 如果某个 chapter 没有显式 section，而候选中出现 chapter_scope，表示直接在该 chapter 下比对条款，可正常选择。
4. 输出中的 section id 必须直接复制自输入 `candidate_sections[].id`，不得改写、缩写、解释或生成新 id。
5. `reasoning` 只写简短中文说明，不要粘贴原文，不要使用双引号，不要输出嵌套对象。
6. 输出必须严格满足给定 JSON Schema。"""


def build_report_clause_assessment_system_prompt() -> str:
    return """你是水利水电报告规范覆盖评估代理。

任务：
1. 将一个报告分块与候选规范条款进行逐条评估。
2. `covered` 表示报告文本已经明确满足或覆盖该条款核心要求。
3. `partial` 表示报告只覆盖了一部分要求，仍有明显缺口。
4. `missing` 表示候选条款要求在报告分块中没有体现。
5. `violated` 表示报告分块明确出现了与条款要求相冲突、相反或明显不满足的表述。
6. `not_applicable` 只在该条款与当前报告分块显然无关时使用。
7. 必须尽量引用报告分块中的具体语句作为 `report_evidence`；没有明确证据时可返回 null。
8. `coverage_score` 取 0 到 1 之间的小数，表示该分块对当前候选条款集合的总体覆盖程度。
9. `clause_id` 必须直接复制自输入 `candidate_clauses[].id`，不得改写。
10. `summary` 和 `reason` 都只写简短中文说明，不要粘贴带双引号的原文；如需引用原文，优先放在 `report_evidence`，且避免使用双引号。
11. 输出必须严格满足给定 JSON Schema。"""


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


def build_report_clause_assessment_prompt(
    report_unit: dict[str, Any],
    chapters: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    clauses: list[dict[str, Any]],
) -> str:
    return _json_payload(
        {
            "task": "对报告分块与候选规范条款进行覆盖/缺失/违反评估。",
            "report_unit": _report_scope_payload(report_unit),
            "selected_chapters": chapters,
            "selected_sections": sections,
            "candidate_clauses": clauses,
        }
    )


def _report_scope_payload(report_unit: dict[str, Any]) -> dict[str, Any]:
    return {
        "unit_uid": report_unit.get("unit_uid") or report_unit.get("scope_uid"),
        "title": report_unit.get("title"),
        "section_path": report_unit.get("section_path", []),
        "structural_path": report_unit.get("structural_path", []),
        "text": report_unit.get("text_normalized") or report_unit.get("text"),
        "page_span": report_unit.get("source_page_span") or report_unit.get("page_span"),
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
        chapter_result = self.client.create_structured_output(
            system_prompt=build_report_chapter_routing_system_prompt(),
            user_prompt=build_report_chapter_routing_prompt(report_scope, chapter_candidates),
            schema_name="report_comparison_chapter_routing",
            schema=CHAPTER_ROUTING_SCHEMA,
        )
        chapter_ids = self._normalize_ids(
            chapter_result.get("chapter_ids")
            or chapter_result.get("selected_chapter_ids")
            or chapter_result.get("selected_chapters"),
            chapter_candidates,
            "chapter_id",
        )
        if not chapter_ids:
            raise ResponseAPIError(f"Report comparison returned no chapter candidates for {standard_id}.")

        selected_chapters = [item for item in chapter_candidates if item["id"] in chapter_ids]
        selected_sections_source = [
            item for item in section_candidates if item.get("chapter_id") in chapter_ids or item["id"] in chapter_ids
        ]
        section_result = self.client.create_structured_output(
            system_prompt=build_report_section_routing_system_prompt(),
            user_prompt=build_report_section_routing_prompt(report_scope, selected_chapters, selected_sections_source),
            schema_name="report_comparison_section_routing",
            schema=SECTION_ROUTING_SCHEMA,
        )
        section_ids = self._normalize_ids(
            section_result.get("section_ids")
            or section_result.get("selected_section_ids")
            or section_result.get("selected_sections")
            or section_result.get("sections"),
            selected_sections_source,
            "section_id",
        )
        if not section_ids:
            logger.warning("Report comparison section routing returned no normalized ids for %s. Raw payload: %s", standard_id, section_result)
            raise ResponseAPIError(f"Report comparison returned no section candidates for {standard_id}.")

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

        assessment_result = self.client.create_structured_output(
            system_prompt=build_report_clause_assessment_system_prompt(),
            user_prompt=build_report_clause_assessment_prompt(
                report_unit,
                selected_chapters,
                selected_sections,
                selected_clauses,
            ),
            schema_name="report_comparison_clause_assessment",
            schema=CLAUSE_ASSESSMENT_SCHEMA,
        )
        items = self._normalize_assessment_items(
            self._extract_assessment_rows(assessment_result),
            selected_clauses,
        )
        if not items:
            logger.warning("Report comparison assessment returned no normalized items for %s. Raw payload: %s", standard_id, assessment_result)
            raise ResponseAPIError(f"Report comparison returned no clause assessments for {standard_id}.")

        summary = str(
            assessment_result.get("summary")
            or assessment_result.get("overall_summary")
            or self._build_summary_text(items)
        ).strip()
        coverage_score = self._resolve_coverage_score(assessment_result, items)
        return {
            "chapter_ids": chapter_ids,
            "section_ids": section_ids,
            "summary": summary,
            "coverage_score": coverage_score,
            "items": items,
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
            status = self._normalize_status(item.get("status"))
            if status not in {"covered", "partial", "missing", "violated", "not_applicable"}:
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

    def _coerce_assessment_rows(self, values: Any) -> list[dict[str, Any]]:
        if isinstance(values, list):
            return [item for item in values if isinstance(item, dict)]
        if not isinstance(values, dict):
            return []

        rows: list[dict[str, Any]] = []
        if any(key in values for key in ("clause_id", "clauseId", "status", "reason", "analysis")):
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
            "partial": "partial",
            "partially_covered": "partial",
            "partially_satisfied": "partial",
            "partially_compliant": "partial",
            "部分覆盖": "partial",
            "部分满足": "partial",
            "部分符合": "partial",
            "missing": "missing",
            "not_covered": "missing",
            "uncovered": "missing",
            "absent": "missing",
            "缺失": "missing",
            "未覆盖": "missing",
            "未体现": "missing",
            "violated": "violated",
            "violation": "violated",
            "non_compliant": "violated",
            "not_satisfied": "violated",
            "冲突": "violated",
            "违反": "violated",
            "不满足": "violated",
            "不符合": "violated",
            "not_applicable": "not_applicable",
            "not-applicable": "not_applicable",
            "n/a": "not_applicable",
            "na": "not_applicable",
            "不适用": "not_applicable",
            "无关": "not_applicable",
        }.get(normalized, normalized)

    def _resolve_coverage_score(self, payload: dict[str, Any], items: list[dict[str, Any]]) -> float:
        raw_value = (
            payload.get("coverage_score")
            or payload.get("coverageScore")
            or payload.get("score")
            or payload.get("coverage")
        )
        if raw_value is None:
            return self._compute_items_coverage_score(items)
        return self._clamp_score(raw_value)

    def _clamp_score(self, value: Any) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            raise ResponseAPIError("Structured output did not return a valid coverage_score.")
        return max(0.0, min(1.0, parsed))

    def _compute_items_coverage_score(self, items: list[dict[str, Any]]) -> float:
        applicable_items = [item for item in items if item.get("status") != "not_applicable"]
        if not applicable_items:
            return 0.0
        score = 0.0
        for item in applicable_items:
            status = item.get("status")
            if status == "covered":
                score += 1.0
            elif status == "partial":
                score += 0.5
        return round(score / len(applicable_items), 4)

    def _build_summary_text(self, items: list[dict[str, Any]]) -> str:
        counts = {
            "covered": 0,
            "partial": 0,
            "missing": 0,
            "violated": 0,
            "not_applicable": 0,
        }
        for item in items:
            status = item.get("status")
            if status in counts:
                counts[status] += 1
        return (
            f"covered={counts['covered']}, partial={counts['partial']}, "
            f"missing={counts['missing']}, violated={counts['violated']}, "
            f"not_applicable={counts['not_applicable']}"
        )
