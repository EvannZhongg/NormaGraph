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


LLM_REPORT_TITLE_PLANNING_SYSTEM_PROMPT = """你是水利水电工程安全评价报告的标题规划器。输入来自 PDF 解析后被标为 title 的文本块，其中很多并不是真正的报告标题。你的任务是生成稳定、保守的 title plan，用于后续切分正文。

只允许输出以下 role：
- toc: 只有“目录”标题本身。
- chapter: 正文主体一级大章，例如 1 基本情况、2 现场安全检查及安全检测、11 大坝安全综合评价。
- unit: 大章内部应作为一个评估分块起点的小章/小节，例如 1.1、1.2.3、4.2.2.1。unit 不会创建新的 section，只会切出 report unit。
- appendix: 附录、附件、附图、附表等附属大章，例如 12 附图。
- ignore: 目录条目、页眉页脚、图表题、检查项列项、正文误识别、只有局部列举意义的短语；ignore 不应作为新的评估分块边界。

判别要求：
1. 目标结构要粗：整份报告通常只有 10 到 12 个 chapter，正文中 1.x、1.x.x、1.x.x.x 都优先判为 unit，不要提升为 section/subsection/chapter。
2. 目录页上只有“目录”判为 toc，其余目录条目全部 ignore，即使它们看起来像 chapter 或 unit。
3. 单独数字标题只有在正文中按 1、2、3... 顺序出现，且不是目录条目、不是年份页码、不是局部列举项时，才判为 chapter。
4. `12 附图`、`附图`、`附件`、`附表` 这类附属内容判为 appendix；如果只想保留 11 个主体大章，appendix 不参与主体章节数量。
5. 点号编号标题如 2.1、2.2.3、4.2.2.1 只要位于正文主体，通常判为 unit。
6. `1）大坝坝顶`、`1、主坝`、`（1）评价依据`、`一、地形地貌`、`①I级响应` 等列举项或局部短语判为 ignore，除非上下文明确它是一个完整小节。
7. 标题附近正文预览只用于判断是否有独立正文承载；不要把表格名称、图名、检测项清单、运行制度清单误判为 chapter。
8. 如果无法可靠判断，优先 ignore；宁可少切分，也不要产生过细 unit。
9. 必须为每个输入 title_id 返回一条结果，不能遗漏，title_id 必须原样保留。
10. 输出必须严格满足给定 JSON Schema，只输出 JSON。"""


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


LLM_CHAPTER_SUMMARY_SYSTEM_PROMPT = """你是规范章节摘要生成器，负责根据某个 chapter 下提供的规范条文，生成一个简明、保守、可读的中文摘要。

要求：
1. 只能依据输入中的 chapter 标题和条文内容总结，不能补写输入中不存在的事实、数值、范围、流程或外部背景。
2. summary 使用中文，尽量控制在 80 到 180 字之间，概括这个 chapter 的核心主题、主要约束、检查重点或工作要求。
3. 不要逐条抄写所有 clause，也不要输出项目符号、编号列表或引用符号。
4. 如果输入条文较少，就基于现有内容做保守总结，不要为了凑完整而虚构。
5. 输出必须严格满足给定 JSON Schema。"""


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
            "toc": "目录标题本身。",
            "chapter": "正文主体一级大章，只用于 1、2、3... 这类大章。",
            "unit": "大章内部的小章或小节起点，例如 1.1、2.2.3、4.2.2.1。",
            "appendix": "附录、附件、附表、附图等附属大章。",
            "ignore": "目录条目、页眉页脚、图表题、列举项、局部短语或正文误识别。",
        },
        "previous_decisions": [
            {
                "title_id": item.get("title_id"),
                "text": item.get("text"),
                "page_idx": item.get("page_idx"),
                "role": item.get("role"),
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
                "previous_title": item.get("previous_title"),
                "next_title": item.get("next_title"),
                "preceding_text_preview": item.get("preceding_text_preview"),
                "following_text_preview": item.get("following_text_preview"),
                "numbering_pattern": item.get("numbering_pattern"),
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


def build_chapter_summary_prompt(
    standard_uid: str,
    chapter: dict[str, Any],
    clauses: Sequence[dict[str, Any]],
) -> str:
    payload = {
        "standard_uid": standard_uid,
        "task": "根据该 chapter 下的规范条文生成章节摘要，并用于写回 chapter 节点。",
        "chapter": {
            "chapter_id": chapter.get("node_uid"),
            "ref": chapter.get("ref"),
            "title": chapter.get("title"),
            "raw_text": chapter.get("raw_text"),
        },
        "clauses": [
            {
                "clause_ref": clause.get("clause_ref"),
                "clause_summary": clause.get("clause_summary"),
                "source_text_normalized": clause.get("source_text_normalized") or clause.get("source_text"),
            }
            for clause in clauses
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
