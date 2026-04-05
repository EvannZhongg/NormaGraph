# NormaGraph

面向规范文档知识图谱、规范要求抽取、图谱浏览与后续 QA / 对比能力扩展的最小可运行原型。

## 当前进度

当前仓库已经形成一条可实际运行的规范建图闭环：

- 支持 PDF / DOC / DOCX 输入，统一进入标准化与解析流程
- 对接 MinerU 在线 API，拉取并归档 `content_list_v2.json` 等解析产物
- 文档解析 artifact 固定保留在 `data/artifacts/<document_id>/`，标准图谱派生产物单独落在 `data/kg_spaces/<standard_id>/`
- 完成 `content_list_v2.json -> 结构归一化 -> 条文切分 -> requirement extraction`
- 支持 `heuristic / llm / hybrid` 三种抽取模式
- LLM 抽取统一走 OpenAI 兼容的 `responses` 风格接口
- 已补齐结构化输出兼容层，可兼容 `items / results / clauses / extracted_requirements`
- 已支持 batch 重试、退避和并发执行，并保证最终仍按原始条文顺序汇总
- 可物化输出 `requirements.json`、`graph_nodes.json`、`graph_edges.json`、`embedding_inputs.jsonl`、`embedding_store.jsonl`
- 可选生成 embedding；当 `postgres.enabled=false` 时会写入本地 `embedding_store.jsonl`，启用时则可落库到 PostgreSQL / pgvector
- 已提供 FastAPI 查询接口和独立静态图谱前端 viewer
- 已提供前台脚本，便于直接观察解析、抽取、建图和产物输出日志

当前尚未完成：

- QA / RAG 工作流尚未实现
- 报告对比工作流尚未实现
- 报告证据块切分与规范要求对齐尚未实现
- 多规范联合检索与跨图推理尚未实现

## 目录说明

- `src/main.py`
  - FastAPI 应用入口
- `src/api/routes.py`
  - HTTP 路由与接口暴露
- `src/core/config.py`
  - `.env + config.yaml` 配置加载
- `src/adapters/mineru_client.py`
  - MinerU 在线 API 适配层
- `src/adapters/llm_client.py`
  - OpenAI 兼容 `responses` / `embeddings` 客户端
- `src/prompts.py`
  - 统一 prompt 管理
- `src/services/normalization.py`
  - 文档标准化与本地预处理识别
- `src/services/standard_pipeline.py`
  - 规范建图主流水线
- `src/services/llm_extraction.py`
  - 批量 LLM 抽取、兼容层、重试与并发控制
- `src/services/graph_materialization.py`
  - 图节点/边与 embedding 输入物化
- `src/services/ingestion_service.py`
  - ingestion 任务调度与 artifact 管理
- `src/repositories/postgres_graph_store.py`
  - PostgreSQL / pgvector 落库原型
- `src/resources/schemas/`
  - 运行时 JSON Schema 资源
- `data/artifacts/`
  - 文档解析产物目录，保留 MinerU / 标准化中间结果
- `data/kg_spaces/`
  - 标准图谱空间目录，保留 requirement / graph / metrics 等派生产物
- `scripts/test_ingestion_pipeline.py`
  - 从源文件开始跑完整链路并打印日志
- `scripts/run_standard_pipeline.py`
  - 对已有 artifact 离线建图
- `scripts/ensure_postgres_db.py`
  - PostgreSQL 建库与 schema 初始化脚本
- `scripts/serve_graph_viewer.py`
  - 独立静态图谱前端启动脚本
- `viewer/`
  - 不依赖当前后端 API 的图谱展示前端

## 安装依赖

```powershell
uv pip install --python .\.venv\Scripts\python.exe -e .
```

## 配置约定

### `.env`

至少会用到这些密钥：

- `MINERU_API_KEY`
- `LLM_API_KEY` 或 `config.yaml -> llm.api_key_env` 指定的变量名
- `EMBED_API_KEY` 或 `config.yaml -> embedding.api_key_env` 指定的变量名
- `POSTGRES_PASSWORD`

