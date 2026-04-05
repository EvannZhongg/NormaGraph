const TYPE_COLORS = {
  standard: '#0f766e',
  chapter: '#1d4ed8',
  section: '#2563eb',
  appendix: '#7c3aed',
  clause: '#d97706',
  requirement: '#dc2626',
  concept: '#059669',
  reference_standard: '#7c2d12',
  default: '#64748b',
};

const EDGE_COLORS = {
  CONTAINS: 'rgba(29, 78, 216, 0.28)',
  DERIVES_REQUIREMENT: 'rgba(220, 38, 38, 0.28)',
  ABOUT: 'rgba(5, 150, 105, 0.22)',
  CITES_STANDARD: 'rgba(124, 45, 18, 0.32)',
  NEXT: 'rgba(100, 116, 139, 0.2)',
  default: 'rgba(31, 43, 40, 0.16)',
};

const state = {
  nodes: [],
  edges: [],
  requirements: [],
  nodesById: new Map(),
  adjacency: new Map(),
  edgesBySource: new Map(),
  edgesByTarget: new Map(),
  nodeDegree: new Map(),
  requirementsByClause: new Map(),
  requirementsByNode: new Map(),
  selectedNodeId: null,
  searchText: '',
  activeNodeTypes: new Set(),
  activeEdgeTypes: new Set(),
  depth: 1,
  neighborLimit: 30,
  sourceLabel: '尚未加载',
};

const refs = {};

document.addEventListener('DOMContentLoaded', () => {
  bindRefs();
  bindEvents();
  renderEmptyShell();
  maybeLoadFromQuery();
});

function bindRefs() {
  [
    'folder-input', 'manual-files-input', 'data-source', 'selection-status',
    'depth-indicator', 'stats-grid', 'summary-pill', 'search-input', 'depth-select', 'neighbor-limit-select',
    'type-filters', 'edge-filters', 'reset-filters', 'node-list', 'list-count', 'graph-title', 'graph-stage',
    'graph-empty', 'legend', 'detail-type', 'detail-title', 'detail-uid', 'detail-text', 'detail-properties',
    'detail-requirements', 'detail-neighbors', 'focus-standard-button', 'stat-card-template'
  ].forEach((id) => {
    refs[toCamel(id)] = document.getElementById(id);
  });
}

function bindEvents() {
  refs.folderInput.addEventListener('change', handleFolderSelection);
  refs.manualFilesInput.addEventListener('change', handleManualFilesSelection);
  refs.searchInput.addEventListener('input', (event) => {
    state.searchText = event.target.value.trim().toLowerCase();
    renderNodeList();
    renderGraph();
  });
  refs.depthSelect.addEventListener('change', (event) => {
    state.depth = Number(event.target.value || 1);
    refs.depthIndicator.textContent = `${state.depth} 跳`;
    renderGraph();
  });
  refs.neighborLimitSelect.addEventListener('change', (event) => {
    state.neighborLimit = Number(event.target.value || 30);
    renderGraph();
  });
  refs.resetFilters.addEventListener('click', resetFilters);
  refs.focusStandardButton.addEventListener('click', focusStandardRoot);
}

async function maybeLoadFromQuery() {
  const params = new URLSearchParams(window.location.search);
  const nodesUrl = params.get('nodes');
  const edgesUrl = params.get('edges');
  const requirementsUrl = params.get('requirements');
  const title = params.get('title');
  if (!nodesUrl || !edgesUrl) {
    return;
  }
  try {
    const [nodes, edges, requirements] = await Promise.all([
      fetchJson(nodesUrl),
      fetchJson(edgesUrl),
      requirementsUrl ? fetchJson(requirementsUrl) : Promise.resolve([]),
    ]);
    ingestDataset({
      nodes,
      edges,
      requirements,
      sourceLabel: title || `${basename(resolveDataUrl(nodesUrl))} + ${basename(resolveDataUrl(edgesUrl))}`,
    });
  } catch (error) {
    showGraphEmpty(`通过 URL 载入失败：${error.message}`);
  }
}

