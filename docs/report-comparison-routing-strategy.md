# Report Comparison Routing Strategy

## 背景

当前 report comparison 已经完成了两项关键重构：

- report 分割由 LLM `title_plan` 驱动，主体报告稳定形成 10 到 12 个大章。
- 大章内部的 `1.1 / 1.2.3 / 4.2.2.1` 等标题作为 `report_unit` 边界，而不是继续创建 report section。

因此，comparison 阶段面对的结构已经从“多层 section”变成了：

```text
report section = 粗粒度主体大章
report unit    = 大章下的真实业务小节 / 小节组 / 表格
```

这会直接影响规范图谱探索策略。原有“按 report section 路由一次，再复用给所有 unit”的策略能降低 LLM 调用量，但在新的 report 结构下粒度偏粗，容易造成候选规范范围过宽、局部失败扩大化，以及 coverage 解释不清。

本文档用于指导后续 report comparison 的路由、条款探索、结果聚合与图谱展示策略。

## 当前策略

当前流程分为四步。

### 1. 构建规范候选层级

从 KG space 中读取节点和 `CONTAINS` 边，构建三类候选：

- `chapter_candidates`
- `section_candidates`
- `clause_candidates`

候选结构大致为：

```text
standard
  -> chapter
      -> section
          -> clause
```

如果某个 chapter 没有显式 section，会创建一个 `chapter_scope`，让 clause 可以直接挂在 chapter 下参与比较。

### 2. 构建 report section scope

系统按 `parentSectionUid` 把 report units 分组。

```text
section: 5 运行管理评价
  -> unit: 5.1 大坝管理运行
  -> unit: 5.2 水库调度运行
  -> unit: 5.3 工程养护维修
  -> unit: 5.4 运行管理评价结论
```

随后把同一 section 下所有 unit 的文本拼接成一个 section scope。

### 3. section scope 做规范路由

每个 section scope 调用 LLM 两次：

```text
section scope
  -> route standard chapters
  -> route standard sections within selected chapters
```

路由结果写入：

```text
routing_by_scope[section_uid] = {
  chapter_ids,
  section_ids,
  selected_chapters,
  selected_sections
}
```

### 4. unit 级 clause discovery

每个 report unit 不再单独选择规范 chapter / section，而是复用所属 section 的路由结果。

```text
report unit
  -> reuse selected_sections from parent section
  -> collect clauses under selected_sections
  -> LLM returns only matched clauses:
       covered / violated
```

最终聚合时，按唯一 clause id 合并所有 unit 证据：

```text
violated > covered > missing
```

只要任意 unit 违反某条 clause，该 clause 最终为 `violated`；否则只要任意 unit 覆盖该 clause，最终为 `covered`；否则为 `missing`。

## 同一个 Section 下的图谱探索是否相同

当前策略下，同一个 report section 下的所有 unit 共享相同的规范探索范围。

也就是说，同一个 section 下的 unit 通常具有相同的：

- `matchedChapterIds`
- `matchedSectionIds`
- 候选 clause 集合

但它们的最终命中结果不一样：

- 每个 unit 会单独进行 clause discovery。
- 每个 unit 只输出自己文本明确覆盖或违反的 clause。
- 图谱中每个 unit 到 clause 的 `COVERED / VIOLATED` 边是独立的。

因此：

```text
同 section 下探索范围一样
同 section 下实际 covered / violated 不一定一样
```

在图谱展示上，同 section 下的 unit 往往共享相似的 chapter / section 骨架，但 clause 节点和命中边由 unit 自己决定。

## 当前策略的优点

### 调用量低

如果每个 unit 都做 chapter routing 和 section routing，调用量会明显增加。

当前策略只对 section scope 做路由：

```text
route calls ~= section_count * 2
assessment calls ~= unit_count
```

对于 181 个 unit、13 个 section 的报告，路由调用量会从数百次降到几十次。

