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
8. 对每个输入条文都必须返回一个结果项，使用原样的 clause_uid 和 clause_ref；如果没有规范性要求，则 requirements 返回空数组。
9. 输出必须严格满足给定 JSON Schema。"""


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


LLM_STANDARD_TITLE_PLANNING_SYSTEM_PROMPT = """你是水利水电工程规范的标题规划器。输入来自 PDF 解析后被标为 title 的文本块，其中可能混有目录项、页眉页脚、正文条文、附录标题和噪声。你的任务是生成稳定、保守的 title plan，用于后续规范图谱抽取的结构切分。

只允许输出以下 role：
- toc: 只有“目录”标题本身。
- appendix: 附录、附件、附表、附图等附属结构标题。
- reference_standard: “引用标准 / 规范性引用文件”这类章节。
- chapter: 正文主体一级章，例如 1 总则、7 防洪能力复核。
- section: 章下二级节，例如 2.1、7.3；规范结构只保留 chapter / section / clause 三层。
- clause: 规范条文层，例如 1.0.1、2.3.4。即使 OCR 把条文误标为 title，也必须判为 clause。
- ignore: 目录条目、封面标题、页眉页脚、图表题、列项、噪声、正文中的局部短语。

判别要求：
1. 目录页上只有“目录”标题本身判为 toc，其余目录条目全部 ignore，即使看起来像 chapter、section 或 clause。
2. 标准正文只建立三层：chapter / section / clause。1、2、3 这类一级编号通常是 chapter；1.1、2.3 通常是 section；1.0.1、2.3.4 通常是 clause。
3. 如果文本语义上就是“引用标准 / 规范性引用文件”，优先判为 reference_standard，而不是普通 chapter。
4. 如果文本本身像完整条文句子，或以 1.0.1 / 2.3.4 这类条款编号开头，优先判为 clause。
5. 如果文本以 6.4.1～6.4.4、3.2.1-3.2.3 这类条款范围编号开头，本质上仍按 clause 处理。
6. 只有明确是附录/附件/附表/附图时才能判为 appendix。
7. `1）大坝坝顶`、`1、主坝`、`（1）评价依据`、`一、地形地貌` 等列举项或局部短语判为 ignore。
8. ref 字段应填写对应编号：chapter 填 1，section 填 2.1，clause 填 2.1.3，appendix 填 A；无法可靠给出时填 null。
9. 如果无法可靠判断，优先 ignore；不要用结构类兜底。
10. 必须为每个输入 title_id 返回一条结果，不能遗漏，title_id 必须原样保留。
11. 输出必须严格满足给定 JSON Schema，只输出 JSON。"""


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


LLM_REPORT_SECTION_SUMMARY_SYSTEM_PROMPT = """你是水利水电工程安全评价报告的章节摘要生成器。输入来自 title planner 判断后的 report section 及其包含的 report unit 文本。

任务：
1. 为整个 section 生成一段中文总概括，用于后续规范 chapter / section 路由判断。
2. 为输入中的每个 report unit 生成一段中文概括，帮助后续判断该 unit 应关联哪些规范范围。
3. 总概括应综合 section 标题、路径、unit 标题和 unit 文本，说明本章评价对象、主要检查/复核主题、关键结论或问题类型。
4. unit 概括应保留该 unit 的核心评价语义，不要只复述标题；表格 unit 应概括表格用途和主要信息类型。
5. 只能依据输入内容总结，不能补写输入中不存在的工程事实、数值、缺陷、结论或整改措施。
6. 不要大段照抄原文，不要输出 Markdown、编号列表或额外解释。
7. 必须为每个输入 unit 返回一个 `unit_summaries` 项，`unit_uid` 必须原样复制。
8. 输出必须严格满足给定 JSON Schema。"""


RAG_SCOPE_ROUTING_SYSTEM_PROMPT = """你是水利水电规范知识图谱问答的检索路由器。你的任务是根据用户问题，在给定的 chapter summary 与 section label 中选择最可能相关的范围，用于后续 clause / requirement 向量召回。

要求：
1. 只能依据输入候选的 ref、title、summary、section labels 判断，不要补写外部知识。
2. 优先选择能覆盖问题主题的 chapter；如果能进一步定位到 section，则选择对应 section。
3. 不要为了凑数量选择无关范围；如果问题较泛，可以只选 chapter。
4. 输出必须严格满足 JSON Schema。"""


RAG_ANSWER_SYSTEM_PROMPT = """你是水利水电规范知识图谱问答助手。你只能依据输入的检索上下文回答问题。