### `config.yaml`

当前已接入的配置域：

- `server`
- `storage`
- `mineru`
- `normalization`
- `knowledge_graph`
- `llm`
- `embedding`
- `postgres`

重点说明：

- `knowledge_graph.extraction_mode`
  - 可选 `heuristic / llm / hybrid`
- `knowledge_graph.fallback_to_heuristic_on_llm_error`
  - LLM 失败时是否自动回退到启发式抽取
- `llm.structured_output_mode`
  - 可选 `response_format / text_format / auto`
- `llm.enable_thinking`
  - 对某些 OpenAI 兼容端点建议显式关闭
- `llm.batch_max_retries`
  - 控制单个 clause batch 的最大重试次数
- `llm.batch_retry_backoff_seconds`
  - 控制重试退避时间
- `llm.batch_max_concurrency`
  - 控制 LLM batch 并发数；最终输出仍按原始顺序归并
- `embedding.enabled`
  - 是否生成 embedding
- `postgres.enabled`
  - 是否将图谱和向量写入 PostgreSQL / pgvector；关闭时会保留本地 `embedding_store.jsonl`
- PostgreSQL 首次启用
  - 如果目标数据库不存在，系统会在第一次落库时自动创建数据库，并初始化 `vector` 扩展、schema 和表结构

当前仓库中的 `config.yaml` 示例已经切到 DashScope 兼容接口：

- LLM: `qwen3.5-plus`
- Embedding: `text-embedding-v4`

## 启动 API

```powershell
.\.venv\Scripts\python.exe -m uvicorn --app-dir src main:app --host 127.0.0.1 --port 8010
```

打开：

- API 文档：`http://127.0.0.1:8010/docs`
- 健康检查：`http://127.0.0.1:8010/healthz`

## 前台测试脚本

推荐优先使用前台脚本排查问题，它会直接打印：

- 标准化阶段
- MinerU 上传 URL
- OSS 上传阶段
- 结果轮询阶段
- 图谱构建阶段
- 抽取 warning 与图谱统计
- 最终产物路径

### 从原始 PDF 开始跑完整链路

```powershell
.\.venv\Scripts\python.exe scripts\test_ingestion_pipeline.py `
  --source-path "Doc/1_SL 258-2017 水库大坝安全评价导则.pdf" `
  --document-type standard `
  --standard-id sl258:2017
```

### 仅测试 MinerU 解析，不建图

```powershell
.\.venv\Scripts\python.exe scripts\test_ingestion_pipeline.py `
  --source-path "Doc/1_SL 258-2017 水库大坝安全评价导则.pdf" `
  --document-type standard `
  --no-build-graph
```

### 强制启发式抽取

```powershell
.\.venv\Scripts\python.exe scripts\test_ingestion_pipeline.py `
  --source-path "Doc/1_SL 258-2017 水库大坝安全评价导则.pdf" `
  --document-type standard `
  --standard-id sl258:2017 `
  --disable-llm
```

### 临时覆盖本次运行的 LLM 超时

```powershell
.\.venv\Scripts\python.exe scripts\test_ingestion_pipeline.py `
  --source-path "Doc/1_SL 258-2017 水库大坝安全评价导则.pdf" `
  --document-type standard `
  --standard-id sl258:2017 `
  --llm-timeout-seconds 10
```

## 已有 artifact 的离线建图

如果 MinerU 结果已经存在，可以直接对 parse artifact 跑建图；脚本会从 `data/artifacts/<document_id>/` 读取解析结果，并将图谱产物写入 `data/kg_spaces/<standard_id>/`：

```powershell
.\.venv\Scripts\python.exe scripts\run_standard_pipeline.py --artifact-dir data\artifacts\<document_id> --standard-id sl258:2017
```

强制启发式：

```powershell
.\.venv\Scripts\python.exe scripts\run_standard_pipeline.py --artifact-dir data\artifacts\<document_id> --standard-id sl258:2017 --disable-llm
```