### 路由更稳定

单个 unit 文本可能很短，例如只有标题、表格或几句结论。用整个 section 的上下文做路由，通常更容易选到正确规范章节。

### 聚合逻辑清晰

unit 只负责输出明确命中的 `covered / violated`，最终状态由全局 clause 聚合得到，避免了局部 `missing` 被误当成全局缺失。

## 当前策略的问题

### 1. 路由粒度偏粗

新的 report section 是主体大章，而不是细分小节。一个大章内可能包含多个主题。

典型例子：

```text
11 大坝安全综合评价
  -> 11.2 运行管理评价
  -> 11.3 防洪能力复核
  -> 11.4 渗流安全评价
  -> 11.5 结构安全评价
  -> 11.6 抗震安全评价
  -> 11.7 金属结构安全评价
```

如果按 `11 大坝安全综合评价` 整章路由，选出的规范范围会覆盖运行管理、防洪、渗流、结构、抗震、金属结构等多个章节。后续每个 unit 都会在这组过大的候选 clause 中探索。

风险：

- 候选 clause 过多。
- LLM discovery 难度上升。
- 弱相关条款更容易混入。
- coverage 解释变差。

### 2. section 路由失败会扩大影响

因为同 section 下所有 unit 复用同一份 routing，所以 section scope 路由失败时，整个大章下所有 unit 都会失败。

例如某个 section 下有 21 个 unit，只要该 section 的 routing payload 解析失败，这 21 个 unit 都会被标记为 failed。

### 3. 细粒度 unit 语义没有参与路由

当前 unit 只参与 clause discovery，不参与 chapter / section routing。

这对单主题章节没有明显问题，但对综合章或跨主题章不够精细。例如 `11.2 运行管理评价` 明明应该主要路由到运行管理规范，却会继承整个 `11 大坝安全综合评价` 的大范围候选。

### 4. missing 容易被产品层误读

当前最终 `missing` 是“没有任何 unit 命中该 clause”。这比旧的局部 missing 更合理，但仍需注意：

```text
missing != 报告一定必须覆盖但没有覆盖
```

它更准确的语义是：

```text
在当前探索策略和证据判断下，该 clause 未找到 covered / violated 证据
```

如果候选范围过粗或过窄，missing 数量都会受到影响。

## 推荐目标策略

推荐采用“section 级 unit routing plan + unit clause discovery”的主流程，并保留 unit 精路由作为 fallback。

核心变化是：section 阶段不只输出“这个大章相关的规范 chapter / section 列表”，而是直接输出“每个 report unit 应挂载到哪些规范 chapter / section”。这样可以利用 section 的全局视野，同时避免把整章召回的全部规范范围无差别分配给每个 unit。

```text
report section
  -> build unit routing plan
       unit A -> standard chapter / section set A
       unit B -> standard chapter / section set B

report unit
  -> use its own route from unit routing plan
  -> fallback to unit refine route when route is missing / low confidence
  -> discover covered / violated clauses

all unit results
  -> aggregate by unique clause id
```

### 阶段 1：Section 级 Unit Routing Plan

section scope 仍然保留，但目标从“粗召回一组规范范围”升级为“为 section 下的每个 unit 分配规范范围”。

输入：

- section title
- section path
- section 下 unit 标题列表
- section 文本摘要或截断文本
- 可选：每个 unit 的短文本 preview

输出：

- `unit_routes`
- 可选：section 级 `coarse_chapter_ids / coarse_section_ids`
- 可选：`broad_scope`、路由置信度与主题说明

推荐输出形态：

