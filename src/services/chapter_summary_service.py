from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import json
import logging
from typing import Any, Sequence

from adapters.llm_client import ResponseAPIError, ResponsesAPIClient
from core.config import AppConfig
from prompts import LLM_CHAPTER_SUMMARY_SYSTEM_PROMPT, build_chapter_summary_prompt


logger = logging.getLogger(__name__)


@dataclass
class ChapterSummaryResult:
    chapter_items: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


class ChapterSummaryService:
    MAX_PROMPT_CLAUSES = 80
    MAX_PROMPT_TOTAL_CHARS = 16000
    MAX_PROMPT_CLAUSE_CHARS = 800

    def __init__(self, config: AppConfig, client: ResponsesAPIClient) -> None:
        self.config = config
        self.client = client
        schema_path = self.config.schema_dir / "chapter_summary.schema.json"
        self.schema = json.loads(schema_path.read_text(encoding="utf-8"))

    @property
    def enabled(self) -> bool:
        return self.client.enabled

    def summarize_chapters(
        self,
        *,
        standard_uid: str,
        structure_nodes: Sequence[dict[str, Any]],
        clauses: Sequence[dict[str, Any]],
    ) -> ChapterSummaryResult:
        chapter_nodes = [node for node in structure_nodes if node.get("node_type") == "chapter"]
        if not chapter_nodes:
            return ChapterSummaryResult(
                metrics={
                    "chapter_summary_status": "skipped_no_chapters",
                    "chapter_summary_discovered_count": 0,
                    "chapter_summary_requested_count": 0,
                    "chapter_summary_completed_count": 0,
                    "chapter_summary_failed_count": 0,
                    "chapter_summary_skipped_count": 0,
                }
            )

        if not self.config.knowledge_graph.generate_chapter_summaries:
            return ChapterSummaryResult(
                metrics={
                    "chapter_summary_status": "skipped_disabled",
                    "chapter_summary_discovered_count": len(chapter_nodes),
                    "chapter_summary_requested_count": 0,
                    "chapter_summary_completed_count": 0,
                    "chapter_summary_failed_count": 0,
                    "chapter_summary_skipped_count": len(chapter_nodes),
                }
            )

        if not self.enabled:
            warning = f"Chapter summary generation skipped because {self.config.llm.api_key_env} is not configured."
            logger.warning(warning)
            return ChapterSummaryResult(
                warnings=[warning],
                metrics={
                    "chapter_summary_status": f"missing_api_key:{self.config.llm.api_key_env}",
                    "chapter_summary_discovered_count": len(chapter_nodes),
                    "chapter_summary_requested_count": 0,
                    "chapter_summary_completed_count": 0,
                    "chapter_summary_failed_count": 0,
                    "chapter_summary_skipped_count": len(chapter_nodes),
                },
            )

        clauses_by_chapter_ref: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for clause in clauses:
            chapter_ref = str(clause.get("chapter_ref") or "").strip()
            if clause.get("body_kind") != "main" or not chapter_ref:
                continue
            clauses_by_chapter_ref[chapter_ref].append(clause)

        chapter_items: dict[str, dict[str, Any]] = {}
        warnings: list[str] = []
        requested_count = 0
        completed_count = 0
        failed_count = 0
        skipped_count = 0

        for chapter in chapter_nodes:
            chapter_uid = str(chapter.get("node_uid") or "")
            chapter_ref = str(chapter.get("ref") or "").strip()
            chapter_clauses = self._sort_clauses(clauses_by_chapter_ref.get(chapter_ref, []))
            if not chapter_uid or not chapter_clauses:
                skipped_count += 1
                warnings.append(f"chapter_summary_skipped:{chapter_uid or chapter_ref or 'unknown'}:no_clauses")
                continue

            prompt_clauses, was_truncated = self._build_prompt_clauses(chapter_clauses)
            if not prompt_clauses:
                skipped_count += 1
                warnings.append(f"chapter_summary_skipped:{chapter_uid}:empty_prompt")
                continue

            requested_count += 1
            try:
                payload = self.client.create_structured_output(
                    system_prompt=LLM_CHAPTER_SUMMARY_SYSTEM_PROMPT,
                    user_prompt=build_chapter_summary_prompt(standard_uid, chapter, prompt_clauses),
                    schema_name="standard_chapter_summary",
                    schema=self.schema,
                )
                summary = str(payload.get("summary") or "").strip()
                if not summary:
                    raise ResponseAPIError("Chapter summary response did not include a non-empty summary.")
            except ResponseAPIError as exc:
                failed_count += 1
                warnings.append(f"chapter_summary_failed:{chapter_uid}:{exc}")
                continue

            chapter_items[chapter_uid] = {
                "summary": summary,
                "summary_source_clause_count": len(chapter_clauses),
                "summary_source_truncated": was_truncated,
            }
            completed_count += 1

        status = "completed"
        if requested_count == 0:
            status = "skipped_no_eligible_chapters"
        elif completed_count == 0 and failed_count > 0:
            status = "failed"
        elif failed_count > 0:
            status = "partial"

        return ChapterSummaryResult(
            chapter_items=chapter_items,
            warnings=warnings,
            metrics={
                "chapter_summary_status": status,
                "chapter_summary_discovered_count": len(chapter_nodes),
                "chapter_summary_requested_count": requested_count,
                "chapter_summary_completed_count": completed_count,
                "chapter_summary_failed_count": failed_count,
                "chapter_summary_skipped_count": skipped_count,
            },
        )

    def _build_prompt_clauses(self, clauses: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
        prompt_clauses: list[dict[str, Any]] = []
        total_chars = 0
        truncated = False

        for index, clause in enumerate(clauses):
            if len(prompt_clauses) >= self.MAX_PROMPT_CLAUSES:
                truncated = True
                break

            raw_text = str(clause.get("source_text_normalized") or clause.get("source_text") or "").strip()
            if not raw_text:
                continue

            clause_text = raw_text[: self.MAX_PROMPT_CLAUSE_CHARS].strip()
            if len(clause_text) < len(raw_text):
                truncated = True

            if total_chars >= self.MAX_PROMPT_TOTAL_CHARS:
                truncated = True
                break

            remaining_chars = self.MAX_PROMPT_TOTAL_CHARS - total_chars
            if len(clause_text) > remaining_chars:
                clause_text = clause_text[:remaining_chars].strip()
                truncated = True
            if not clause_text:
                break

            prompt_clauses.append(
                {
                    "clause_ref": clause.get("clause_ref"),
                    "clause_summary": clause.get("clause_summary"),
                    "source_text_normalized": clause_text,
                }
            )
            total_chars += len(clause_text)

            if index < len(clauses) - 1 and total_chars >= self.MAX_PROMPT_TOTAL_CHARS:
                truncated = True
                break

        return prompt_clauses, truncated

    def _sort_clauses(self, clauses: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(clauses, key=lambda item: self._clause_sort_key(item.get("clause_ref")))

    def _clause_sort_key(self, clause_ref: Any) -> tuple[Any, ...]:
        parts = str(clause_ref or "").split(".")
        key: list[Any] = []
        for part in parts:
            if part.isdigit():
                key.append((0, int(part)))
            else:
                key.append((1, part))
        return tuple(key)
