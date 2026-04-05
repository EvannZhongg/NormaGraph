# Graph Viewer

独立静态前端，直接消费 artifact 里的图谱 JSON，不依赖当前 FastAPI 后端。

## 打开方式

### 方式 1：直接打开页面

打开 `viewer/index.html`，优先直接选择 artifact 目录：

- 推荐直接选择 `data/artifacts/<artifact_id>`，页面会自动读取 `derived/graph_nodes.json`、`derived/graph_edges.json`
- 也支持直接选择 `derived` 目录
- `requirements.json` 会在存在时自动一起加载

如需手动导入，可展开页面里的“高级导入”，一次批量选择：

- `graph_nodes.json`
- `graph_edges.json`
- `requirements.json`（可选）

### 方式 2：启动独立静态服务器

```powershell
.\.venv\Scripts\python.exe scripts\serve_graph_viewer.py --artifact-dir data\artifacts\1_sl-258-2017-9be61aa3
```

脚本会打印一个本地 URL，浏览器打开后会自动加载该 artifact。

## 设计目标

- 不依赖当前后端 API
- 可直接浏览 standard、chapter、section、clause、requirement、concept、reference_standard
- 可查看节点文本、结构化属性、关联 requirement、相邻节点
- 图谱区只展示当前选中节点的局部邻域，避免一次性渲染全图过载
