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


LLM_REPORT_TITLE_PLANNING_SYSTEM_PROMPT = """你是水利水电工程报告标题规划器，负责识别 OCR / 版面分析输出中被标记为 title 的文本块，在报告结构中的真实角色，并生成用于后续正文切分的 title plan。

只允许输出以下角色：
- front_matter: 正文前的摘要、前言、说明、成果概述等前置内容。
- toc: “目录”标题本身。
- chapter: 一级主体章节，例如 1 引言、2 工程概况。
- section: 二级结构标题，例如 2.1、3.4。
- subsection: 三级结构标题，例如 2.1.3。
- topic: 结构章节内部的局部主题、小标题、检查项分组、监测项目组。
- subtopic: topic 之下更细的局部列项、小标题或局部检查点。
- appendix: 附录、附件、附表、附图等附属结构标题。
- ignore: 不应作为独立分段处理的标题，包括目录条目、页眉页脚、重复标题、噪声、以及无独立结构意义的局部短语。

判别要求：
1. 只能依据输入中的标题文本和上下文信息判断，不要假设存在额外规则。
2. 必须优先综合标题顺序、编号模式、前后标题关系、页角色、标题附近正文预览来判断，不要机械依赖 raw title level。
3. chapter / section / subsection / appendix 是结构层；topic / subtopic 不是新的主体层级，而是挂在当前结构层下面的局部议题。
4. 同一份报告里，单独数字标题可能既可能是 chapter，也可能只是局部 topic；例如 `1 引言` 可能是 chapter，但 `1 基础资料收集` 在 `1.2` 节内部更可能是 topic。
5. 目录页上通常只有真正的“目录”标题应判为 toc；其余目录条目即使长得像 chapter / section，也一律判为 ignore。
6. 如果标题明显是附录、附件、附表、附图，优先判为 appendix，而不是 chapter / section。
7. 如果标题只是局部检查项、并列列项、监测项目名、短语性段首或无独立分段意义的提示语，优先判为 topic / subtopic / ignore，而不是勉强提升为结构层。
8. 如果无法可靠判断，优先使用 ignore，而不是勉强归入 chapter / section / subsection。
9. 你必须为每个输入 title_id 返回一条结果，不能遗漏，title_id 必须原样保留。
10. 输出必须严格满足给定 JSON Schema。"""


LLM_STANDARD_TITLE_CLASSIFICATION_SYSTEM_PROMPT = """你是规范标题判别器，负责识别 OCR / 版面分析输出中被标记为 title 的文本块，在规范结构中的真实角色。

只允许输出以下标签：
- clause: 实际上是条文正文或条文起始，例如 1.0.1、2.3.4 这种规范条款。
- section: 结构性标题，但不是一级 chapter，例如 2.1、2.1.3 之类的节、小节。
- reference_standard: 专门表示“引用标准 / 规范性引用文件”这一类章节。
- chapter: 一级章节标题，例如 1 总则、7 防洪能力复核。
- appendix: 附录、附件、附表、附图等附属结构标题。
- none: 不应作为结构节点处理，包括封面标题、英文标题、目录条目、页眉页脚、噪声、以及 OCR 把正文误识别为 title 的情况。

判别要求：
1. 只能依据输入中的标题文本和上下文信息判断，不要假设存在额外规则。
2. 目录页条目即使长得像 chapter / section，只要明显是目录项而不是正文真实标题，一律判为 none。
3. 如果文本语义上就是“引用标准 / 规范性引用文件”，优先判为 reference_standard，而不是 chapter。
4. 如果文本本身像完整条文句子，或以 1.0.1 / 2.3.4 这类条款编号开头，优先判为 clause。
5. 如果文本以 6.4.1～6.4.4、3.2.1-3.2.3 这类条款范围编号开头，本质上仍按 clause 处理。
6. 只有明确是附录/附件时才能判为 appendix。
7. 如果无法可靠判断，优先使用 none，而不是勉强归入结构类。
8. 你必须为每个输入 title_id 返回一条结果，不能遗漏。
9. 输出必须严格满足给定 JSON Schema。"""


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


def build_report_title_planning_prompt(
    document_id: str,
    previous_titles: Sequence[dict[str, Any]],
    current_titles: Sequence[dict[str, Any]],
) -> str:
    payload = {
        "document_id": document_id,
        "task": "请为当前批标题生成 title plan，判断其真实结构角色，供后续正文挂载使用。",
        "role_definitions": {
            "front_matter": "正文前的摘要、说明、前言等前置内容。",
            "toc": "目录标题本身。",
            "chapter": "一级主体章节。",
            "section": "二级结构章节。",
            "subsection": "三级结构章节。",
            "topic": "结构章节内部的局部主题、小节标题或检查项分组。",
            "subtopic": "topic 之下更细的局部标题、列项或监测项。",
            "appendix": "附录、附件、附表、附图等附属结构。",
            "ignore": "不应单独开段的噪声、目录条目、重复标题或无独立结构意义的短标题。",
        },
        "previous_decisions": [
            {
                "title_id": item.get("title_id"),
                "text": item.get("text"),
                "page_idx": item.get("page_idx"),
                "section_kind": item.get("section_kind"),
                "hierarchy_level": item.get("hierarchy_level"),
                "is_structural": item.get("is_structural"),
                "ref": item.get("ref"),
            }
            for item in previous_titles
        ],
        "current_titles": [
            {
                "title_id": item.get("title_id"),
                "title_index": item.get("title_index"),
                "page_idx": item.get("page_idx"),
                "page_role": item.get("page_role"),
                "text": item.get("text"),
                "text_normalized": item.get("text_normalized"),
                "raw_title_level": item.get("raw_title_level"),
                "previous_title": item.get("previous_title"),
                "next_title": item.get("next_title"),
                "preceding_text_preview": item.get("preceding_text_preview"),
                "following_text_preview": item.get("following_text_preview"),
                "numbering_pattern": item.get("numbering_pattern"),
                "heuristic_suggestion": {
                    "section_kind": item.get("heuristic_section_kind"),
                    "hierarchy_level": item.get("heuristic_hierarchy_level"),
                    "is_structural": item.get("heuristic_is_structural"),
                    "ref": item.get("heuristic_ref"),
                },
            }
            for item in current_titles
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_standard_title_classification_prompt(
    standard_uid: str,
    previous_titles: Sequence[dict[str, Any]],
    current_titles: Sequence[dict[str, Any]],
) -> str:
    payload = {
        "standard_uid": standard_uid,
        "task": "判断这些被 OCR 标记为 title 的文本块，在规范正文中的真实类别。",
        "previous_batch_tail": [
            {
                "title_id": item.get("title_id"),
                "page_idx": item.get("page_idx"),
                "text": item.get("text"),
                "label": item.get("label"),
            }
            for item in previous_titles
        ],
        "current_titles": [
            {
                "title_id": item.get("title_id"),
                "title_index": item.get("title_index"),
                "page_idx": item.get("page_idx"),
                "text": item.get("text"),
                "text_normalized": item.get("text_normalized"),
                "raw_title_level": item.get("raw_title_level"),
                "previous_title": item.get("previous_title"),
                "next_title": item.get("next_title"),
                "previous_block_preview": item.get("previous_block_preview"),
                "next_block_preview": item.get("next_block_preview"),
            }
            for item in current_titles
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
