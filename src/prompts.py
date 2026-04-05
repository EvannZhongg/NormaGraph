from __future__ import annotations

import json
from typing import Any, Sequence


LLM_REQUIREMENT_EXTRACTION_SYSTEM_PROMPT = """你是水利水电规范知识图谱抽取器，负责把规范条文转成后续可用于报告审查、问答检索和图谱构建的结构化要求。

抽取规则：
1. 只能依据输入条文内容抽取，不能补写条文中不存在的事实、数值、范围或外部标准。
2. 只抽取可判定、可执行、可核查的规范性要求；纯定义、目的、说明、背景、举例、解释性描述返回空 requirements。
3. 如果一个条文包含多个并列动作、多个列项或多个判定点，应拆成多个原子 requirement。
4. 如果列项承接上文的主语、谓语或适用条件，需要最小化继承补全，但不得改变原意。
5. requirement_text 采用中文，尽量保留原句表达；subject/action/object/applicability_rule/judgement_criteria/evidence_expected/domain_tags 是对 requirement_text 的结构化解释。
6. modality 只能是 must、should、may、forbidden、conditional 之一。
7. cited_targets 只填写条文中明确出现的外部标准编号或条款号；没有明确引用则返回空数组。
8. confidence 取 0 到 1 之间的小数，表示你对该 requirement 抽取正确性的信心。
9. 对每个输入条文都必须返回一个结果项，使用原样的 clause_uid 和 clause_ref；如果没有规范性要求，则 requirements 返回空数组。
10. 输出必须严格满足给定 JSON Schema。"""


def build_clause_extraction_prompt(standard_uid: str, clauses: Sequence[dict[str, Any]]) -> str:
    payload = {
        "standard_uid": standard_uid,
        "task": "从以下规范条文中抽取原子要求、主题概念与显式引用。",
        "clauses": [
            {
                "clause_uid": clause["clause_uid"],
                "clause_ref": clause["clause_ref"],
                "heading_path": clause.get("heading_path", []),
                "chapter_ref": clause.get("chapter_ref"),
                "section_ref": clause.get("section_ref"),
                "source_text_normalized": clause.get("source_text_normalized") or clause.get("source_text"),
                "list_items": [item.get("text_normalized") or item.get("text") for item in clause.get("list_items", [])],
            }
            for clause in clauses
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
