# NormaGraph

面向规范文档知识图谱构建、图谱浏览和后续 Retrieval / QA / 对比能力扩展的最小可运行原型。

## 当前架构

当前项目已经切换为单服务部署架构：

- 后端使用 FastAPI
- 前端源码使用 React + Vite + Tailwind CSS，位于 `frontend/`
- 前端构建产物输出到 `webui/`
- FastAPI 在生产形态下直接静态托管 `webui/`
- 根路径 `/` 自动重定向到 `/webui/`
- 前端所有 API 请求都走同域相对路径，不保留独立前端生产服务器

## 当前能力

已完成：

- 支持 PDF / DOC / DOCX 输入，统一进入标准化与解析流程
- 对接 MinerU 在线 API，归档 `content_list_v2.json` 等解析产物
- 文档解析 artifact 固定保留在 `data/artifacts/<document_id>/`
- 标准图谱派生产物保留在 `data/kg_spaces/<standard_id>/`
- 完成 `content_list_v2.json -> 结构归一化 -> 条文切分 -> requirement extraction`
- 支持 `heuristic / llm / hybrid` 三种抽取模式
- 支持 embedding 本地输出与 PostgreSQL / pgvector 可选落库
- 提供 Documents / Knowledge Graph / Retrieval / API 四个前端工作区
- 提供文档上传、扫描、重试、删除、流水线状态查看
- 提供 kg space 切换、节点搜索、子图加载、布局切换、节点/关系编辑

尚未完成：

- Retrieval 后端问答链路尚未实现
- 报告对比工作流尚未实现
- 报告证据块切分与规范要求对齐尚未实现
- 多规范联合检索与跨图推理尚未实现

## 目录说明

- `src/main.py`
  - FastAPI 应用入口，同时挂载 `/webui`
- `src/api/routes.py`
  - HTTP 路由与接口暴露
- `src/core/config.py`
  - `.env + config.yaml` 配置加载
- `src/adapters/mineru_client.py`
  - MinerU 在线 API 适配层
- `src/adapters/llm_client.py`
  - OpenAI 兼容 `responses` / `embeddings` 客户端
- `src/services/normalization.py`
  - 文档标准化与本地预处理识别
- `src/services/standard_pipeline.py`
  - 规范建图主流水线
- `src/services/ingestion_service.py`
  - ingestion 任务调度、文档列表、kg space 查询与图谱编辑
- `src/repositories/job_store.py`
  - 任务状态存储
- `src/repositories/standard_registry.py`
  - 标准注册表
- `frontend/`
  - React + Vite + Tailwind 前端源码
- `webui/`
  - 前端构建产物，供 FastAPI 静态托管
- `data/artifacts/`
  - 文档解析产物目录
- `data/kg_spaces/`
  - 标准图谱空间目录
- `scripts/test_ingestion_pipeline.py`
  - 从源文件开始跑完整链路并打印日志
- `scripts/run_standard_pipeline.py`
  - 对已有 artifact 离线建图
- `scripts/ensure_postgres_db.py`
  - PostgreSQL 建库与 schema 初始化脚本

## 安装依赖

先安装 Python 侧依赖并注册命令行入口：

```powershell
uv pip install --python .\.venv\Scripts\python.exe -e .
```

前端依赖安装：

```powershell
Set-Location frontend
npm install
Set-Location ..
```

如果当前 Windows 环境对全局 npm / uv 缓存权限比较严格，可以改用项目内缓存：

```powershell
Set-Location frontend
npm install --cache .npm-cache --ignore-scripts --force
Set-Location ..
```

## 前端构建

前端源码位于 `frontend/`，构建产物输出到 `webui/`：

```powershell
Set-Location frontend
npm run build
Set-Location ..
```

构建完成后会生成：

- `webui/index.html`
- `webui/assets/*`

如果只改了后端、不改前端，可以直接复用已有 `webui/` 构建产物。

## 单服务启动

完成 `uv pip install -e .` 之后，推荐直接使用统一入口启动：

```powershell
normagraph-server
```

这条命令会启动 Uvicorn + FastAPI 进程，并且：

