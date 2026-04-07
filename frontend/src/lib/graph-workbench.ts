import { MultiDirectedGraph } from 'graphology'
import circlepack from 'graphology-layout/circlepack'
import circular from 'graphology-layout/circular'
import forceLayout from 'graphology-layout-force'
import forceAtlas2 from 'graphology-layout-forceatlas2'
import noverlap from 'graphology-layout-noverlap'
import randomLayout from 'graphology-layout/random'
import type Sigma from 'sigma'

import type { GraphLabelItem, GraphWorkbenchData, GraphWorkbenchEdge, GraphWorkbenchNode } from './api'

export type GraphLayoutMode = 'circular' | 'circlepack' | 'random' | 'noverlap' | 'force-directed' | 'force-atlas'

export interface RuntimeNodeAttributes {
  label: string
  color: string
  size: number
  x: number
  y: number
  type: 'circle'
  hidden: boolean
  forceLabel: boolean
  zIndex: number
  nodeId: string
  nodeType: string
  degree: number
  root: boolean
}

export interface RuntimeEdgeAttributes {
  label: string | null
  color: string
  size: number
  type: 'curve'
  hidden: boolean
  zIndex: number
  edgeId: string
  edgeType: string
  sourceNodeId: string
  targetNodeId: string
  curvature: number
}

export type RuntimeGraph = MultiDirectedGraph<RuntimeNodeAttributes, RuntimeEdgeAttributes>

export interface NodePalette {
  color: string
  soft: string
  text: string
}

const DEFAULT_NODE_PALETTE: NodePalette = {
  color: '#708090',
  soft: 'rgba(112, 128, 144, 0.16)',
  text: '#314152',
}

const NODE_TYPE_PALETTE: Record<string, NodePalette> = {
  standard: { color: '#0f766e', soft: 'rgba(15, 118, 110, 0.18)', text: '#0f766e' },
  chapter: { color: '#2563eb', soft: 'rgba(37, 99, 235, 0.16)', text: '#1d4ed8' },
  section: { color: '#0891b2', soft: 'rgba(8, 145, 178, 0.16)', text: '#0e7490' },
  clause: { color: '#4f46e5', soft: 'rgba(79, 70, 229, 0.16)', text: '#4338ca' },
  appendix: { color: '#7c3aed', soft: 'rgba(124, 58, 237, 0.16)', text: '#6d28d9' },
  requirement: { color: '#dc2626', soft: 'rgba(220, 38, 38, 0.16)', text: '#b91c1c' },
  concept: { color: '#ca8a04', soft: 'rgba(202, 138, 4, 0.16)', text: '#a16207' },
  reference_standard: { color: '#475569', soft: 'rgba(71, 85, 105, 0.16)', text: '#334155' },
}

const EDGE_TYPE_COLORS: Record<string, string> = {
  contains: 'rgba(71, 85, 105, 0.34)',
  derives_requirement: 'rgba(220, 38, 38, 0.38)',
  about: 'rgba(8, 145, 178, 0.28)',
  cites_standard: 'rgba(79, 70, 229, 0.28)',
  next: 'rgba(100, 116, 139, 0.24)',
}

const GRAPH_EDGE_COLOR = 'rgba(15, 23, 42, 0.44)'

export function resolveNodePalette(nodeType: string): NodePalette {
  const normalized = normalizeText(nodeType)
  for (const [key, palette] of Object.entries(NODE_TYPE_PALETTE)) {
    if (normalized === key || normalized.includes(key)) {
      return palette
    }
  }
  return DEFAULT_NODE_PALETTE
}

export function resolveEdgeColor(edgeType: string): string {
  const normalized = normalizeText(edgeType)
  for (const [key, color] of Object.entries(EDGE_TYPE_COLORS)) {
    if (normalized === key || normalized.includes(key)) {
      return color
    }
  }
  return 'rgba(100, 116, 139, 0.26)'
}

export function normalizeText(value: unknown): string {
  return String(value ?? '')
    .trim()
    .toLowerCase()
    .replace(/\s+/g, ' ')
}

export function getLayoutLabel(layout: GraphLayoutMode): string {
  switch (layout) {
    case 'circular':
      return 'Circular'
    case 'circlepack':
      return 'Circlepack'
    case 'random':
      return 'Random'
    case 'noverlap':
      return 'Noverlaps'
    case 'force-directed':
      return 'Force Directed'
    case 'force-atlas':
      return 'Force Atlas'
    default:
      return layout
  }
}