async function fetchJson(url) {
  const resolvedUrl = resolveDataUrl(url);
  const response = await fetch(resolvedUrl);
  if (!response.ok) {
    throw new Error(`${resolvedUrl} -> ${response.status}`);
  }
  return response.json();
}

async function handleFolderSelection(event) {
  const files = Array.from(event.target.files || []);
  if (!files.length) {
    return;
  }
  try {
    const dataset = await readDatasetFiles(files);
    ingestDataset({
      nodes: dataset.nodes,
      edges: dataset.edges,
      requirements: dataset.requirements,
      sourceLabel: guessFolderLabel(dataset.nodesFile.webkitRelativePath || dataset.nodesFile.name),
    });
  } catch (error) {
    showGraphEmpty(error.message);
  } finally {
    event.target.value = '';
  }
}

async function handleManualFilesSelection(event) {
  const files = Array.from(event.target.files || []);
  if (!files.length) {
    return;
  }
  try {
    const dataset = await readDatasetFiles(files);
    ingestDataset({
      nodes: dataset.nodes,
      edges: dataset.edges,
      requirements: dataset.requirements,
      sourceLabel: `${dataset.nodesFile.name} + ${dataset.edgesFile.name}`,
    });
    const advancedImport = refs.manualFilesInput.closest('details');
    if (advancedImport) {
      advancedImport.open = false;
    }
  } catch (error) {
    showGraphEmpty(error.message);
  } finally {
    event.target.value = '';
  }
}

async function readDatasetFiles(files) {
  const nodesFile = pickDatasetFile(files, 'graph_nodes.json');
  const edgesFile = pickDatasetFile(files, 'graph_edges.json');
  const requirementsFile = pickDatasetFile(files, 'requirements.json');
  if (!nodesFile || !edgesFile) {
    throw new Error('没有找到 graph_nodes.json 或 graph_edges.json，请选择图谱空间目录。');
  }
  const [nodes, edges, requirements] = await Promise.all([
    readJsonFile(nodesFile),
    readJsonFile(edgesFile),
    requirementsFile ? readJsonFile(requirementsFile) : Promise.resolve([]),
  ]);
  return { nodes, edges, requirements, nodesFile, edgesFile, requirementsFile };
}

function readJsonFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        resolve(JSON.parse(reader.result));
      } catch (error) {
        reject(new Error(`${file.name} 不是合法 JSON`));
      }
    };
    reader.onerror = () => reject(new Error(`读取 ${file.name} 失败`));
    reader.readAsText(file, 'utf-8');
  });
}

function ingestDataset({ nodes, edges, requirements, sourceLabel }) {
  state.nodes = Array.isArray(nodes) ? nodes : [];
  state.edges = Array.isArray(edges) ? edges : [];
  state.requirements = Array.isArray(requirements) ? requirements : [];
  state.sourceLabel = sourceLabel;
  buildIndexes();
  resetFilters(false);
  state.selectedNodeId = pickDefaultNodeId();
  renderAll();
}

function buildIndexes() {
  state.nodesById = new Map(state.nodes.map((node) => [node.node_uid, node]));
  state.adjacency = new Map();
  state.edgesBySource = new Map();
  state.edgesByTarget = new Map();
  state.nodeDegree = new Map();
  state.requirementsByClause = new Map();
  state.requirementsByNode = new Map();

  state.nodes.forEach((node) => {
    state.adjacency.set(node.node_uid, []);
    state.nodeDegree.set(node.node_uid, 0);
  });

  state.edges.forEach((edge) => {
    pushMapArray(state.edgesBySource, edge.source_uid, edge);
    pushMapArray(state.edgesByTarget, edge.target_uid, edge);
    pushMapArray(state.adjacency, edge.source_uid, edge);
    pushMapArray(state.adjacency, edge.target_uid, edge);
    state.nodeDegree.set(edge.source_uid, (state.nodeDegree.get(edge.source_uid) || 0) + 1);
    state.nodeDegree.set(edge.target_uid, (state.nodeDegree.get(edge.target_uid) || 0) + 1);
  });

  state.requirements.forEach((item) => {
    if (item.parent_clause_uid) {
      pushMapArray(state.requirementsByClause, item.parent_clause_uid, item);
    }
    if (item.requirement_uid) {
      state.requirementsByNode.set(item.requirement_uid, item);
    }
  });
}