- 提供后端 API
- 提供 Swagger / OpenAPI
- 将已构建的前端静态资源挂载到 `/webui`
- 在同一个终端持续输出服务日志和状态

如果当前 shell 没有激活虚拟环境，也可以显式运行：

```powershell
.\.venv\Scripts\normagraph-server.exe
```

默认打开的入口：

- Web UI: `http://127.0.0.1:8010/webui/`
- 根路径重定向: `http://127.0.0.1:8010/`
- Swagger: `http://127.0.0.1:8010/docs`
- 健康检查: `http://127.0.0.1:8010/healthz`

说明：

- `normagraph-server` 本质上调用的是 `main:main`
- 如果 `webui/` 不存在，后端仍可启动，但 `/webui` 会返回缺少构建产物的提示

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
- `embedding.enabled`
  - 是否生成 embedding
- `postgres.enabled`
  - 是否写入 PostgreSQL / pgvector

当前仓库中的 `config.yaml` 示例已经切到 DashScope 兼容接口：

- LLM: `qwen3.5-plus`
- Embedding: `text-embedding-v4`

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

## 已有 artifact 的离线建图

如果 MinerU 结果已经存在，可以直接对 parse artifact 跑建图：

```powershell
.\.venv\Scripts\python.exe scripts\run_standard_pipeline.py --artifact-dir data\artifacts\<document_id> --standard-id sl258:2017
```

## PostgreSQL 初始化

```powershell
.\.venv\Scripts\python.exe scripts\ensure_postgres_db.py
```

如需临时强制执行一次：

```powershell
.\.venv\Scripts\python.exe scripts\ensure_postgres_db.py --force-enable
```

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

## 当前已实现接口

基础接口：

- `GET /healthz`
- `POST /v1/ingestions`
- `GET /v1/ingestions/{jobId}`

标准与 requirements：

- `GET /v1/standards`
- `GET /v1/standards/{standardId}`
- `GET /v1/standards/{standardId}/subgraph`
- `GET /v1/requirements/{requirementId}`

Documents：

- `GET /v1/documents`
- `GET /v1/documents/{documentId}/jobs`
- `POST /v1/documents/upload`
- `POST /v1/documents/{documentId}/retry`
- `DELETE /v1/documents/{documentId}`

Knowledge Graph：

- `GET /v1/kg-spaces`
- `GET /v1/kg-spaces/{standardId}`
- `GET /v1/kg-spaces/{standardId}/search`
- `GET /v1/kg-spaces/{standardId}/subgraph`
- `PATCH /v1/kg-spaces/{standardId}/nodes/{nodeId}`
- `PATCH /v1/kg-spaces/{standardId}/edges/{edgeId}`

当前仍为预留，接口会返回 `501`：

- `POST /v1/qa/ask`
- `POST /v1/comparisons`
- `GET /v1/comparisons/{comparisonId}`
- `GET /v1/comparisons/{comparisonId}/items`

## 当前已知问题

- MinerU 批任务创建成功后，仍可能在 OSS 上传阶段受本地网络环境影响
- 某些 OpenAI 兼容端点对 `/responses` 的支持并不完整，可能导致结构化输出漂移、超时或回退到启发式抽取
- 当 `embedding.enabled=true` 且 embedding 服务未就绪时，embedding 生成阶段会失败或超时
- 当 `postgres.enabled=true` 且 PostgreSQL 服务不可达、凭据错误或当前账号没有建库/建表权限时，建图流程会在落库阶段报错
- Retrieval / Comparison 后端尚未实现，前端页面当前为工作台占位和参数面板

## 当前建议

- 日常使用优先走 `normagraph-server` 单服务入口
- 前端改动后先执行 `frontend\npm run build`，再启动服务
- 排查解析或网络问题时，优先用 `scripts/test_ingestion_pipeline.py`
- 对已有 MinerU 产物做纯建图验证时，优先用 `scripts/run_standard_pipeline.py`
- 如果当前兼容端点对 `/responses` 支持不稳定，先保持 `fallback_to_heuristic_on_llm_error=true`
