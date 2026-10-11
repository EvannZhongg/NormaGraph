from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from typing import Any, Sequence

from adapters.llm_client import ResponseAPIError, ResponsesAPIClient
from core.config import AppConfig


logger = logging.getLogger(__name__)

DOMAIN_TAGGING_SYSTEM_PROMPT = """你是水利水电规范领域标签分类器。你只负责给输入条文选择领域标签，不负责抽取或改写规范要求。

标签规则：
1. 每个标签必须是从一个维度根节点到最具体末级节点的完整路径，使用半角连字符 `-` 连接，例如 `过程-资料搜集-基础资料搜集`、`工程对象-挡水建筑物-土石坝-坝体`。
2. 可以返回多个标签，但只返回条文内容明确涉及的标签，不要为了凑数量添加泛化标签。
3. 只能使用以下四个维度根节点：过程、工程对象、环境、材料。维度下的节点应从条文语义概括，路径至少包含根节点和一个下级节点。
4. 不要返回单独关键词、章节标题、解释、编号或 Markdown；不要输出 `domain_tags` 以外的字段。
5. 每个输入条文都必须返回一个结果项，clause_uid 原样复制；无法判断时返回空数组。
6. 输出必须严格满足给定 JSON Schema。"""


def build_domain_tagging_prompt(standard_uid: str, clauses: Sequence[dict[str, Any]]) -> str:
    payload = {
        "standard_uid": standard_uid,
        "task": "为每个规范条文选择零个或多个完整领域路径标签。",
        "clauses": [
            {
                "clause_uid": clause.get("clause_uid"),
                "clause_ref": clause.get("clause_ref"),
                "heading_path": clause.get("heading_path") or [],
                "clause_text": clause.get("source_text_normalized") or clause.get("source_text") or "",
                "requirement_texts": [
                    item.get("requirement_text")
                    for item in (clause.get("requirements") or [])
                    if item.get("requirement_text")
                ],
            }
            for clause in clauses
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


@dataclass
class DomainTaggingResult:
    clause_tags: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


class DomainTaggingService:
    """独立领域标签 API；不与 requirement 抽取共享结构化输出。"""

    ROOTS = {"过程", "工程对象", "环境", "材料"}

    def __init__(self, config: AppConfig, client: ResponsesAPIClient) -> None:
        self.config = config
        self.client = client
        schema_path = self.config.schema_dir / "domain_tagging.schema.json"
        self.schema = json.loads(schema_path.read_text(encoding="utf-8"))

    @property
    def enabled(self) -> bool:
        return self.client.enabled

    def tag_clauses(self, standard_uid: str, clauses: Sequence[dict[str, Any]]) -> DomainTaggingResult:
        if not clauses:
            return DomainTaggingResult(metrics={"requested_clause_count": 0, "tag_call_count": 0, "tagged_clause_count": 0})
        if not self.enabled:
            warning = f"Domain tagging skipped because {self.config.llm.api_key_env} is not configured."
            logger.warning(warning)
            return DomainTaggingResult(
                warnings=[warning],
                metrics={"requested_clause_count": len(clauses), "tag_call_count": 0, "tagged_clause_count": 0},
            )

        tags: dict[str, list[str]] = {}
        warnings: list[str] = []
        call_count = 0
        batch_size = max(1, int(self.config.llm.clause_batch_size))
        for start in range(0, len(clauses), batch_size):
            batch = clauses[start : start + batch_size]
            call_count += 1
            try:
                payload = self.client.create_structured_output(
                    system_prompt=DOMAIN_TAGGING_SYSTEM_PROMPT,
                    user_prompt=build_domain_tagging_prompt(standard_uid, batch),
                    schema_name="domain_tagging_batch",
                    schema=self.schema,
                )
                items = payload.get("items", []) if isinstance(payload, dict) else []
                for item in items if isinstance(items, list) else []:
                    if not isinstance(item, dict):
                        continue
                    uid = str(item.get("clause_uid") or "").strip()
                    if uid:
                        tags[uid] = self._normalize_tags(item.get("domain_tags"))
            except ResponseAPIError as exc:
                logger.warning("Domain tagging batch failed: %s", exc)
                warnings.append(f"domain_tagging_failed_batch_{start // batch_size + 1}:{exc}")
        missing = [str(clause.get("clause_uid")) for clause in clauses if clause.get("clause_uid") not in tags]
        if missing:
            warnings.append(f"domain_tag_missing_clause_count={len(missing)}")
        return DomainTaggingResult(
            clause_tags=tags,
            warnings=warnings,
            metrics={"requested_clause_count": len(clauses), "tag_call_count": call_count, "tagged_clause_count": len(tags)},
        )

    @classmethod
    def _normalize_tags(cls, values: Any) -> list[str]:
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            return []
        result: list[str] = []
        for value in values:
            text = str(value or "").strip().replace("－", "-").replace("—", "-")
            parts = [part.strip() for part in text.split("-") if part.strip()]
            if len(parts) < 2 or parts[0] not in cls.ROOTS:
                continue
            normalized = "-".join(parts)
            if normalized not in result:
                result.append(normalized)
        return result
