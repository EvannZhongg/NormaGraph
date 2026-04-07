import { MarkerType, Position, type Edge, type Node } from '@xyflow/react'

import type { GraphEdgeData, GraphNodeData } from './api'

export type GraphLayoutMode = 'radial' | 'hierarchy'

interface PositionedNodeMeta {
  position: { x: number; y: number }
  level: number
  order: number
  degree: number
}

interface GraphTree {
  children: Map<string, string[]>
  degree: Map<string, number>
  levels: Map<string, number>
  order: Map<string, number>
  rootId: string | null
}

interface GraphNeighbor {
  otherNodeId: string
  priority: number
}

export function buildFlowGraph(
  rawNodes: GraphNodeData[],
  rawEdges: GraphEdgeData[],
  focusNodeId: string | null,
  selectedNodeId: string | null,
  selectedEdgeId: string | null,
  layout: GraphLayoutMode,
): { nodes: Node[]; edges: Edge[] } {
  const rootId = focusNodeId ?? rawNodes.find((node) => node.node_type === 'standard')?.node_uid ?? rawNodes[0]?.node_uid ?? null
  const graphTree = buildGraphTree(rawNodes, rawEdges, rootId)
  const positionedNodes = layout === 'hierarchy' ? buildHierarchyLayout(rawNodes, graphTree) : buildRadialLayout(rawNodes, graphTree)
  const positions = new Map([...positionedNodes.entries()].map(([nodeId, meta]) => [nodeId, meta.position]))
  const graphIsCompact = rawNodes.length <= 18
  const graphIsMedium = rawNodes.length <= 40

  return {
    nodes: rawNodes.map((node) => {
      const tone = resolveNodeTone(node.node_type)
      const meta = positionedNodes.get(node.node_uid)
      const level = meta?.level ?? graphTree.levels.get(node.node_uid) ?? 0
      const degree = meta?.degree ?? graphTree.degree.get(node.node_uid) ?? 0
      const isRoot = node.node_uid === rootId
      const isSelected = node.node_uid === selectedNodeId
      const nodeSize = resolveNodeSize({ level, degree, isRoot })
      const showLabel = graphIsCompact || isRoot || isSelected || (graphIsMedium && level <= 1)

      return {
        id: node.node_uid,
        position: meta?.position ?? { x: 0, y: 0 },
        data: {
          ...node,
          label: buildNodeLabel(node),
          typeLabel: buildTypeLabel(node.node_type),
          accent: tone.accent,
          accentSoft: tone.soft,
          isRoot,
          showLabel,
          labelPinned: isRoot || isSelected,
          nodeSize,
        },
        type: 'entity',
        draggable: false,
        focusable: true,
        sourcePosition: fallbackNodePosition(meta?.position, true),
        targetPosition: fallbackNodePosition(meta?.position, false),
        style: {
          background: 'transparent',
          border: 'none',
          padding: 0,
          width: nodeSize,
          height: nodeSize,
          boxShadow: 'none',
        },
        zIndex: isSelected ? 24 : isRoot ? 18 : 10,
      }
    }),
    edges: rawEdges.map((edge) => {
      const sourcePosition = positions.get(edge.source_uid)
      const targetPosition = positions.get(edge.target_uid)
      const isSelected = edge.edge_uid === selectedEdgeId
      const isContainmentEdge = edge.edge_type.toUpperCase() === 'CONTAINS'
      const stroke = isSelected
        ? 'rgba(13, 148, 136, 0.92)'
        : isContainmentEdge
          ? 'rgba(148, 163, 184, 0.48)'
          : 'rgba(71, 85, 105, 0.54)'

      return {
        id: edge.edge_uid,
        source: edge.source_uid,
        target: edge.target_uid,
        sourceHandle: resolveHandleId(sourcePosition, targetPosition, 'source'),
        targetHandle: resolveHandleId(targetPosition, sourcePosition, 'target'),
        label: isSelected ? edge.edge_type : undefined,
        type: layout === 'hierarchy' && !isContainmentEdge ? 'simplebezier' : 'straight',
        animated: false,
        style: {
          stroke,
          strokeWidth: isSelected ? 2.8 : isContainmentEdge ? 1.25 : 1.65,
        },
        labelStyle: { fill: 'var(--text-primary)', fontSize: 11, fontWeight: 600 },
        labelBgStyle: { fill: 'rgba(255,255,255,0.92)', fillOpacity: 1, stroke: 'rgba(255,255,255,0.4)' },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          width: isSelected ? 14 : 12,
          height: isSelected ? 14 : 12,
          color: stroke,
        },
        data: edge,
      }
    }),
  }
}