function renderAll() {
  refs.dataSource.textContent = state.sourceLabel;
  refs.depthIndicator.textContent = `${state.depth} 跳`;
  renderSummary();
  renderNodeTypeFilters();
  renderEdgeTypeFilters();
  renderNodeList();
  renderLegend();
  renderGraph();
  renderDetail();
}

function renderEmptyShell() {
  refs.statsGrid.innerHTML = '';
  refs.nodeList.innerHTML = `<div class="empty-note">请先加载图谱 JSON。</div>`;
  refs.listCount.textContent = '0';
  refs.legend.innerHTML = '';
  refs.detailProperties.innerHTML = '<div class="empty-note">加载后会展示结构化属性。</div>';
  refs.detailRequirements.innerHTML = '<div class="empty-note">选中 clause 或 requirement 后会出现关联 requirement。</div>';
  refs.detailNeighbors.innerHTML = '<div class="empty-note">选中节点后会出现一跳相邻节点。</div>';
  showGraphEmpty('选择图谱空间目录后即可开始浏览。');
}

function renderSummary() {
  const nodeTypeCount = countBy(state.nodes, (node) => node.node_type);
  const edgeTypeCount = countBy(state.edges, (edge) => edge.edge_type);
  const highestDegree = Math.max(...Array.from(state.nodeDegree.values(), (value) => value || 0), 0);
  const statItems = [
    ['节点总数', state.nodes.length, `类型数 ${nodeTypeCount.size}`],
    ['边总数', state.edges.length, `类型数 ${edgeTypeCount.size}`],
    ['Requirement', nodeTypeCount.get('requirement') || 0, `来自 ${state.requirements.length} 条明细`],
    ['Concept', nodeTypeCount.get('concept') || 0, `ABOUT 边 ${(edgeTypeCount.get('ABOUT') || 0).toLocaleString()}`],
    ['Clause', nodeTypeCount.get('clause') || 0, `章节/附录 ${(nodeTypeCount.get('chapter') || 0) + (nodeTypeCount.get('section') || 0) + (nodeTypeCount.get('appendix') || 0)}`],
    ['最高度数', highestDegree, '用于优先展示关键节点'],
  ];

  refs.statsGrid.innerHTML = '';
  const template = refs.statCardTemplate;
  statItems.forEach(([label, value, sub]) => {
    const fragment = template.content.cloneNode(true);
    fragment.querySelector('.stat-label').textContent = label;
    fragment.querySelector('.stat-value').textContent = Number(value).toLocaleString();
    fragment.querySelector('.stat-sub').textContent = sub;
    refs.statsGrid.appendChild(fragment);
  });
  refs.summaryPill.textContent = `${state.nodes.length.toLocaleString()} 节点 / ${state.edges.length.toLocaleString()} 边`;
}

function renderNodeTypeFilters() {
  const counts = Array.from(countBy(state.nodes, (node) => node.node_type).entries()).sort((a, b) => b[1] - a[1]);
  if (!state.activeNodeTypes.size) {
    counts.forEach(([type]) => state.activeNodeTypes.add(type));
  }
  refs.typeFilters.innerHTML = '';
  counts.forEach(([type, count]) => {
    const button = buildChip(`${type} · ${count}`, state.activeNodeTypes.has(type), () => {
      toggleSetValue(state.activeNodeTypes, type);
      renderNodeTypeFilters();
      renderNodeList();
      renderGraph();
    });
    refs.typeFilters.appendChild(button);
  });
}