要求：
1. 只能使用检索上下文中的章节路径、条文号、clause 原文、requirement_text、judgement_criteria、evidence_expected 作答。
2. 不得编造上下文中不存在的事实、数值、条文或外部规范。
3. 如果问题包含多个并列子问、多个条件或多个条文要求，必须逐项完整回答，不能只答第一部分。
4. 如果检索上下文已包含对应条文，就直接给出答案，不要因为答案较长或包含多个相关要求而说“上下文不足”。
5. 回答应使用中文，优先按条文/要求组织，保持简洁。
6. 引用优先使用 clause_ref；如需要更细粒度引用，系统会在响应层补充 node_uid。"""


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


def build_standard_title_planning_prompt(
    standard_uid: str,
    previous_titles: Sequence[dict[str, Any]],
    current_titles: Sequence[dict[str, Any]],
) -> str:
    payload = {
        "standard_uid": standard_uid,
        "task": "请为当前批标题生成 title plan，判断其真实结构角色，供后续规范图谱切分使用。",
        "role_definitions": {
            "toc": "目录标题本身。",
            "appendix": "附录、附件、附表、附图等附属结构标题。",
            "reference_standard": "引用标准 / 规范性引用文件章节。",
            "chapter": "正文一级章，例如 1 总则。",
            "section": "章下二级节，例如 2.1 一般规定。",
            "clause": "规范条文，例如 2.1.3 应符合……。",
            "ignore": "目录条目、封面标题、页眉页脚、图表题、列项、噪声或局部短语。",
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
                "text_normalized": item.get("text_normalized"),
                "raw_title_level": item.get("raw_title_level"),
                "previous_title": item.get("previous_title"),
                "next_title": item.get("next_title"),
                "preceding_text_preview": item.get("preceding_text_preview"),
                "following_text_preview": item.get("following_text_preview"),
                "numbering_pattern": item.get("numbering_pattern"),
                "looks_structural": item.get("looks_structural"),
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


def build_report_section_summary_prompt(
    document_id: str,
    section: dict[str, Any],
    units: Sequence[dict[str, Any]],
) -> str:
    payload = {
        "document_id": document_id,
        "task": "根据 title planner 切出的 report section 和其下 report units 生成路由辅助摘要。",
        "output_usage": "该摘要会作为 Run Evaluation 中 report section 第一次规范路由的主要输入。",
        "required_summary_format": {
            "overall_summary": "一段总概括。",
            "unit_summaries": "每个 unit 一段概括，必须覆盖所有输入 unit_uid。",
        },
        "section": {
            "section_uid": section.get("section_uid"),
            "title": section.get("title"),
            "section_kind": section.get("section_kind"),
            "path": section.get("path") or [],
            "structural_path": section.get("structural_path") or [],
            "page_span": section.get("page_span") or [],
        },
        "units": [
            {
                "unit_uid": unit.get("unit_uid"),
                "title": unit.get("title"),
                "unit_type": unit.get("unit_type"),
                "local_heading_path": unit.get("local_heading_path") or [],
                "source_page_span": unit.get("source_page_span") or [],
                "text": unit.get("text_for_summary") or "",
            }
            for unit in units
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_rag_scope_routing_prompt(
    question: str,
    standard_uid: str,
    chapters: Sequence[dict[str, Any]],
    sections: Sequence[dict[str, Any]],
    *,
    top_k: int,
) -> str:
    sections_by_chapter: dict[str, list[dict[str, Any]]] = {}
    for section in sections:
        chapter_id = str(section.get("chapter_id") or "")
        if chapter_id:
            sections_by_chapter.setdefault(chapter_id, []).append(section)

    payload = {
        "standard_uid": standard_uid,
        "task": "根据用户问题选择相关 chapter / section 粗定位范围。",
        "question": question,
        "selection_limits": {
            "max_chapters": max(1, min(top_k, 8)),
            "max_sections": max(0, min(top_k * 2, 16)),
        },
        "chapters": [
            {
                "node_uid": chapter.get("node_uid"),
                "ref": chapter.get("ref"),
                "title": chapter.get("title") or chapter.get("label"),
                "summary": chapter.get("summary") or chapter.get("text_content") or "",
                "sections": [
                    {
                        "node_uid": section.get("node_uid"),
                        "ref": section.get("ref"),
                        "title": section.get("title") or section.get("label"),
                    }
                    for section in sections_by_chapter.get(str(chapter.get("node_uid") or ""), [])
                ],
            }
            for chapter in chapters
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_rag_answer_prompt(
    question: str,
    standard_uid: str,
    retrieval_contexts: Sequence[dict[str, Any]],
    *,
    user_prompt: str | None = None,
) -> str:
    payload = {
        "standard_uid": standard_uid,
        "task": "基于检索上下文回答用户问题，并给出 node_uid / clause_ref 引用。",
        "question": question,
        "user_prompt": user_prompt or "",
        "context_priority": [
            "chapter_path",
            "clause_ref",
            "clause_text",
            "requirement_text",
            "judgement_criteria",
            "evidence_expected",
        ],
        "retrieval_contexts": [
            {
                "chapter_path": item.get("chapter_path") or [],
                "clause_ref": item.get("clause_ref"),
                "clause_text": item.get("clause_text"),
                "requirement_text": item.get("requirement_text"),
                "judgement_criteria": item.get("judgement_criteria") or [],
                "evidence_expected": item.get("evidence_expected") or [],
            }
            for item in retrieval_contexts
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