function buildGraphTree(rawNodes: GraphNodeData[], rawEdges: GraphEdgeData[], rootId: string | null): GraphTree {
  const nodeMap = new Map(rawNodes.map((node) => [node.node_uid, node]))
  const adjacency = new Map<string, GraphNeighbor[]>()
  const children = new Map<string, string[]>()
  const degree = new Map<string, number>()
  const levels = new Map<string, number>()
  const order = new Map<string, number>()

  rawNodes.forEach((node) => {
    adjacency.set(node.node_uid, [])
    children.set(node.node_uid, [])
    degree.set(node.node_uid, 0)
  })

  rawEdges.forEach((edge) => {
    const sourceType = nodeMap.get(edge.source_uid)?.node_type ?? ''
    const targetType = nodeMap.get(edge.target_uid)?.node_type ?? ''
    adjacency.get(edge.source_uid)?.push({
      otherNodeId: edge.target_uid,
      priority: resolveNeighborPriority(edge.edge_type, true, targetType),
    })
    adjacency.get(edge.target_uid)?.push({
      otherNodeId: edge.source_uid,
      priority: resolveNeighborPriority(edge.edge_type, false, sourceType),
    })
    degree.set(edge.source_uid, (degree.get(edge.source_uid) ?? 0) + 1)
    degree.set(edge.target_uid, (degree.get(edge.target_uid) ?? 0) + 1)
  })

  for (const [nodeId, neighbors] of adjacency.entries()) {
    neighbors.sort((left, right) => {
      if (left.priority !== right.priority) {
        return left.priority - right.priority
      }

      const leftLabel = nodeMap.get(left.otherNodeId)?.label ?? nodeMap.get(left.otherNodeId)?.text_content ?? left.otherNodeId
      const rightLabel = nodeMap.get(right.otherNodeId)?.label ?? nodeMap.get(right.otherNodeId)?.text_content ?? right.otherNodeId
      return leftLabel.localeCompare(rightLabel, 'zh-CN')
    })
    adjacency.set(nodeId, neighbors)
  }

  if (!rootId || !nodeMap.has(rootId)) {
    rawNodes.forEach((node, index) => {
      levels.set(node.node_uid, 0)
      order.set(node.node_uid, index)
    })

    return { children, degree, levels, order, rootId: rawNodes[0]?.node_uid ?? null }
  }

  const queue = [rootId]
  const visited = new Set<string>([rootId])
  levels.set(rootId, 0)

  while (queue.length) {
    const current = queue.shift() as string
    const level = levels.get(current) ?? 0

    for (const neighbor of adjacency.get(current) ?? []) {
      if (visited.has(neighbor.otherNodeId)) {
        continue
      }

      visited.add(neighbor.otherNodeId)
      levels.set(neighbor.otherNodeId, level + 1)
      children.get(current)?.push(neighbor.otherNodeId)
      queue.push(neighbor.otherNodeId)
    }
  }

  const disconnected = rawNodes
    .map((node) => node.node_uid)
    .filter((nodeId) => !visited.has(nodeId))
    .sort((left, right) => {
      const leftLabel = nodeMap.get(left)?.label ?? nodeMap.get(left)?.text_content ?? left
      const rightLabel = nodeMap.get(right)?.label ?? nodeMap.get(right)?.text_content ?? right
      return leftLabel.localeCompare(rightLabel, 'zh-CN')
    })

  const disconnectedLevelBase = Math.max(...levels.values(), 0) + 1
  disconnected.forEach((nodeId, index) => {
    levels.set(nodeId, disconnectedLevelBase + index)
  })

  let cursor = 0
  const assignOrder = (nodeId: string) => {
    order.set(nodeId, cursor)
    cursor += 1
    for (const childId of children.get(nodeId) ?? []) {
      assignOrder(childId)
    }
  }

  assignOrder(rootId)
  disconnected.forEach((nodeId) => {
    order.set(nodeId, cursor)
    cursor += 1
  })

  return { children, degree, levels, order, rootId }
}