```json
{
  "section_uid": "report:xxx:section:12",
  "section_title": "11 大坝安全综合评价",
  "broad_scope": true,
  "unit_routes": [
    {
      "unit_uid": "report:xxx:unit:173",
      "unit_title": "11.2 运行管理评价",
      "chapter_ids": ["sl258:2017:chapter:6"],
      "section_ids": ["sl258:2017:section:6.2", "sl258:2017:section:6.3"],
      "reasoning": "该 unit 评价运行管理、划界确权和安全监测资料整编。"
    },
    {
      "unit_uid": "report:xxx:unit:174",
      "unit_title": "11.3 防洪能力复核",
      "chapter_ids": ["sl258:2017:chapter:7"],
      "section_ids": ["sl258:2017:section:7.2", "sl258:2017:section:7.3", "sl258:2017:section:7.5"],
      "reasoning": "该 unit 复核防洪标准、设计洪水、调洪和坝顶高程。"
    }
  ]
}
```

挂载关系允许多对多：

```text
one standard chapter / section -> many report units
one report unit -> many standard chapters / sections
```

例如 `SL258-2017 运行管理评价` 可以同时挂到：

```text
5.1 大坝管理运行
5.2 水库调度运行
5.4 运行管理评价结论
11.2 运行管理评价
11.9 建议
```

这符合报告写作习惯：同一类规范要求可能出现在过程章节、评价结论和建议章节中。

建议限制：

```text
每个 unit chapter_ids: 1 到 3 个
每个 unit section_ids: 1 到 6 个
section coarse chapter_ids: 1 到 6 个
section coarse section_ids: 3 到 18 个
```

对于综合章可以允许 section coarse 范围更宽，但每个 unit 的 route 仍应尽量窄。

### 阶段 2：Unit Route 使用与 Fallback

每个 unit 优先使用 section 阶段生成的 `unit_routes`。只有以下情况才进行单独 unit 精路由：

- 该 unit 没有出现在 `unit_routes` 中。
- 该 unit 的 `section_ids` 为空。
- 该 unit route 标记为低置信度。
- unit 文本与分配的规范范围明显不一致。

输入：

- unit title
- unit text
- parent section title
- section 阶段的 coarse candidates
- section 阶段已分配的 unit route

输出：

- unit 自己的 `matchedSectionIds`
- route source，例如 `section_unit_plan`、`unit_refine`、`section_fallback`

建议限制：

```text
unit matched sections: 1 到 6 个
```

如果 unit 是综合结论、建议、汇总表，可允许更多。

### 阶段 3：Clause Discovery

每个 unit 只在自己的 route 下探索 clause。

LLM 只返回有明确证据的条款：

- `covered`
- `violated`

不返回：

- `missing`
- `partial`
- `not_applicable`

证据不足时不输出该 clause。

## 推荐策略分支

### 普通章节

适用于：

- `3 安全监测资料分析`
- `4 工程质量评价`
- `6 防洪能力复核`
- `8 结构安全评价`

策略：

```text
section unit routing plan
unit uses assigned route
unit clause discovery
```

如果 section 内 unit 主题高度一致，也可以让多个 unit 共享同一组 route，但仍应显式写入每个 unit 的 `unit_routes`，不要依赖隐式整章继承。

### 综合章节

适用于：

- `11 大坝安全综合评价`
- `建议`
- `结论`
- 同一章下包含多个专项评价的小节

策略：

```text
section unit routing plan is required
each unit must receive its own route
```

综合章不能直接复用整章 routing 作为最终候选范围。尤其是 `11 大坝安全综合评价` 这类章节，应在 section 阶段把运行管理、防洪、渗流、结构、抗震、金属结构等规范范围分别挂载到对应 unit。

### 目录、附图、纯图表

建议默认不进入 comparison，或只在明确需要时进入低优先级探索。

过滤条件可包括：

- `section_kind == toc`
- `section_kind == appendix`
- unit 文本只包含附图清单
- unit 文本过短且没有评价语义

这类内容不应影响 coverage。

### 表格 Unit

表格仍可参与 comparison，但需要注意表格 prompt 应优先传 `html`，不要重复传 markdown 文本。

## 结果聚合口径

最终规则实体仍固定为 clause。