function renderEdgeTypeFilters() {
  const counts = Array.from(countBy(state.edges, (edge) => edge.edge_type).entries()).sort((a, b) => b[1] - a[1]);
  if (!state.activeEdgeTypes.size) {
    counts.forEach(([type]) => state.activeEdgeTypes.add(type));
  }
  refs.edgeFilters.innerHTML = '';
  counts.forEach(([type, count]) => {
    const button = buildChip(`${type} · ${count}`, state.activeEdgeTypes.has(type), () => {
      toggleSetValue(state.activeEdgeTypes, type);
      renderEdgeTypeFilters();
      renderGraph();
      renderDetail();
    });
    refs.edgeFilters.appendChild(button);
  });
}

function renderNodeList() {
  const results = getFilteredNodes().slice(0, 160);
  refs.listCount.textContent = `${results.length.toLocaleString()}`;
  if (!results.length) {
    refs.nodeList.innerHTML = '<div class="empty-note">没有符合当前过滤条件的节点。</div>';
    return;
  }
  refs.nodeList.innerHTML = '';
  results.forEach((node) => {
    const card = document.createElement('button');
    card.type = 'button';
    card.className = `node-row${node.node_uid === state.selectedNodeId ? ' is-selected' : ''}`;
    card.innerHTML = `
      <div class="node-row-head">
        <div>
          <p class="node-row-title">${escapeHtml(node.label || node.node_uid)}</p>
          <div class="node-row-meta">${escapeHtml(node.node_type)} · 度数 ${state.nodeDegree.get(node.node_uid) || 0}</div>
        </div>
        <span class="pill pill-muted">${escapeHtml(node.node_type)}</span>
      </div>
      <div class="node-row-meta">${escapeHtml(truncate(node.text_content || node.node_uid, 96))}</div>
    `;
    card.addEventListener('click', () => selectNode(node.node_uid));
    refs.nodeList.appendChild(card);
  });
}

function renderLegend() {
  const types = Array.from(countBy(state.nodes, (node) => node.node_type).keys()).sort();
  refs.legend.innerHTML = '';
  types.forEach((type) => {
    const item = document.createElement('div');
    item.className = 'legend-item';
    item.innerHTML = `<span class="legend-swatch" style="background:${colorForType(type)}"></span>${escapeHtml(type)}`;
    refs.legend.appendChild(item);
  });
}

function renderGraph() {
  if (!state.nodes.length || !state.edges.length) {
    showGraphEmpty('还没有可渲染的数据。');
    return;
  }
  const selectedId = state.selectedNodeId || pickDefaultNodeId();
  if (!selectedId || !state.nodesById.has(selectedId)) {
    showGraphEmpty('当前选择的节点不存在。');
    return;
  }
  const neighborhood = buildNeighborhood(selectedId, state.depth, state.neighborLimit);
  const visibleNodes = neighborhood.nodes;
  const visibleEdges = neighborhood.edges;
  refs.graphEmpty.style.display = 'none';
  refs.graphTitle.textContent = `${state.nodesById.get(selectedId).label || selectedId} · 局部网络`;

  const width = 1200;
  const height = 760;
  const center = { x: width / 2, y: height / 2 };
  const positions = computePositions(visibleNodes, neighborhood.levels, center);

  const edgeMarkup = visibleEdges.map((edge) => {
    const source = positions.get(edge.source_uid);
    const target = positions.get(edge.target_uid);
    if (!source || !target) {
      return '';
    }
    const midX = (source.x + target.x) / 2;
    const midY = (source.y + target.y) / 2;
    const dx = target.x - source.x;
    const curve = Math.min(46, Math.max(-46, (edge.edge_type === 'NEXT' ? 28 : 18) * (dx >= 0 ? 1 : -1)));
    const path = `M ${source.x} ${source.y} Q ${midX + curve} ${midY - curve} ${target.x} ${target.y}`;
    const faded = edge.source_uid !== selectedId && edge.target_uid !== selectedId && !neighborhood.focusSet.has(edge.source_uid) && !neighborhood.focusSet.has(edge.target_uid);
    return `
      <path class="graph-edge${faded ? ' is-faded' : ''}" data-edge-type="${escapeHtml(edge.edge_type)}"
        d="${path}" stroke="${EDGE_COLORS[edge.edge_type] || EDGE_COLORS.default}">
        <title>${escapeHtml(edge.edge_type)}\n${escapeHtml(edge.source_uid)} → ${escapeHtml(edge.target_uid)}</title>
      </path>`;
  }).join('');

  const nodeMarkup = visibleNodes.map((node) => {
    const pos = positions.get(node.node_uid);
    if (!pos) {
      return '';
    }
    const selected = node.node_uid === selectedId;
    const faded = !selected && !neighborhood.focusSet.has(node.node_uid) && (neighborhood.levels.get(node.node_uid) || 0) > 1;
    const radius = nodeRadius(node);
    const label = truncate(node.label || node.node_uid, selected ? 52 : 24);
    return `
      <g class="graph-node${selected ? ' is-selected' : ''}${faded ? ' is-faded' : ''}" data-node-id="${escapeHtml(node.node_uid)}" transform="translate(${pos.x}, ${pos.y})">
        <circle class="graph-node-circle" r="${radius}" fill="${colorForType(node.node_type)}"></circle>
        <text text-anchor="middle" y="${radius + 18}">${escapeHtml(label)}</text>
        <title>${escapeHtml(node.node_uid)}\n${escapeHtml(node.node_type)}\n${escapeHtml(node.label || node.node_uid)}</title>
      </g>`;
  }).join('');

  refs.graphStage.innerHTML = `${edgeMarkup}${nodeMarkup}`;
  refs.graphStage.querySelectorAll('.graph-node').forEach((element) => {
    element.addEventListener('click', () => {
      const nodeId = element.getAttribute('data-node-id');
      if (nodeId) {
        selectNode(nodeId);
      }
    });
  });
}