function buildRadialLayout(rawNodes: GraphNodeData[], graphTree: GraphTree) {
  const positions = new Map<string, PositionedNodeMeta>()
  const { children, degree, levels, order, rootId } = graphTree

  if (rootId) {
    const subtreeWeights = computeSubtreeWeights(children, rootId)
    const maxLevel = Math.max(...levels.values(), 0)

    const placeNode = (nodeId: string, startAngle: number, endAngle: number) => {
      const level = levels.get(nodeId) ?? 0
      const angle = level === 0 ? -Math.PI / 2 : (startAngle + endAngle) / 2
      const radius = level === 0 ? 0 : 170 + (level - 1) * 124

      positions.set(nodeId, {
        position: {
          x: Math.cos(angle) * radius,
          y: Math.sin(angle) * radius * 0.82,
        },
        level,
        order: order.get(nodeId) ?? 0,
        degree: degree.get(nodeId) ?? 0,
      })

      const branch = children.get(nodeId) ?? []
      if (branch.length === 0) {
        return
      }

      const parentSpan = endAngle - startAngle
      const childSpread = level === 0 ? Math.PI * 2 : Math.min(parentSpan * 0.82, Math.PI * 1.18)
      const centerAngle = (startAngle + endAngle) / 2
      const gap = Math.min(0.09, childSpread * 0.045)
      const usableStart = centerAngle - childSpread / 2 + gap
      const usableEnd = centerAngle + childSpread / 2 - gap
      const usableSpan = Math.max(usableEnd - usableStart, 0.08)
      const totalWeight = branch.reduce((sum, childId) => sum + (subtreeWeights.get(childId) ?? 1), 0)

      let cursorAngle = usableStart
      branch.forEach((childId, index) => {
        const childWeight = subtreeWeights.get(childId) ?? 1
        const slotSpan = (usableSpan * childWeight) / Math.max(totalWeight, 1)
        const branchStart = index === 0 ? usableStart : cursorAngle
        const branchEnd = index === branch.length - 1 ? usableEnd : cursorAngle + slotSpan

        placeNode(childId, branchStart, branchEnd)
        cursorAngle = branchEnd
      })
    }

    placeNode(rootId, -Math.PI, Math.PI)

    const disconnected = rawNodes.filter((node) => !positions.has(node.node_uid))
    if (disconnected.length > 0) {
      const orbitRadius = 220 + maxLevel * 132
      disconnected.forEach((node, index) => {
        const angle = -Math.PI / 2 + (Math.PI * 2 * index) / disconnected.length
        positions.set(node.node_uid, {
          position: {
            x: Math.cos(angle) * orbitRadius,
            y: Math.sin(angle) * orbitRadius * 0.8,
          },
          level: levels.get(node.node_uid) ?? maxLevel + 1,
          order: order.get(node.node_uid) ?? index,
          degree: degree.get(node.node_uid) ?? 0,
        })
      })
    }
  }

  return positions
}

function buildHierarchyLayout(rawNodes: GraphNodeData[], graphTree: GraphTree) {
  const positions = new Map<string, PositionedNodeMeta>()
  const { children, degree, levels, order, rootId } = graphTree

  if (!rootId) {
    return positions
  }

  const slotGap = 104
  const levelGap = 250
  let cursor = 0

  const placeNode = (nodeId: string) => {
    const branch = children.get(nodeId) ?? []
    const level = levels.get(nodeId) ?? 0

    if (branch.length === 0) {
      const y = cursor * slotGap
      cursor += 1
      positions.set(nodeId, {
        position: { x: level * levelGap, y },
        level,
        order: order.get(nodeId) ?? cursor,
        degree: degree.get(nodeId) ?? 0,
      })
      return y
    }

    const childYs = branch.map((childId) => placeNode(childId))
    const y = childYs.reduce((sum, value) => sum + value, 0) / childYs.length
    positions.set(nodeId, {
      position: { x: level * levelGap, y },
      level,
      order: order.get(nodeId) ?? cursor,
      degree: degree.get(nodeId) ?? 0,
    })
    return y
  }

  placeNode(rootId)

  const placedNodes = [...positions.values()]
  const minY = Math.min(...placedNodes.map((meta) => meta.position.y), 0)
  const maxY = Math.max(...placedNodes.map((meta) => meta.position.y), 0)
  const centerOffset = (minY + maxY) / 2

  for (const meta of positions.values()) {
    meta.position.y -= centerOffset
  }

  const disconnected = rawNodes.filter((node) => !positions.has(node.node_uid)).sort((left, right) => {
    const leftOrder = order.get(left.node_uid) ?? 0
    const rightOrder = order.get(right.node_uid) ?? 0
    return leftOrder - rightOrder
  })

  let disconnectedCursor = Math.ceil((maxY - minY) / slotGap / 2) + 1
  disconnected.forEach((node) => {
    const level = levels.get(node.node_uid) ?? 0
    positions.set(node.node_uid, {
      position: {
        x: level * levelGap,
        y: disconnectedCursor * slotGap,
      },
      level,
      order: order.get(node.node_uid) ?? disconnectedCursor,
      degree: degree.get(node.node_uid) ?? 0,
    })
    disconnectedCursor += 1
  })

  return positions
}