### 预初始化 PostgreSQL

如果希望在真正跑 ingestion 前先检查并初始化数据库，可以执行：

```powershell
.\.venv\Scripts\python.exe scripts\ensure_postgres_db.py
```

如果当前 `config.yaml` 里还是 `postgres.enabled: false`，也可以临时强制执行一次：

```powershell
.\.venv\Scripts\python.exe scripts\ensure_postgres_db.py --force-enable
```

## 独立图谱前端

仓库包含一个不依赖 FastAPI 后端的静态图谱展示页面：

- 页面目录：`viewer/`
- 启动脚本：`scripts/serve_graph_viewer.py`

直接启动示例：

```powershell
.\.venv\Scripts\python.exe scripts\serve_graph_viewer.py --artifact-dir data\kg_spaces\sl258-2017
```

viewer 当前支持两种方式：

- 通过启动脚本自动预加载某个 graph space；如果传入的是标准 artifact 目录，脚本也会根据 registry 自动解析到对应 graph space
- 直接打开 `viewer/index.html` 后，优先选择 `data/kg_spaces/<standard_id>`

如需手动导入，可使用页面里的“高级导入”一次性选择：

- `graph_nodes.json`
- `graph_edges.json`
- `requirements.json`

## 主要输出文件

文档解析产物保留在 `data/artifacts/<document_id>/`：

- `content_list_v2.json`
- `full.md`
- `layout.json`
- `images/`
- `*_origin.pdf` / `*_model.json` / `*_content_list.json`

规范图谱空间产物生成在 `data/kg_spaces/<standard_id>/`：

- `space_manifest.json`
- `normalized_blocks.json`
- `normalized_structure.json`
- `clauses.json`
- `requirements.json`
- `graph_nodes.json`
- `graph_edges.json`
- `embedding_inputs.jsonl`
- `embedding_store.jsonl`
- `segmentation_metrics.json`
- `segmentation_report.md`

常见指标包括：

- `requirement_count`
- `graph_node_count`
- `graph_edge_count`
- `embedding_generation_status`
- `postgres_persist_status`
- `llm_retried_batch_count`
- `llm_retry_attempt_count`
- `llm_batch_max_concurrency`

## 当前已实现接口

- `GET /healthz`
- `POST /v1/ingestions`
- `GET /v1/ingestions/{jobId}`
- `GET /v1/standards`
- `GET /v1/standards/{standardId}`
- `GET /v1/standards/{standardId}/subgraph`
- `GET /v1/requirements/{requirementId}`

当前仍为预留，接口会返回 `501`：

- `POST /v1/qa/ask`
- `POST /v1/comparisons`
- `GET /v1/comparisons/{comparisonId}`
- `GET /v1/comparisons/{comparisonId}/items`

## 当前已知问题

- MinerU 批任务创建成功后，仍可能在 OSS 上传阶段受本地网络环境影响。
- 某些 OpenAI 兼容端点对 `/responses` 的支持并不完整，可能导致结构化输出形状漂移、超时或回退到启发式抽取。
- 当 `embedding.enabled=true` 且 embedding 服务未就绪时，embedding 生成阶段会失败或超时。
- 当 `postgres.enabled=true` 且 PostgreSQL 服务不可达、凭据错误，或当前账号没有建库/建表权限时，建图流程会在落库阶段报错；若仅数据库不存在，系统会自动创建。
- viewer 当前是静态局部邻域图，不是全图大规模布局引擎。

## 当前建议

- 排查解析或网络问题时，优先用 `scripts/test_ingestion_pipeline.py`。
- 对已有 MinerU 产物做纯建图验证时，优先用 `scripts/run_standard_pipeline.py`。
- 做图谱展示或验收时，优先使用 `viewer/` 静态前端。
- 做前后端联调时，再使用 FastAPI ingestion 接口。
- 如果当前兼容端点对 `/responses` 支持不稳定，先保持 `fallback_to_heuristic_on_llm_error=true`。