function buildNeighborhood(selectedId, depth, limit) {
  const levels = new Map([[selectedId, 0]]);
  const queue = [selectedId];
  const visibleIds = new Set([selectedId]);
  const focusSet = new Set([selectedId]);

  while (queue.length) {
    const currentId = queue.shift();
    const currentLevel = levels.get(currentId) || 0;
    if (currentLevel >= depth) {
      continue;
    }
    const candidateEdges = (state.adjacency.get(currentId) || []).filter((edge) => state.activeEdgeTypes.has(edge.edge_type));
    const candidateNodes = candidateEdges
      .map((edge) => edge.source_uid === currentId ? edge.target_uid : edge.source_uid)
      .filter((nodeId) => state.nodesById.has(nodeId))
      .filter((nodeId) => nodeId === selectedId || state.activeNodeTypes.has(state.nodesById.get(nodeId).node_type));

    const ranked = uniq(candidateNodes)
      .map((nodeId) => state.nodesById.get(nodeId))
      .sort((a, b) => {
        const degreeDiff = (state.nodeDegree.get(b.node_uid) || 0) - (state.nodeDegree.get(a.node_uid) || 0);
        if (degreeDiff !== 0) {
          return degreeDiff;
        }
        return (a.label || a.node_uid).localeCompare(b.label || b.node_uid, 'zh-CN');
      })
      .slice(0, limit)
      .map((node) => node.node_uid);

    ranked.forEach((nodeId) => {
      if (visibleIds.has(nodeId)) {
        return;
      }
      visibleIds.add(nodeId);
      levels.set(nodeId, currentLevel + 1);
      queue.push(nodeId);
    });
    ranked.slice(0, Math.min(10, ranked.length)).forEach((nodeId) => focusSet.add(nodeId));
  }

  const nodes = Array.from(visibleIds).map((nodeId) => state.nodesById.get(nodeId)).filter(Boolean);
  const edges = state.edges.filter((edge) => state.activeEdgeTypes.has(edge.edge_type) && visibleIds.has(edge.source_uid) && visibleIds.has(edge.target_uid));
  return { nodes, edges, levels, focusSet };
}