function computeSubtreeWeights(children: Map<string, string[]>, rootId: string) {
  const weights = new Map<string, number>()

  const visit = (nodeId: string) => {
    const branch = children.get(nodeId) ?? []
    if (branch.length === 0) {
      weights.set(nodeId, 1)
      return 1
    }

    const weight = branch.reduce((sum, childId) => sum + visit(childId), 0)
    weights.set(nodeId, Math.max(weight, 1))
    return weight
  }

  visit(rootId)
  return weights
}

function resolveNeighborPriority(edgeType: string, isOutbound: boolean, nodeType: string) {
  const normalizedEdgeType = edgeType.toUpperCase()
  const normalizedNodeType = nodeType.toLowerCase()

  if (normalizedEdgeType === 'CONTAINS') {
    return resolveNodeTypeRank(normalizedNodeType)
  }

  if (normalizedEdgeType === 'REFERENCES' || normalizedEdgeType === 'CITES') {
    return 16 + (isOutbound ? 0 : 2)
  }

  if (normalizedNodeType.includes('require')) {
    return 8 + (isOutbound ? 0 : 2)
  }

  return 12 + (isOutbound ? 0 : 2) + resolveNodeTypeRank(normalizedNodeType)
}

function resolveNodeTypeRank(nodeType: string) {
  if (nodeType.includes('chapter')) {
    return 1
  }

  if (nodeType.includes('section')) {
    return 2
  }

  if (nodeType.includes('clause')) {
    return 3
  }

  if (nodeType.includes('appendix')) {
    return 4
  }

  if (nodeType.includes('require')) {
    return 5
  }

  return 6
}

function fallbackNodePosition(position: { x: number; y: number } | undefined, isSource: boolean) {
  if (!position) {
    return isSource ? Position.Right : Position.Left
  }

  if (Math.abs(position.x) >= Math.abs(position.y)) {
    return position.x >= 0 ? Position.Right : Position.Left
  }

  return position.y >= 0 ? Position.Bottom : Position.Top
}

function resolveHandleId(
  fromPosition: { x: number; y: number } | undefined,
  toPosition: { x: number; y: number } | undefined,
  handleType: 'source' | 'target',
) {
  const direction = resolveCardinalDirection(fromPosition, toPosition)
  return `${handleType}-${direction}`
}

function resolveCardinalDirection(fromPosition: { x: number; y: number } | undefined, toPosition: { x: number; y: number } | undefined) {
  if (!fromPosition || !toPosition) {
    return 'right'
  }

  const dx = toPosition.x - fromPosition.x
  const dy = toPosition.y - fromPosition.y

  if (Math.abs(dx) >= Math.abs(dy)) {
    return dx >= 0 ? 'right' : 'left'
  }

  return dy >= 0 ? 'bottom' : 'top'
}

function buildNodeLabel(node: GraphNodeData) {
  const label = node.label?.trim() || node.text_content?.trim() || node.node_uid
  return label.length > 34 ? `${label.slice(0, 34)}...` : label
}

function buildTypeLabel(nodeType: string) {
  return nodeType.replace(/[_-]+/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase())
}

function resolveNodeSize({ level, degree, isRoot }: { level: number; degree: number; isRoot: boolean }) {
  if (isRoot) {
    return 48
  }

  const base = level <= 1 ? 30 : 24
  return Math.min(base + Math.min(degree, 4), 36)
}

function resolveNodeTone(nodeType: string) {
  const normalized = nodeType.toLowerCase()

  if (normalized.includes('standard')) {
    return {
      accent: '#1dbf92',
      soft: 'rgba(29, 191, 146, 0.2)',
    }
  }

  if (normalized.includes('require')) {
    return {
      accent: '#2563eb',
      soft: 'rgba(37, 99, 235, 0.18)',
    }
  }

  if (normalized.includes('chapter') || normalized.includes('section') || normalized.includes('clause') || normalized.includes('appendix')) {
    return {
      accent: '#f59e0b',
      soft: 'rgba(245, 158, 11, 0.18)',
    }
  }

  return {
    accent: '#64748b',
    soft: 'rgba(100, 116, 139, 0.18)',
  }
}