export function createRuntimeGraph(
  rawGraph: GraphWorkbenchData,
  options?: {
    currentGraph?: RuntimeGraph | null
    rootNodeId?: string | null
    anchorNodeId?: string | null
  },
): RuntimeGraph {
  const graph = new MultiDirectedGraph()
  const currentGraph = options?.currentGraph ?? null
  const rootNodeId = options?.rootNodeId ?? rawGraph.rootNodeId ?? null
  const anchorPosition = readGraphNodePosition(currentGraph, options?.anchorNodeId) ?? { x: 0, y: 0 }

  rawGraph.nodes.forEach((node) => {
    const seededPosition = readGraphNodePosition(currentGraph, node.id) ?? createSeededPosition(node.id, anchorPosition)
    const palette = resolveNodePalette(node.nodeType)
    graph.addNode(node.id, {
      label: node.label,
      color: palette.color,
      size: resolveNodeSize(node, node.id === rootNodeId),
      x: seededPosition.x,
      y: seededPosition.y,
      type: 'circle',
      hidden: false,
      forceLabel: node.id === rootNodeId,
      zIndex: node.id === rootNodeId ? 3 : 1,
      nodeId: node.id,
      nodeType: node.nodeType,
      degree: node.degree,
      root: node.id === rootNodeId,
    })
  })

  rawGraph.edges.forEach((edge) => {
    if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) {
      return
    }
    graph.addDirectedEdgeWithKey(edge.id, edge.source, edge.target, {
      label: edge.edgeType,
      color: GRAPH_EDGE_COLOR,
      size: resolveEdgeSize(edge),
      type: 'curve',
      hidden: false,
      zIndex: 0,
      edgeId: edge.id,
      edgeType: edge.edgeType,
      sourceNodeId: edge.source,
      targetNodeId: edge.target,
      curvature: 0.18,
    })
  })

  ensureGraphNodePositions(graph)
  return graph
}

export function mergeWorkbenchGraphs(current: GraphWorkbenchData | null, incoming: GraphWorkbenchData): GraphWorkbenchData {
  if (!current || current.standardId !== incoming.standardId) {
    return incoming
  }

  const nodeMap = new Map<string, GraphWorkbenchNode>()
  current.nodes.forEach((node) => nodeMap.set(node.id, node))
  incoming.nodes.forEach((node) => {
    const existing = nodeMap.get(node.id)
    nodeMap.set(node.id, existing ? { ...existing, ...node, properties: { ...existing.properties, ...node.properties } } : node)
  })

  const edgeMap = new Map<string, GraphWorkbenchEdge>()
  current.edges.forEach((edge) => edgeMap.set(edge.id, edge))
  incoming.edges.forEach((edge) => {
    const existing = edgeMap.get(edge.id)
    edgeMap.set(edge.id, existing ? { ...existing, ...edge, properties: { ...existing.properties, ...edge.properties } } : edge)
  })

  const orderedNodeIds = [...current.nodes.map((node) => node.id)]
  incoming.nodes.forEach((node) => {
    if (!orderedNodeIds.includes(node.id)) {
      orderedNodeIds.push(node.id)
    }
  })

  const orderedEdgeIds = [...current.edges.map((edge) => edge.id)]
  incoming.edges.forEach((edge) => {
    if (!orderedEdgeIds.includes(edge.id)) {
      orderedEdgeIds.push(edge.id)
    }
  })

  return {
    standardId: incoming.standardId,
    rootNodeId: incoming.rootNodeId ?? current.rootNodeId,
    maxDepth: incoming.maxDepth,
    maxNodes: incoming.maxNodes,
    isTruncated: current.isTruncated || incoming.isTruncated,
    nodes: orderedNodeIds.map((nodeId) => nodeMap.get(nodeId)!).filter(Boolean),
    edges: orderedEdgeIds.map((edgeId) => edgeMap.get(edgeId)!).filter(Boolean),
  }
}