function computePositions(nodes, levels, center) {
  const positions = new Map();
  const levelBuckets = new Map();
  nodes.forEach((node) => {
    const level = levels.get(node.node_uid) || 0;
    pushMapArray(levelBuckets, level, node);
  });
  positions.set(state.selectedNodeId, center);

  Array.from(levelBuckets.entries()).sort((a, b) => a[0] - b[0]).forEach(([level, bucket]) => {
    if (level === 0) {
      bucket.forEach((node) => positions.set(node.node_uid, center));
      return;
    }
    const baseRadius = level === 1 ? 220 : 360;
    const ringStep = 66;
    const perRing = level === 1 ? 18 : 24;
    const sortedBucket = bucket.sort((a, b) => (a.label || a.node_uid).localeCompare(b.label || b.node_uid, 'zh-CN'));
    sortedBucket.forEach((node, index) => {
      const ringIndex = Math.floor(index / perRing);
      const indexInRing = index % perRing;
      const ringCount = Math.min(perRing, sortedBucket.length - ringIndex * perRing);
      const angle = (-Math.PI / 2) + ((Math.PI * 2) / ringCount) * indexInRing + (ringIndex * 0.2);
      const radiusX = baseRadius + ringIndex * ringStep;
      const radiusY = (baseRadius * 0.72) + ringIndex * ringStep * 0.7;
      positions.set(node.node_uid, {
        x: center.x + Math.cos(angle) * radiusX,
        y: center.y + Math.sin(angle) * radiusY,
      });
    });
  });
  return positions;
}

function renderDetail() {
  const node = state.nodesById.get(state.selectedNodeId);
  if (!node) {
    refs.selectionStatus.textContent = '未选择';
    refs.detailType.textContent = '未选择';
    refs.detailTitle.textContent = '请先选择一个节点';
    refs.detailUid.textContent = '节点 UID 会显示在这里';
    refs.detailText.textContent = '暂无内容';
    refs.detailProperties.innerHTML = '<div class="empty-note">没有可显示的属性。</div>';
    refs.detailRequirements.innerHTML = '<div class="empty-note">暂无 requirement。</div>';
    refs.detailNeighbors.innerHTML = '<div class="empty-note">暂无相邻节点。</div>';
    return;
  }

  refs.selectionStatus.textContent = `${node.node_type} · ${node.label || node.node_uid}`;
  refs.detailType.textContent = node.node_type;
  refs.detailTitle.textContent = node.label || node.node_uid;
  refs.detailUid.textContent = node.node_uid;
  refs.detailText.textContent = node.text_content || node.properties?.source_text_normalized || '暂无文本';
  renderPropertyCards(node);
  renderRequirementCards(node);
  renderNeighborCards(node);
}

function renderPropertyCards(node) {
  const properties = {
    node_uid: node.node_uid,
    node_type: node.node_type,
    standard_uid: node.standard_uid,
    degree: state.nodeDegree.get(node.node_uid) || 0,
    ...normalizeNodeProperties(node),
  };
  const entries = Object.entries(properties).filter(([, value]) => value !== null && value !== undefined && value !== '');
  if (!entries.length) {
    refs.detailProperties.innerHTML = '<div class="empty-note">没有结构化属性。</div>';
    return;
  }
  refs.detailProperties.innerHTML = entries.map(([key, value]) => `
    <div class="property-card">
      <strong>${escapeHtml(key)}</strong>
      <div>${formatValue(value)}</div>
    </div>
  `).join('');
}

function renderRequirementCards(node) {
  let items = [];
  if (node.node_type === 'clause') {
    items = state.requirementsByClause.get(node.node_uid) || [];
  } else if (node.node_type === 'requirement') {
    const item = state.requirementsByNode.get(node.node_uid);
    items = item ? [item] : [];
  }
  if (!items.length) {
    refs.detailRequirements.innerHTML = '<div class="empty-note">当前节点没有额外的 requirement 详情。</div>';
    return;
  }
  refs.detailRequirements.innerHTML = items.slice(0, 12).map((item) => `
    <article class="detail-card">
      <strong>${escapeHtml(item.requirement_uid || item.requirement_text || 'requirement')}</strong>
      <div>${escapeHtml(item.requirement_text || '暂无 requirement_text')}</div>
      <div class="node-row-meta">${escapeHtml(item.modality || 'unknown')} · 置信度 ${formatConfidence(item.confidence)}</div>
      ${item.applicability_rule ? `<div class="node-row-meta">适用条件：${escapeHtml(item.applicability_rule)}</div>` : ''}
    </article>
  `).join('');
}