推荐最终状态：

```text
violated > covered > missing
```

定义：

- `violated`：任一 unit 明确违反该 clause。
- `covered`：无违反，且任一 unit 明确覆盖该 clause。
- `missing`：没有任何 covered / violated 证据。

建议额外增加统计字段，避免误读：

- `evaluated_clause_count`：实际进入过 unit discovery 的唯一 clause 数。
- `total_clause_count`：规范图谱中全部 clause 数。
- `covered_count`
- `violated_count`
- `missing_count`
- `not_explored_count`
- `failed_unit_count`
- `failed_scope_count`

其中：

```text
not_explored = total_clause_count - evaluated_clause_count
```

这比把所有未命中 clause 都直接叫 missing 更清楚。

## 图谱展示建议

### Unit 级图谱

Unit 图谱应展示：

- 当前 report unit 节点
- section unit plan 分配给该 unit 的规范 chapter / section
- 当前 unit 命中的 covered / violated clause
- report unit 到 clause 的证据边

不建议展示 parent section 的全部 coarse clause，否则图谱会很大且噪声较多。

### Section 级图谱

Section 图谱应展示：

- report section 节点
- section 下 unit 节点
- section unit plan 中涉及的规范 chapter / section
- 所有 unit 命中的 clause 汇总

Section 图谱适合看“这一章下每个 unit 被分配到了哪些规范范围，以及最终命中了哪些 clause”。

### Report 级图谱

Report 图谱应展示唯一 clause 最终状态：

- covered clause
- violated clause
- missing / not_explored clause 可按需折叠

默认不要把所有 missing clause 全展开。

## 错误处理策略

### Section 路由失败

不要让整个 section 下所有 unit 直接失败。

推荐 fallback：

```text
section unit routing plan failed
  -> each unit performs direct unit route against all chapters
  -> if unit route succeeds, continue assessment
  -> if unit route fails, only该 unit failed
```

### Unit Route 缺失或失败

推荐：

```text
unit route missing / invalid
  -> unit refine route within section coarse candidates
  -> if still failed, fallback to section coarse route
  -> mark route_source = unit_refine / section_fallback

unit refine route failed
  -> fallback to section coarse route
  -> mark route_source = section_fallback
```

这样可以保留可用结果，同时在指标中暴露风险。

### Clause Discovery 输出异常

当前做法可以保留：

- 格式错误可重试。
- 多次失败后只标记当前 unit failed。
- 不影响其他 unit。

### Violated Summary 输出异常

缺陷摘要不应影响 clause 状态。

推荐：

- 优先使用纯文本 summary。
- structured summary 失败时不要输出 ERROR 级别日志。
- summary 失败时保留基础统计摘要。

## Prompt 设计原则

### Section Unit Routing Plan Prompt

应强调：

- 任务是为 section 下每个 report unit 分配规范 chapter / section。
- 一个规范 chapter / section 可以挂载到多个 unit。
- 一个 unit 可以挂载多个规范 chapter / section。
- 每个 unit 的 route 应尽量窄，不要默认继承整章全部候选。
- 综合章要按 unit 标题拆分主题，例如运行管理、防洪、渗流、结构、抗震、金属结构。
- 输出 id 必须来自候选，不得编造。
- 如果某个 unit 没有评价语义，应输出空 route 并给出简短原因。

### Unit 精路由 Prompt

应强调：

- 只在 section unit plan 缺失、低置信或疑似错误时调用。
- 只围绕当前 unit 文本和标题。
- 不要继承 parent section 的所有主题。
- 综合章下的小节要按小节标题选择规范范围。
- 输出少量最相关 section。

### Clause Discovery Prompt

应强调：

- 只返回明确证据命中的 clause。
- 不要输出 missing、partial、not_applicable。
- 违反项必须有报告原文证据。
- 弱相关、背景性提及、无法判断时不输出。