export function pruneWorkbenchGraph(rawGraph: GraphWorkbenchData | null, nodeId: string): GraphWorkbenchData | null {
  if (!rawGraph || !rawGraph.nodes.some((node) => node.id === nodeId)) {
    return rawGraph
  }

  const adjacency = buildAdjacency(rawGraph)
  const removable = new Set<string>([nodeId])
  const queue = [nodeId]
  const rootNodeId = rawGraph.rootNodeId ?? null

  while (queue.length > 0) {
    const currentId = queue.shift()!
    const neighbors = adjacency.get(currentId) ?? []
    neighbors.forEach((neighborId) => {
      if (neighborId === rootNodeId || removable.has(neighborId)) {
        return
      }
      const activeConnections = (adjacency.get(neighborId) ?? []).filter((candidateId) => !removable.has(candidateId))
      if (activeConnections.length <= 1) {
        removable.add(neighborId)
        queue.push(neighborId)
      }
    })
  }

  if (removable.size >= rawGraph.nodes.length) {
    return rawGraph
  }

  return {
    ...rawGraph,
    nodes: rawGraph.nodes.filter((node) => !removable.has(node.id)),
    edges: rawGraph.edges.filter((edge) => !removable.has(edge.source) && !removable.has(edge.target)),
  }
}

export function searchCurrentGraph(rawGraph: GraphWorkbenchData | null, query: string): GraphLabelItem[] {
  if (!rawGraph) {
    return []
  }

  const needle = normalizeText(query)
  if (!needle) {
    return []
  }

  const items = rawGraph.nodes
    .filter((node) => {
      const propertiesText = JSON.stringify(node.properties ?? {})
      return [node.label, node.id, node.nodeType, propertiesText].some((value) => normalizeText(value).includes(needle))
    })
    .map<GraphLabelItem>((node) => ({
      standardId: rawGraph.standardId,
      nodeId: node.id,
      label: node.label,
      nodeType: node.nodeType,
      degree: node.degree,
      excerpt: typeof node.properties.text_content === 'string' ? String(node.properties.text_content) : null,
    }))

  items.sort((left, right) => right.degree - left.degree || left.label.localeCompare(right.label, 'zh-CN'))
  return items.slice(0, 20)
}

export function collectNodePositions(graph: RuntimeGraph): Record<string, { x: number; y: number }> {
  const positions: Record<string, { x: number; y: number }> = {}
  graph.forEachNode((nodeId, attributes) => {
    positions[nodeId] = sanitizePosition(attributes, createSeededPosition(nodeId, { x: 0, y: 0 }))
  })
  return positions
}

export function layoutGraph(graph: RuntimeGraph, layout: GraphLayoutMode): Record<string, { x: number; y: number }> {
  const clone = graph.copy() as RuntimeGraph
  ensureGraphNodePositions(clone)

  if (layout === 'circular') {
    circular.assign(clone, { scale: 120 })
  } else if (layout === 'circlepack') {
    circlepack.assign(clone, { hierarchyAttributes: ['nodeType'], scale: 220 })
  } else if (layout === 'random') {
    randomLayout.assign(clone, { scale: 180 })
  } else if (layout === 'noverlap') {
    randomLayout.assign(clone, { scale: 160 })
    noverlap.assign(clone, { maxIterations: 220, settings: { margin: 8, ratio: 1.1 } })
  } else if (layout === 'force-directed') {
    forceLayout.assign(clone, {
      maxIterations: 220,
      settings: {
        attraction: 0.0006,
        repulsion: 0.08,
        gravity: 0.0008,
        inertia: 0.6,
        maxMove: 18,
      },
    })
  } else {
    forceAtlas2.assign(clone, {
      iterations: 180,
      settings: {
        ...forceAtlas2.inferSettings(clone),
        gravity: 0.8,
        scalingRatio: 18,
        slowDown: 2,
      },
    })
  }

  normalizeGraphPositions(clone)
  return collectNodePositions(clone)
}

export function animateLayoutTransition(
  graph: RuntimeGraph,
  targetPositions: Record<string, { x: number; y: number }>,
  onFrame: () => void,
  duration = 650,
) {
  const startPositions = collectNodePositions(graph)
  const startTime = performance.now()

  return new Promise<void>((resolve) => {
    const tick = (now: number) => {
      const progress = Math.min((now - startTime) / duration, 1)
      const eased = 1 - Math.pow(1 - progress, 3)

      Object.entries(targetPositions).forEach(([nodeId, target]) => {
        const start = startPositions[nodeId] ?? target
        graph.mergeNodeAttributes(nodeId, {
          x: start.x + (target.x - start.x) * eased,
          y: start.y + (target.y - start.y) * eased,
        })
      })

      onFrame()
      if (progress < 1) {
        requestAnimationFrame(tick)
        return
      }
      resolve()
    }

    requestAnimationFrame(tick)
  })
}