function renderNeighborCards(node) {
  const relatedEdges = getAdjacentEdges(node.node_uid);
  if (!relatedEdges.length) {
    refs.detailNeighbors.innerHTML = '<div class="empty-note">当前节点没有相邻节点。</div>';
    return;
  }
  const cards = relatedEdges.slice(0, 18).map((edge) => {
    const neighborId = edge.source_uid === node.node_uid ? edge.target_uid : edge.source_uid;
    const neighbor = state.nodesById.get(neighborId);
    if (!neighbor) {
      return '';
    }
    return `
      <article class="detail-card">
        <strong>${escapeHtml(edge.edge_type)}</strong>
        <div>${escapeHtml(neighbor.label || neighbor.node_uid)}</div>
        <div class="node-row-meta">${escapeHtml(neighbor.node_type)} · ${escapeHtml(neighbor.node_uid)}</div>
        <button class="ghost-button neighbor-jump" type="button" data-node-id="${escapeHtml(neighbor.node_uid)}">查看该节点</button>
      </article>
    `;
  }).join('');
  refs.detailNeighbors.innerHTML = cards;
  refs.detailNeighbors.querySelectorAll('.neighbor-jump').forEach((button) => {
    button.addEventListener('click', () => selectNode(button.getAttribute('data-node-id')));
  });
}

function getFilteredNodes() {
  return state.nodes
    .filter((node) => state.activeNodeTypes.has(node.node_type))
    .filter((node) => {
      if (!state.searchText) {
        return true;
      }
      const haystack = [node.node_uid, node.label, node.text_content].filter(Boolean).join(' ').toLowerCase();
      return haystack.includes(state.searchText);
    })
    .sort((a, b) => {
      const degreeDiff = (state.nodeDegree.get(b.node_uid) || 0) - (state.nodeDegree.get(a.node_uid) || 0);
      if (degreeDiff !== 0) {
        return degreeDiff;
      }
      return (a.label || a.node_uid).localeCompare(b.label || b.node_uid, 'zh-CN');
    });
}

function selectNode(nodeId) {
  if (!nodeId || !state.nodesById.has(nodeId)) {
    return;
  }
  state.selectedNodeId = nodeId;
  renderNodeList();
  renderGraph();
  renderDetail();
}

function resetFilters(shouldRender = true) {
  state.searchText = '';
  refs.searchInput.value = '';
  state.depth = Number(refs.depthSelect.value || 1);
  state.neighborLimit = Number(refs.neighborLimitSelect.value || 30);
  state.activeNodeTypes = new Set(state.nodes.map((node) => node.node_type));
  state.activeEdgeTypes = new Set(state.edges.map((edge) => edge.edge_type));
  if (shouldRender) {
    renderNodeTypeFilters();
    renderEdgeTypeFilters();
    renderNodeList();
    renderGraph();
    renderDetail();
  }
}

function focusStandardRoot() {
  const standardNode = state.nodes.find((node) => node.node_type === 'standard');
  if (standardNode) {
    selectNode(standardNode.node_uid);
  }
}

function pickDefaultNodeId() {
  const standardNode = state.nodes.find((node) => node.node_type === 'standard');
  if (standardNode) {
    return standardNode.node_uid;
  }
  const topDegreeNode = state.nodes.slice().sort((a, b) => (state.nodeDegree.get(b.node_uid) || 0) - (state.nodeDegree.get(a.node_uid) || 0))[0];
  return topDegreeNode?.node_uid || null;
}

function getAdjacentEdges(nodeId) {
  return (state.adjacency.get(nodeId) || []).filter((edge) => state.activeEdgeTypes.has(edge.edge_type)).sort((a, b) => a.edge_type.localeCompare(b.edge_type, 'en'));
}