## 推荐实施阶段

### 第一阶段：修正稳定性

目标：

- 兼容 LLM 路由返回 list / dict / compact mapping。
- 避免 section 路由失败扩大成整章 unit 失败。
- violated summary 使用纯文本 fallback。
- comparison 输出中明确 `failed_scope_count` 和 `failed_unit_count`。

验收：

- 不再出现同一 section 下大量 unit 因同一 payload 解析错误失败。
- comparison 文件能区分 routing failed 与 assessment failed。

### 第二阶段：引入 Section Unit Routing Plan

目标：

- section 阶段直接输出 `unit_routes`。
- 每个 unit 优先使用自己的 route。
- 一个规范 chapter / section 可以挂载到多个 unit。
- 综合章强制输出 unit 级 route，不允许整章全量继承。
- unit 精路由只作为 route 缺失、低置信或异常时的 fallback。

验收：

- 同一 section 下不同 unit 的 `matchedSectionIds` 可以不同。
- `11 大坝安全综合评价` 下的运行管理、防洪、渗流、结构、抗震、金属结构 unit 能分别路由到对应规范 section。
- 同一个规范 chapter / section 可以出现在多个 unit 的 route 中。
- 每个 unit result 记录 `routeSource`，例如 `section_unit_plan`、`unit_refine`、`section_fallback`。

### 第三阶段：改进统计口径

目标：

- 区分 `missing` 与 `not_explored`。
- 输出唯一 clause 状态。
- 输出探索覆盖率与证据覆盖率。

推荐指标：

```text
exploration_rate = evaluated_clause_count / total_clause_count
coverage_rate    = covered_count / total_clause_count
violation_rate   = violated_count / total_clause_count
failure_rate     = failed_unit_count / total_unit_count
```

### 第四阶段：优化图谱展示

目标：

- Unit 图只显示 unit 命中的 clause 与 assigned route 范围。
- Section 图显示 section unit plan 与所有 unit 的命中汇总。
- Report 图显示最终唯一 clause 状态。

## 判断当前结果时的注意事项

当 comparison 输出包含 failed units 时，coverage 不应被视为稳定结果。

例如：

```text
clauses=363, covered=43, violated=2, missing=318, failed_units=73
```

这说明：

- 363 是规范图谱中的 clause 总量。
- 43 / 2 是当前成功 unit 已找到的唯一 covered / violated clause。
- 318 是当前没有证据命中的 clause。
- 73 个 unit 没有完成评估，会影响最终 coverage。

因此，在 failed units 修复前，不建议用该 coverage 作为最终质量判断。

## 推荐默认策略

短期默认策略：

```text
section unit routing plan
  -> unit clause discovery using assigned unit route
  -> missing / invalid unit route falls back to direct unit route
```

中期默认策略：

```text
section unit routing plan
  -> unit route validation
  -> unit refine route only when needed
  -> unit clause discovery
  -> aggregate unique clause status
```

长期默认策略：

```text
adaptive routing:
  ordinary section: section unit plan may assign the same route to multiple units
  broad / mixed section: section unit plan must assign different routes by unit topic
  short / noisy unit: use parent section route with low confidence
  table unit: classify table purpose before route
```

## 结论

当前策略的核心问题不是 clause 聚合，而是“路由粒度与新的 report 结构不匹配”。

在 title_plan 重构后，report section 已经变成大章，report unit 才是业务小节。因此 comparison 不应长期把 section routing 当作所有 unit 的最终规范范围。更稳妥的方向是：

```text
大章负责生成 unit routing plan
unit 使用自己的规范挂载范围
clause discovery 只输出明确证据
最终按唯一 clause 聚合
```

这样既能保留 section 阶段的全局判断能力，又能让每个 unit 拿到更窄、更准确的规范候选范围。一个规范 chapter / section 可以挂载到多个 unit，但每个 unit 不应默认继承 section 的全量候选。