export function centerNodeInView(renderer: Sigma, graph: RuntimeGraph, nodeId: string) {
  if (!graph.hasNode(nodeId)) {
    return
  }
  const camera = renderer.getCamera()
  const position = sanitizePosition(
    {
      x: graph.getNodeAttribute(nodeId, 'x'),
      y: graph.getNodeAttribute(nodeId, 'y'),
    },
    createSeededPosition(nodeId, { x: 0, y: 0 }),
  )
  void camera.animate(position, { duration: 380 })
}

function buildAdjacency(rawGraph: GraphWorkbenchData) {
  const adjacency = new Map<string, Set<string>>()
  rawGraph.nodes.forEach((node) => adjacency.set(node.id, new Set<string>()))
  rawGraph.edges.forEach((edge) => {
    adjacency.get(edge.source)?.add(edge.target)
    adjacency.get(edge.target)?.add(edge.source)
  })
  return adjacency
}

function resolveNodeSize(node: GraphWorkbenchNode, isRoot: boolean) {
  if (isRoot) {
    return 20
  }
  const base = node.nodeType.includes('requirement') ? 12 : node.nodeType.includes('concept') ? 11 : 10
  return Math.max(7, Math.min(16, base + Math.min(node.degree, 8) * 0.55))
}

function resolveEdgeSize(edge: GraphWorkbenchEdge) {
  const weight = Number(edge.properties.weight ?? edge.properties.score ?? 1)
  if (Number.isFinite(weight) && weight > 0) {
    return Math.max(0.8, Math.min(3.4, 0.9 + weight * 0.35))
  }
  if (normalizeText(edge.edgeType) === 'contains') {
    return 0.95
  }
  return 1.2
}

function createSeededPosition(nodeId: string, anchor: { x: number; y: number }) {
  const seed = hashString(nodeId)
  const angle = ((seed % 360) / 180) * Math.PI
  const radius = 16 + (seed % 50)
  return {
    x: anchor.x + Math.cos(angle) * radius,
    y: anchor.y + Math.sin(angle) * radius,
  }
}

function normalizeGraphPositions(graph: RuntimeGraph, span = 120) {
  const positions = collectNodePositions(graph)
  const values = Object.values(positions)
  if (values.length === 0) {
    return
  }

  const minX = Math.min(...values.map((position) => position.x))
  const maxX = Math.max(...values.map((position) => position.x))
  const minY = Math.min(...values.map((position) => position.y))
  const maxY = Math.max(...values.map((position) => position.y))
  const centerX = (minX + maxX) / 2
  const centerY = (minY + maxY) / 2
  const scale = Math.max(maxX - minX, maxY - minY, 1)

  graph.forEachNode((nodeId) => {
    const position = positions[nodeId] ?? createSeededPosition(nodeId, { x: 0, y: 0 })
    graph.mergeNodeAttributes(nodeId, {
      x: ((position.x - centerX) / scale) * span,
      y: ((position.y - centerY) / scale) * span,
    })
  })
}

function readGraphNodePosition(graph: RuntimeGraph | null | undefined, nodeId: string | null | undefined) {
  if (!graph || !nodeId || !graph.hasNode(nodeId)) {
    return null
  }

  return sanitizePosition(
    {
      x: graph.getNodeAttribute(nodeId, 'x'),
      y: graph.getNodeAttribute(nodeId, 'y'),
    },
    null,
  )
}

function ensureGraphNodePositions(graph: RuntimeGraph) {
  graph.forEachNode((nodeId, attributes) => {
    const position = sanitizePosition(attributes, createSeededPosition(nodeId, { x: 0, y: 0 }))
    if (position.x !== attributes.x || position.y !== attributes.y) {
      graph.mergeNodeAttributes(nodeId, position)
    }
  })
}

function sanitizePosition(
  position: { x?: unknown; y?: unknown },
  fallback: { x: number; y: number } | null,
): { x: number; y: number } | null {
  if (isFiniteNumber(position.x) && isFiniteNumber(position.y)) {
    return { x: position.x, y: position.y }
  }
  return fallback
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function hashString(value: string) {
  let hash = 0
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash << 5) - hash + value.charCodeAt(index)
    hash |= 0
  }
  return Math.abs(hash)
}