function normalizeNodeProperties(node) {
  if (!node.properties || typeof node.properties !== 'object') {
    return {};
  }
  const properties = { ...node.properties };
  if (node.node_type === 'requirement') {
    const requirement = state.requirementsByNode.get(node.node_uid);
    if (requirement) {
      return { ...properties, ...requirement };
    }
  }
  if (node.node_type === 'clause') {
    const linkedRequirements = state.requirementsByClause.get(node.node_uid) || [];
    properties.linked_requirement_count = linkedRequirements.length;
  }
  return properties;
}

function buildChip(label, active, onClick) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = `filter-chip${active ? ' is-active' : ''}`;
  button.textContent = label;
  button.addEventListener('click', onClick);
  return button;
}

function colorForType(type) {
  return TYPE_COLORS[type] || TYPE_COLORS.default;
}

function nodeRadius(node) {
  const degree = state.nodeDegree.get(node.node_uid) || 0;
  const base = {
    standard: 24,
    chapter: 17,
    section: 15,
    appendix: 15,
    clause: 13,
    requirement: 11,
    concept: 10,
    reference_standard: 11,
  }[node.node_type] || 10;
  return Math.min(base + Math.log2(degree + 1) * 2.2, 30);
}

function showGraphEmpty(message) {
  refs.graphEmpty.style.display = 'grid';
  refs.graphEmpty.innerHTML = `<div><h3>图谱暂未渲染</h3><p>${escapeHtml(message)}</p></div>`;
  refs.graphStage.innerHTML = '';
}

function formatValue(value) {
  if (Array.isArray(value)) {
    if (!value.length) {
      return '<span class="node-row-meta">空数组</span>';
    }
    return value.map((item) => `<div>${formatValue(item)}</div>`).join('');
  }
  if (value && typeof value === 'object') {
    return `<pre class="detail-text">${escapeHtml(JSON.stringify(value, null, 2))}</pre>`;
  }
  return escapeHtml(String(value));
}

function formatConfidence(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(2) : 'n/a';
}

function truncate(text, maxLength) {
  if (!text || text.length <= maxLength) {
    return text || '';
  }
  return `${text.slice(0, maxLength - 1)}…`;
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function basename(path) {
  return path.replace(/\\/g, '/').split('/').filter(Boolean).pop() || path;
}

function guessFolderLabel(relativePath) {
  const normalizedPath = relativePath.replace(/\\/g, '/');
  const segments = normalizedPath.split('/').filter(Boolean);
  const derivedIndex = segments.indexOf('derived');
  if (derivedIndex > 0) {
    return segments[derivedIndex - 1];
  }
  if (segments.length >= 2) {
    return segments[segments.length - 2];
  }
  return normalizedPath;
}

function resolveDataUrl(path) {
  if (!path) {
    return path;
  }
  if (/^[a-z]+:\/\//i.test(path) || path.startsWith('//') || path.startsWith('file://')) {
    return path;
  }
  const normalized = path.replace(/\\/g, '/');
  return normalized.startsWith('/') ? normalized : `/${normalized.replace(/^\/+/, '')}`;
}

function pickDatasetFile(files, filename) {
  const matches = files.filter((file) => file.name === filename);
  if (!matches.length) {
    return null;
  }
  return matches
    .slice()
    .sort((left, right) => scoreDatasetFile(right, filename) - scoreDatasetFile(left, filename))[0];
}

function scoreDatasetFile(file, filename) {
  const path = (file.webkitRelativePath || file.name).replace(/\\/g, '/').toLowerCase();
  if (path === filename || path.endsWith(`/${filename}`)) {
    return 1;
  }
  return 0;
}

function toCamel(value) {
  return value.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
}

function uniq(values) {
  return Array.from(new Set(values));
}

function pushMapArray(map, key, value) {
  if (!map.has(key)) {
    map.set(key, []);
  }
  map.get(key).push(value);
}

function toggleSetValue(set, value) {
  if (set.has(value)) {
    set.delete(value);
  } else {
    set.add(value);
  }
}

function countBy(items, getter) {
  const map = new Map();
  items.forEach((item) => {
    const key = getter(item);
    map.set(key, (map.get(key) || 0) + 1);
  });
  return map;
}
