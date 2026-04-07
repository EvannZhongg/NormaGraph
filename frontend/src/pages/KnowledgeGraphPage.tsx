import * as Dialog from '@radix-ui/react-dialog'
import EdgeCurveProgram from '@sigma/edge-curve'
import clsx from 'clsx'
import { ChevronLeft, ChevronRight, Crosshair, GitBranchPlus, Info, LayoutGrid, LoaderCircle, Maximize2, Minimize2, Network, Pencil, RefreshCw, RotateCw, Scissors, Search, Settings2, Workflow, ZoomIn, ZoomOut, X } from 'lucide-react'
import Sigma from 'sigma'
import { startTransition, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'

import {
  checkGraphEntityExists,
  editGraphEntity,
  editGraphRelation,
  fetchGraphServiceStatus,
  fetchPopularGraphLabels,
  listKgSpaces,
  loadWorkbenchGraph,
  searchGraphLabels,
  type GraphEntityEditResponse,
  type GraphLabelItem,
  type GraphRelationEditResponse,
  type GraphWorkbenchData,
  type GraphWorkbenchEdge,
  type GraphWorkbenchNode,
  type KgSpaceSummary,
} from '../lib/api'
import {
  animateLayoutTransition,
  centerNodeInView,
  createRuntimeGraph,
  getLayoutLabel,
  layoutGraph,
  mergeWorkbenchGraphs,
  normalizeText,
  pruneWorkbenchGraph,
  resolveEdgeColor,
  resolveNodePalette,
  searchCurrentGraph,
  type GraphLayoutMode,
  type RuntimeGraph,
} from '../lib/graph-workbench'
import { useAppStore } from '../store/app-store'
import { useGraphWorkbenchStore } from '../store/graph-workbench-store'

const LAYOUT_OPTIONS: GraphLayoutMode[] = ['circular', 'circlepack', 'random', 'noverlap', 'force-directed', 'force-atlas']
const MAX_DEPTH_OPTIONS = [1, 2, 3, 4]
const ALL_MAX_NODES_VALUE = 0
const MAX_NODE_OPTIONS = [120, 180, 220, 300, 420, 600, 800, 1200, 1600, 2200, 3000, ALL_MAX_NODES_VALUE]

interface InspectorRelationItem {
  edgeId: string
  edgeType: string
  direction: 'out' | 'in'
  otherNodeId: string
  otherNodeLabel: string
}

interface GraphCommitOptions {
  relayout?: boolean
  resetView?: boolean
  centerNodeId?: string | null
  selectNodeId?: string | null
  selectEdgeId?: string | null
  anchorNodeId?: string | null
}

interface LoadGraphOptions {
  standardId: string
  nodeId?: string | null
  label?: string | null
  replace?: boolean
  resetView?: boolean
  centerNodeId?: string | null
  activateStart?: boolean
  startLabel?: string
  selectNodeId?: string | null
}

type DockPanelMode = 'actions' | 'settings' | 'status'

export function KnowledgeGraphPage() {
  const selectedStandardId = useAppStore((state) => state.selectedStandardId)
  const setSelectedStandardId = useAppStore((state) => state.setSelectedStandardId)

  const layout = useGraphWorkbenchStore((state) => state.layout)
  const maxDepth = useGraphWorkbenchStore((state) => state.maxDepth)
  const maxNodes = useGraphWorkbenchStore((state) => state.maxNodes)
  const showLegend = useGraphWorkbenchStore((state) => state.showLegend)
  const showStatusPanel = useGraphWorkbenchStore((state) => state.showStatusPanel)
  const inspectorCollapsed = useGraphWorkbenchStore((state) => state.inspectorCollapsed)
  const showEdgeLabels = useGraphWorkbenchStore((state) => state.showEdgeLabels)
  const showNodeLabels = useGraphWorkbenchStore((state) => state.showNodeLabels)
  const muteNonSelectedEdges = useGraphWorkbenchStore((state) => state.muteNonSelectedEdges)
  const labelSizeThreshold = useGraphWorkbenchStore((state) => state.labelSizeThreshold)
  const rawGraph = useGraphWorkbenchStore((state) => state.rawGraph)
  const runtimeGraph = useGraphWorkbenchStore((state) => state.runtimeGraph)
  const status = useGraphWorkbenchStore((state) => state.status)
  const selectedNodeId = useGraphWorkbenchStore((state) => state.selectedNodeId)
  const selectedEdgeId = useGraphWorkbenchStore((state) => state.selectedEdgeId)
  const activeStartNodeId = useGraphWorkbenchStore((state) => state.activeStartNodeId)
  const activeStartLabel = useGraphWorkbenchStore((state) => state.activeStartLabel)
  const canvasSearchQuery = useGraphWorkbenchStore((state) => state.canvasSearchQuery)
  const labelSearchQuery = useGraphWorkbenchStore((state) => state.labelSearchQuery)
  const isLoadingGraph = useGraphWorkbenchStore((state) => state.isLoadingGraph)
  const isAnimatingLayout = useGraphWorkbenchStore((state) => state.isAnimatingLayout)

  const setLayout = useGraphWorkbenchStore((state) => state.setLayout)
  const setMaxDepth = useGraphWorkbenchStore((state) => state.setMaxDepth)
  const setMaxNodes = useGraphWorkbenchStore((state) => state.setMaxNodes)
  const setShowLegend = useGraphWorkbenchStore((state) => state.setShowLegend)
  const setShowStatusPanel = useGraphWorkbenchStore((state) => state.setShowStatusPanel)
  const setInspectorCollapsed = useGraphWorkbenchStore((state) => state.setInspectorCollapsed)
  const setShowEdgeLabels = useGraphWorkbenchStore((state) => state.setShowEdgeLabels)
  const setShowNodeLabels = useGraphWorkbenchStore((state) => state.setShowNodeLabels)
  const setMuteNonSelectedEdges = useGraphWorkbenchStore((state) => state.setMuteNonSelectedEdges)
  const setLabelSizeThreshold = useGraphWorkbenchStore((state) => state.setLabelSizeThreshold)
  const setGraphData = useGraphWorkbenchStore((state) => state.setGraphData)
  const setStatus = useGraphWorkbenchStore((state) => state.setStatus)
  const setLoadingGraph = useGraphWorkbenchStore((state) => state.setLoadingGraph)
  const setAnimatingLayout = useGraphWorkbenchStore((state) => state.setAnimatingLayout)
  const setActiveStart = useGraphWorkbenchStore((state) => state.setActiveStart)
  const selectNode = useGraphWorkbenchStore((state) => state.selectNode)
  const selectEdge = useGraphWorkbenchStore((state) => state.selectEdge)
  const clearSelection = useGraphWorkbenchStore((state) => state.clearSelection)
  const setCanvasSearchQuery = useGraphWorkbenchStore((state) => state.setCanvasSearchQuery)
  const setLabelSearchQuery = useGraphWorkbenchStore((state) => state.setLabelSearchQuery)
  const resetGraphState = useGraphWorkbenchStore((state) => state.resetGraphState)

  const [spaces, setSpaces] = useState<KgSpaceSummary[]>([])
  const [popularLabels, setPopularLabels] = useState<GraphLabelItem[]>([])
  const [globalLabelResults, setGlobalLabelResults] = useState<GraphLabelItem[]>([])
  const [startDropdownOpen, setStartDropdownOpen] = useState(false)
  const [canvasDropdownOpen, setCanvasDropdownOpen] = useState(false)
  const [nodeEditorOpen, setNodeEditorOpen] = useState(false)
  const [relationEditorOpen, setRelationEditorOpen] = useState(false)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [dockOpen, setDockOpen] = useState(false)
  const [dockPanelMode, setDockPanelMode] = useState<DockPanelMode>('actions')
  const [searchPanelOpen, setSearchPanelOpen] = useState(false)
  const [hiddenNodeTypes, setHiddenNodeTypes] = useState<string[]>([])

  const shellRef = useRef<HTMLDivElement | null>(null)
  const canvasRef = useRef<HTMLDivElement | null>(null)
  const sigmaRef = useRef<Sigma | null>(null)
  const runtimeGraphRef = useRef<RuntimeGraph | null>(runtimeGraph)
  const rawGraphRef = useRef<GraphWorkbenchData | null>(rawGraph)
  const layoutRef = useRef<GraphLayoutMode>(layout)
  const selectedStandardRef = useRef<string | null>(null)
  const draggingNodeRef = useRef<string | null>(null)
  const previousLayoutRef = useRef<GraphLayoutMode>(layout)
  const previousLimitsRef = useRef({ maxDepth, maxNodes })
  const legendBootstrappedRef = useRef(false)

  const deferredLabelSearch = useDeferredValue(labelSearchQuery)
  const deferredCanvasSearch = useDeferredValue(canvasSearchQuery)

  const selectedSpace = useMemo(
    () => spaces.find((item) => item.standardId === selectedStandardId) ?? null,
    [selectedStandardId, spaces],
  )

  const nodeMap = useMemo(() => new Map((rawGraph?.nodes ?? []).map((node) => [node.id, node])), [rawGraph])
  const edgeMap = useMemo(() => new Map((rawGraph?.edges ?? []).map((edge) => [edge.id, edge])), [rawGraph])
  const selectedNode = selectedNodeId ? nodeMap.get(selectedNodeId) ?? null : null
  const selectedEdge = selectedEdgeId ? edgeMap.get(selectedEdgeId) ?? null : null
  const hiddenNodeTypeSet = useMemo(() => new Set(hiddenNodeTypes), [hiddenNodeTypes])
  const hiddenNodeIds = useMemo(() => {
    const next = new Set<string>()
    if (!rawGraph) {
      return next
    }

    rawGraph.nodes.forEach((node) => {
      if (hiddenNodeTypeSet.has(normalizeText(node.nodeType))) {
        next.add(node.id)
      }
    })

    return next
  }, [hiddenNodeTypeSet, rawGraph])

  const canvasSearchResults = useMemo(
    () => searchCurrentGraph(rawGraph, deferredCanvasSearch).filter((item) => !hiddenNodeIds.has(item.nodeId)),
    [deferredCanvasSearch, hiddenNodeIds, rawGraph],
  )

  const relatedRelations = useMemo<InspectorRelationItem[]>(() => {
    if (!rawGraph || !selectedNodeId) {
      return []
    }

    return rawGraph.edges
      .filter((edge) => (edge.source === selectedNodeId || edge.target === selectedNodeId) && !hiddenNodeIds.has(edge.source) && !hiddenNodeIds.has(edge.target))
      .map((edge) => {
        const isOutgoing = edge.source === selectedNodeId
        const otherNodeId = isOutgoing ? edge.target : edge.source
        return {
          edgeId: edge.id,
          edgeType: edge.edgeType,
          direction: isOutgoing ? 'out' : 'in',
          otherNodeId,
          otherNodeLabel: nodeMap.get(otherNodeId)?.label ?? otherNodeId,
        }
      })
      .sort((left, right) => left.otherNodeLabel.localeCompare(right.otherNodeLabel, 'zh-CN'))
  }, [hiddenNodeIds, nodeMap, rawGraph, selectedNodeId])

  const selectionContext = useMemo(() => {
    const connectedNodeIds = new Set<string>()
    const connectedEdgeIds = new Set<string>()
    const searchMatches = new Set(canvasSearchResults.map((item) => item.nodeId))

    if (!rawGraph) {
      return { connectedNodeIds, connectedEdgeIds, searchMatches }
    }

    if (selectedNodeId) {
      connectedNodeIds.add(selectedNodeId)
      rawGraph.edges.forEach((edge) => {
        if (edge.source === selectedNodeId || edge.target === selectedNodeId) {
          connectedEdgeIds.add(edge.id)
          connectedNodeIds.add(edge.source)
          connectedNodeIds.add(edge.target)
        }
      })
    }

    if (selectedEdgeId) {
      const edge = rawGraph.edges.find((candidate) => candidate.id === selectedEdgeId)
      if (edge) {
        connectedEdgeIds.add(edge.id)
        connectedNodeIds.add(edge.source)
        connectedNodeIds.add(edge.target)
      }
    }

    return { connectedNodeIds, connectedEdgeIds, searchMatches }
  }, [canvasSearchResults, rawGraph, selectedEdgeId, selectedNodeId])

  const nodeLegend = useMemo(() => {
    if (selectedSpace) {
      return buildDistributionFromCounts(selectedSpace.nodeTypes ?? {}).map(([name, count]) => ({
        name,
        count,
        color: resolveNodePalette(name).color,
      }))
    }

    if (!rawGraph) {
      return [] as Array<{ name: string; count: number; color: string }>
    }

    return buildDistribution(rawGraph.nodes.map((node) => node.nodeType)).map(([name, count]) => ({
      name,
      count,
      color: resolveNodePalette(name).color,
    }))
  }, [rawGraph, selectedSpace])

  useEffect(() => {
    if (selectedNodeId && hiddenNodeIds.has(selectedNodeId)) {
      clearSelection()
      return
    }

    if (selectedEdgeId) {
      const hiddenEdge = edgeMap.get(selectedEdgeId)
      if (hiddenEdge && (hiddenNodeIds.has(hiddenEdge.source) || hiddenNodeIds.has(hiddenEdge.target))) {
        clearSelection()
      }
    }
  }, [clearSelection, edgeMap, hiddenNodeIds, selectedEdgeId, selectedNodeId])

  const hiddenNodeTypeMessage = hiddenNodeTypes.length === 0
    ? '默认全部显示，点击类型可隐藏该类节点。'
    : `当前已隐藏 ${hiddenNodeTypes.length} 类节点，点击灰色类型可恢复显示。`

  const edgeLegend = useMemo(() => {
    if (!rawGraph) {
      return [] as Array<{ name: string; count: number; color: string }>
    }
    return buildDistribution(rawGraph.edges.map((edge) => edge.edgeType)).map(([name, count]) => ({
      name,
      count,
      color: resolveEdgeColor(name),
    }))
  }, [rawGraph])

  const flattenedStatusConfig = useMemo(() => flattenConfig(status?.configuration ?? {}).slice(0, 10), [status?.configuration])
  const activeStartDisplay = activeStartLabel || nodeMap.get(activeStartNodeId ?? '')?.label || selectedSpace?.title || selectedStandardId || '-'
  const truncatedMessage = rawGraph?.isTruncated
    ? `当前子图已截断，最多展示 ${rawGraph.maxNodes} 个节点。可增大 Max Nodes 或切换起始节点。`
    : null

  useEffect(() => {
    runtimeGraphRef.current = runtimeGraph
  }, [runtimeGraph])

  useEffect(() => {
    rawGraphRef.current = rawGraph
  }, [rawGraph])

  useEffect(() => {
    if (!selectedNodeId && !selectedEdgeId) {
      setInspectorCollapsed(true)
    }
  }, [rawGraph, selectedEdgeId, selectedNodeId, setInspectorCollapsed])

  useEffect(() => {
    layoutRef.current = layout
  }, [layout])
  useEffect(() => {
    if (legendBootstrappedRef.current) {
      return
    }

    legendBootstrappedRef.current = true
    if (!useGraphWorkbenchStore.getState().showLegend) {
      setShowLegend(true)
    }
  }, [setShowLegend])

  useEffect(() => {
    void listKgSpaces()
      .then((items) => {
        setSpaces(items)
        if (!selectedStandardId && items[0]) {
          setSelectedStandardId(items[0].standardId)
        }
      })
      .catch((error) => toast.error(extractErrorMessage(error, '知识图谱空间加载失败')))
  }, [selectedStandardId, setSelectedStandardId])

  useEffect(() => {
    const shell = shellRef.current
    if (!shell) {
      return undefined
    }

    const handleFullscreenChange = () => {
      setIsFullscreen(document.fullscreenElement === shell)
    }

    document.addEventListener('fullscreenchange', handleFullscreenChange)
    handleFullscreenChange()
    return () => document.removeEventListener('fullscreenchange', handleFullscreenChange)
  }, [])

  useEffect(() => {
    return () => {
      sigmaRef.current?.kill()
      sigmaRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!selectedStandardId) {
      resetGraphState()
      rawGraphRef.current = null
      runtimeGraphRef.current = null
      sigmaRef.current?.kill()
      sigmaRef.current = null
      if (canvasRef.current) {
        canvasRef.current.innerHTML = ''
      }
      setStatus(null)
      setPopularLabels([])
      setGlobalLabelResults([])
      setHiddenNodeTypes([])
      selectedStandardRef.current = null
      return
    }

    if (selectedStandardRef.current !== selectedStandardId) {
      selectedStandardRef.current = selectedStandardId
      previousLimitsRef.current = { maxDepth, maxNodes }
      resetGraphState()
      rawGraphRef.current = null
      runtimeGraphRef.current = null
      sigmaRef.current?.kill()
      sigmaRef.current = null
      if (canvasRef.current) {
        canvasRef.current.innerHTML = ''
      }
      setCanvasSearchQuery('')
      setLabelSearchQuery('')
      setStartDropdownOpen(false)
      setCanvasDropdownOpen(false)
      setSearchPanelOpen(false)
      setHiddenNodeTypes([])
      void refreshWorkbenchMeta(selectedStandardId)
      void loadGraph({
        standardId: selectedStandardId,
        nodeId: null,
        replace: true,
        resetView: true,
        activateStart: true,
      })
    }
  }, [maxDepth, maxNodes, resetGraphState, selectedStandardId, setCanvasSearchQuery, setLabelSearchQuery, setStatus])

  useEffect(() => {
    if (!selectedStandardId || !activeStartNodeId) {
      return
    }

    if (
      previousLimitsRef.current.maxDepth === maxDepth &&
      previousLimitsRef.current.maxNodes === maxNodes
    ) {
      return
    }

    previousLimitsRef.current = { maxDepth, maxNodes }
    void loadGraph({
      standardId: selectedStandardId,
      nodeId: activeStartNodeId,
      replace: true,
      resetView: true,
      activateStart: true,
      startLabel: activeStartLabel,
    })
  }, [activeStartLabel, activeStartNodeId, maxDepth, maxNodes, selectedNodeId, selectedStandardId])

  useEffect(() => {
    if (!selectedStandardId) {
      return
    }

    let cancelled = false
    const query = deferredLabelSearch.trim()

    if (!query) {
      setGlobalLabelResults(popularLabels.slice(0, 14))
      return () => {
        cancelled = true
      }
    }

    searchGraphLabels(selectedStandardId, query, 14)
      .then((items) => {
        if (!cancelled) {
          setGlobalLabelResults(items)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setGlobalLabelResults([])
        }
      })

    return () => {
      cancelled = true
    }
  }, [deferredLabelSearch, popularLabels, selectedStandardId])

  useEffect(() => {
    if (!runtimeGraph || !canvasRef.current) {
      return
    }

    if (!sigmaRef.current) {
      const renderer = new Sigma(runtimeGraph, canvasRef.current, createSigmaSettings({
        labelSizeThreshold,
        showEdgeLabels,
        showNodeLabels,
        selectionContext,
        muteNonSelectedEdges,
        hiddenNodeIds,
      }))
      sigmaRef.current = renderer
      bindRendererEvents(renderer)
      return
    }

    sigmaRef.current.setGraph(runtimeGraph)
    sigmaRef.current.setSettings(
      createSigmaSettings({
        labelSizeThreshold,
        showEdgeLabels,
        showNodeLabels,
        selectionContext,
        muteNonSelectedEdges,
        hiddenNodeIds,
      }),
    )
    sigmaRef.current.refresh()
  }, [hiddenNodeIds, labelSizeThreshold, muteNonSelectedEdges, runtimeGraph, selectionContext, showEdgeLabels, showNodeLabels])

  useEffect(() => {
    const renderer = sigmaRef.current
    if (!renderer) {
      return
    }

    renderer.setSettings(
      createSigmaSettings({
        labelSizeThreshold,
        showEdgeLabels,
        showNodeLabels,
        selectionContext,
        muteNonSelectedEdges,
        hiddenNodeIds,
      }),
    )
    renderer.refresh()
  }, [hiddenNodeIds, labelSizeThreshold, muteNonSelectedEdges, selectionContext, showEdgeLabels, showNodeLabels])

  useEffect(() => {
    if (!runtimeGraph || !sigmaRef.current) {
      previousLayoutRef.current = layout
      return
    }

    if (previousLayoutRef.current === layout) {
      return
    }

    previousLayoutRef.current = layout
    void relayoutRuntimeGraph(runtimeGraph)
  }, [layout, runtimeGraph])

  async function refreshWorkbenchMeta(standardId: string) {
    const [statusResult, labelsResult] = await Promise.allSettled([
      fetchGraphServiceStatus(standardId),
      fetchPopularGraphLabels(standardId, 120),
    ])

    if (statusResult.status === 'fulfilled') {
      setStatus(statusResult.value)
    } else {
      toast.error(extractErrorMessage(statusResult.reason, '图谱状态面板加载失败'))
      setStatus(null)
    }
    if (labelsResult.status === 'fulfilled') {
      setPopularLabels(labelsResult.value)
      if (!deferredLabelSearch.trim()) {
        setGlobalLabelResults(labelsResult.value.slice(0, 14))
      }
    } else {
      setPopularLabels([])
      setGlobalLabelResults([])
      setSearchPanelOpen(false)
    }
  }

  function bindRendererEvents(renderer: Sigma) {
    const releaseDrag = () => {
      draggingNodeRef.current = null
      renderer.setSetting('enableCameraPanning', true)
      renderer.getContainer().classList.remove('is-dragging-node')
    }

    renderer.on('clickNode', ({ node }) => {
      startTransition(() => {
        useGraphWorkbenchStore.getState().selectNode(node)
      })
    })

    renderer.on('clickEdge', ({ edge }) => {
      startTransition(() => {
        useGraphWorkbenchStore.getState().selectEdge(edge)
      })
    })

    renderer.on('clickStage', () => {
      startTransition(() => {
        useGraphWorkbenchStore.getState().clearSelection()
      })
    })

    renderer.on('downNode', ({ node, preventSigmaDefault }) => {
      draggingNodeRef.current = node
      preventSigmaDefault()
      renderer.setSetting('enableCameraPanning', false)
      renderer.getContainer().classList.add('is-dragging-node')
      startTransition(() => {
        useGraphWorkbenchStore.getState().selectNode(node)
      })
    })

    renderer.on('moveBody', ({ event, preventSigmaDefault }) => {
      const draggingNodeId = draggingNodeRef.current
      const graph = runtimeGraphRef.current
      if (!draggingNodeId || !graph) {
        return
      }

      preventSigmaDefault()
      const nextPosition = renderer.viewportToGraph({ x: event.x, y: event.y })
      graph.mergeNodeAttributes(draggingNodeId, {
        x: nextPosition.x,
        y: nextPosition.y,
      })
      renderer.refresh({ partialGraph: { nodes: [draggingNodeId] }, skipIndexation: true })
    })

    renderer.on('upNode', releaseDrag)
    renderer.on('upEdge', releaseDrag)
    renderer.on('upStage', releaseDrag)
    renderer.on('leaveStage', releaseDrag)
  }

  async function relayoutRuntimeGraph(graph: RuntimeGraph) {
    const renderer = sigmaRef.current
    if (!renderer) {
      return
    }

    setAnimatingLayout(true)
    try {
      const targetPositions = layoutGraph(graph, layoutRef.current)
      await animateLayoutTransition(graph, targetPositions, () => renderer.refresh(), 620)
    } finally {
      setAnimatingLayout(false)
    }
  }

  async function commitGraphSnapshot(nextRawGraph: GraphWorkbenchData, options: GraphCommitOptions = {}) {
    const previousRuntime = runtimeGraphRef.current
    const nextRuntime = createRuntimeGraph(nextRawGraph, {
      currentGraph: previousRuntime,
      rootNodeId: nextRawGraph.rootNodeId,
      anchorNodeId: options.anchorNodeId ?? options.selectNodeId ?? nextRawGraph.rootNodeId,
    })

    const renderer = sigmaRef.current
    runtimeGraphRef.current = nextRuntime
    rawGraphRef.current = nextRawGraph

    if (options.relayout === false) {
      setGraphData(nextRawGraph, nextRuntime)
      if (renderer) {
        renderer.setGraph(nextRuntime)
        renderer.refresh()
      }
    } else {
      const targetPositions = layoutGraph(nextRuntime, layoutRef.current)
      if (renderer && previousRuntime) {
        renderer.setGraph(nextRuntime)
        setGraphData(nextRawGraph, nextRuntime)
        setAnimatingLayout(true)
        try {
          await animateLayoutTransition(nextRuntime, targetPositions, () => renderer.refresh(), 620)
        } finally {
          setAnimatingLayout(false)
        }
      } else {
        Object.entries(targetPositions).forEach(([nodeId, position]) => {
          nextRuntime.mergeNodeAttributes(nodeId, position)
        })
        setGraphData(nextRawGraph, nextRuntime)
        if (renderer) {
          renderer.setGraph(nextRuntime)
          renderer.refresh()
        }
      }
    }

    if (options.selectNodeId !== undefined) {
      startTransition(() => selectNode(options.selectNodeId))
    } else if (options.selectEdgeId !== undefined) {
      startTransition(() => selectEdge(options.selectEdgeId))
    }

    if (renderer) {
      if (options.resetView) {
        await renderer.getCamera().animatedReset({ duration: 260 })
      } else if (options.centerNodeId) {
        centerNodeInView(renderer, nextRuntime, options.centerNodeId)
      }
    }
  }

  async function loadGraph(options: LoadGraphOptions) {
    setLoadingGraph(true)
    try {
      const incoming = await loadWorkbenchGraph({
        standardId: options.standardId,
        nodeId: options.nodeId,
        label: options.label,
        maxDepth,
        maxNodes,
      })

      const currentRawGraph = rawGraphRef.current
      const nextRawGraph = options.replace === false && currentRawGraph
        ? {
            ...mergeWorkbenchGraphs(currentRawGraph, incoming),
            rootNodeId: currentRawGraph.rootNodeId,
          }
        : incoming

      const startNodeId = incoming.rootNodeId ?? options.nodeId ?? nextRawGraph.rootNodeId ?? null
      const startNodeLabel = options.startLabel
        ?? (startNodeId ? nextRawGraph.nodes.find((node) => node.id === startNodeId)?.label : null)
        ?? options.standardId

      const preferredNodeId = options.selectNodeId && nextRawGraph.nodes.some((node) => node.id === options.selectNodeId)
        ? options.selectNodeId
        : null

      if (options.activateStart !== false) {
        setActiveStart(startNodeId, startNodeLabel ?? '')
      }

      await commitGraphSnapshot(nextRawGraph, {
        relayout: true,
        resetView: options.resetView,
        centerNodeId: options.centerNodeId ?? preferredNodeId ?? startNodeId,
        selectNodeId: preferredNodeId,
        anchorNodeId: options.nodeId ?? startNodeId,
      })
    } catch (error) {
      toast.error(extractErrorMessage(error, '图谱加载失败'))
    } finally {
      setLoadingGraph(false)
    }
  }

  async function handleExpandNode(nodeId: string) {
    if (!selectedStandardId || !rawGraphRef.current) {
      return
    }

    setLoadingGraph(true)
    try {
      const incoming = await loadWorkbenchGraph({
        standardId: selectedStandardId,
        nodeId,
          maxDepth,
        maxNodes,
      })
      const nextRaw = {
        ...mergeWorkbenchGraphs(rawGraphRef.current, incoming),
        rootNodeId: rawGraphRef.current.rootNodeId,
      }
      await commitGraphSnapshot(nextRaw, {
        relayout: true,
        resetView: false,
        centerNodeId: nodeId,
        selectNodeId: nodeId,
        anchorNodeId: nodeId,
      })
    } catch (error) {
      toast.error(extractErrorMessage(error, '节点扩展失败'))
    } finally {
      setLoadingGraph(false)
    }
  }

  async function handlePruneNode(nodeId: string) {
    const nextRaw = pruneWorkbenchGraph(rawGraphRef.current, nodeId)
    if (!nextRaw) {
      return
    }

    await commitGraphSnapshot(nextRaw, {
      relayout: true,
      resetView: false,
      centerNodeId: nextRaw.rootNodeId,
      selectNodeId: null,
      anchorNodeId: nextRaw.rootNodeId,
    })
    clearSelection()
  }

  async function handleRefreshLayout() {
    if (!runtimeGraphRef.current) {
      return
    }
    await relayoutRuntimeGraph(runtimeGraphRef.current)
  }

  async function handleReloadGraph() {
    if (!selectedStandardId) {
      return
    }
    await loadGraph({
      standardId: selectedStandardId,
      nodeId: activeStartNodeId,
      replace: true,
      resetView: true,
      activateStart: true,
      startLabel: activeStartLabel,
    })
    void refreshWorkbenchMeta(selectedStandardId)
  }

  async function handleLoadRootGraph() {
    if (!selectedStandardId) {
      return
    }

    await loadGraph({
      standardId: selectedStandardId,
      nodeId: null,
      replace: true,
      resetView: true,
      activateStart: true,
    })
  }

  function handleSelectGlobalLabel(item: GraphLabelItem) {
    setLabelSearchQuery(item.label)
    setStartDropdownOpen(false)
    setSearchPanelOpen(false)
    void loadGraph({
      standardId: item.standardId,
      nodeId: item.nodeId,
      replace: true,
      resetView: true,
      activateStart: true,
      startLabel: item.label,
      selectNodeId: item.nodeId,
    })
  }

  function handleSelectCanvasLabel(item: GraphLabelItem) {
    setCanvasSearchQuery(item.label)
    setCanvasDropdownOpen(false)
    startTransition(() => selectNode(item.nodeId))
    if (sigmaRef.current && runtimeGraphRef.current) {
      centerNodeInView(sigmaRef.current, runtimeGraphRef.current, item.nodeId)
    }
  }

  function handleSelectSpace(nextStandardId: string) {
    setSearchPanelOpen(false)
    startTransition(() => {
      setSelectedStandardId(nextStandardId || null)
    })
  }

  async function handleSaveNodeEdit(response: GraphEntityEditResponse) {
    const operationSummary = response.operation_summary
    if (!selectedStandardId) {
      return
    }

    if (operationSummary?.merged) {
      toast.success('节点已自动合并')
      await loadGraph({
        standardId: selectedStandardId,
        nodeId: response.data.id,
        replace: true,
        resetView: true,
        activateStart: true,
        startLabel: response.data.label,
        selectNodeId: response.data.id,
      })
      return
    }

    const currentRaw = rawGraphRef.current
    if (!currentRaw) {
      return
    }

    const nextRaw: GraphWorkbenchData = {
      ...currentRaw,
      nodes: currentRaw.nodes.map((node) => (node.id === response.data.id ? response.data : node)),
    }

    toast.success(operationSummary?.renamed ? '节点已重命名' : '节点属性已更新')
    await commitGraphSnapshot(nextRaw, {
      relayout: false,
      resetView: false,
      centerNodeId: response.data.id,
      selectNodeId: response.data.id,
      anchorNodeId: response.data.id,
    })
  }

  async function handleSaveRelationEdit(response: GraphRelationEditResponse) {
    const currentRaw = rawGraphRef.current
    if (!currentRaw) {
      return
    }

    const nextRaw: GraphWorkbenchData = {
      ...currentRaw,
      edges: currentRaw.edges.map((edge) => (edge.id === response.data.id ? response.data : edge)),
    }

    toast.success('关系属性已更新')
    await commitGraphSnapshot(nextRaw, {
      relayout: false,
      resetView: false,
      selectEdgeId: response.data.id,
    })
  }

  async function handleToggleFullscreen() {
    const shell = shellRef.current
    if (!shell) {
      return
    }

    if (document.fullscreenElement === shell) {
      await document.exitFullscreen()
      return
    }

    await shell.requestFullscreen()
  }

  async function handleRotateView() {
    const renderer = sigmaRef.current
    if (!renderer) {
      return
    }

    const camera = renderer.getCamera()
    await camera.animate(
      { angle: camera.getState().angle + Math.PI / 6 },
      { duration: 260 },
    )
  }

  async function handleZoomIn() {
    await sigmaRef.current?.getCamera().animatedZoom(1.35)
  }

  async function handleZoomOut() {
    await sigmaRef.current?.getCamera().animatedUnzoom(1.35)
  }

  async function handleResetView() {
    const renderer = sigmaRef.current
    if (!renderer) {
      return
    }

    await renderer.getCamera().animatedReset({ duration: 260 })
  }

  function handleToggleDock() {
    if (dockOpen) {
      setDockOpen(false)
      return
    }

    setDockPanelMode('actions')
    setDockOpen(true)
  }

  function handleToggleSearchPanel() {
    setSearchPanelOpen((current) => !current)
  }

  function handleToggleNodeTypeVisibility(nodeType: string) {
    const normalizedType = normalizeText(nodeType)
    setHiddenNodeTypes((current) => (
      current.includes(normalizedType)
        ? current.filter((item) => item !== normalizedType)
        : [...current, normalizedType]
    ))
  }

  function handleSelectDockPanel(panel: DockPanelMode) {
    if (dockOpen && dockPanelMode === panel) {
      setDockOpen(false)
      return
    }

    setDockPanelMode(panel)
    setDockOpen(true)
  }

  const hasGraph = Boolean(rawGraph?.nodes.length)

  return (
    <div ref={shellRef} className="workbench-shell">
      <div className="workbench-canvas-frame">
        <div className="workbench-grid" aria-hidden="true" />
        <div ref={canvasRef} className="workbench-canvas" />

        <div className="workbench-search-dock">
          {searchPanelOpen ? (
            <div className="workbench-card workbench-search-panel">
              <div className="workbench-search-panel-header">
                <div>
                  <h3>图谱搜索</h3>
                  <p>切换空间、选择起始节点并定位当前子图实体。</p>
                </div>
                <button type="button" onClick={() => setSearchPanelOpen(false)} className="workbench-icon-button" aria-label="Close search panel" title="Close search panel">
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="workbench-search-panel-body">
                <label className="workbench-field">
                  <span className="workbench-field-label">KG Space</span>
                  <select value={selectedStandardId ?? ''} onChange={(event) => handleSelectSpace(event.target.value)} className="workbench-select">
                    {spaces.map((space) => (
                      <option key={space.standardId} value={space.standardId}>
                        {space.standardId} · {space.title}
                      </option>
                    ))}
                  </select>
                </label>

                <div className="workbench-search-block">
                  <label className="workbench-field">
                    <span className="workbench-field-label">起始节点 / 全局标签搜索</span>
                    <div className="workbench-search-input-wrap">
                      <Search className="workbench-search-icon" />
                      <input
                        value={labelSearchQuery}
                        onChange={(event) => setLabelSearchQuery(event.target.value)}
                        onFocus={() => setStartDropdownOpen(true)}
                        onBlur={() => window.setTimeout(() => setStartDropdownOpen(false), 120)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter' && globalLabelResults[0]) {
                            event.preventDefault()
                            handleSelectGlobalLabel(globalLabelResults[0])
                          }
                        }}
                        className="workbench-input workbench-search-input"
                        placeholder="输入实体名，选择图谱起始节点"
                      />
                    </div>
                  </label>
                  {selectedStandardId && startDropdownOpen ? (
                    <div className="workbench-dropdown-panel">
                      <div className="workbench-dropdown-header">
                        <span>{deferredLabelSearch.trim() ? '匹配结果' : '热门标签'}</span>
                        <span>{globalLabelResults.length}</span>
                      </div>
                      {globalLabelResults.length === 0 ? (
                        <div className="workbench-empty-inline">没有可用的起始节点候选。</div>
                      ) : (
                        <div className="workbench-list">
                          {globalLabelResults.map((item) => (
                            <button
                              key={`${item.standardId}:${item.nodeId}`}
                              type="button"
                              onMouseDown={(event) => event.preventDefault()}
                              onClick={() => handleSelectGlobalLabel(item)}
                              className="workbench-list-item"
                            >
                              <div>
                                <strong>{item.label}</strong>
                                <p>{item.excerpt ?? item.nodeId}</p>
                              </div>
                              <span>{item.nodeType}</span>
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  ) : null}
                </div>

                <div className="workbench-search-block">
                  <label className="workbench-field">
                    <span className="workbench-field-label">页内搜索</span>
                    <div className="workbench-search-input-wrap">
                      <Search className="workbench-search-icon" />
                      <input
                        value={canvasSearchQuery}
                        onChange={(event) => setCanvasSearchQuery(event.target.value)}
                        onFocus={() => setCanvasDropdownOpen(true)}
                        onBlur={() => window.setTimeout(() => setCanvasDropdownOpen(false), 120)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter' && canvasSearchResults[0]) {
                            event.preventDefault()
                            handleSelectCanvasLabel(canvasSearchResults[0])
                          }
                        }}
                        className="workbench-input workbench-search-input"
                        placeholder="在当前子图中定位节点"
                      />
                    </div>
                  </label>
                  {canvasDropdownOpen && deferredCanvasSearch.trim() ? (
                    <div className="workbench-dropdown-panel">
                      <div className="workbench-dropdown-header">
                        <span>当前子图匹配</span>
                        <span>{canvasSearchResults.length}</span>
                      </div>
                      {canvasSearchResults.length === 0 ? (
                        <div className="workbench-empty-inline">当前图内没有匹配节点。</div>
                      ) : (
                        <div className="workbench-list">
                          {canvasSearchResults.map((item) => (
                            <button
                              key={item.nodeId}
                              type="button"
                              onMouseDown={(event) => event.preventDefault()}
                              onClick={() => handleSelectCanvasLabel(item)}
                              className="workbench-list-item"
                            >
                              <div>
                                <strong>{item.label}</strong>
                                <p>{item.excerpt ?? item.nodeId}</p>
                              </div>
                              <span>{item.nodeType}</span>
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  ) : null}
                </div>

                <div className="workbench-search-panel-meta">
                  <span className="workbench-pill">Start: {activeStartDisplay}</span>
                  {rawGraph ? <span className="workbench-pill">Nodes: {rawGraph.nodes.length}</span> : null}
                  {rawGraph ? <span className="workbench-pill">Edges: {rawGraph.edges.length}</span> : null}
                  {truncatedMessage ? <span className="workbench-pill is-warning">Truncated</span> : null}
                </div>

                <div className="workbench-search-panel-actions">
                  <button type="button" onClick={() => void handleLoadRootGraph()} className="workbench-inline-button">
                    Root
                  </button>
                  <button type="button" onClick={() => void handleReloadGraph()} className="workbench-inline-button">
                    Refresh
                  </button>
                </div>
              </div>
            </div>
          ) : null}

          <button
            type="button"
            className={clsx('workbench-search-trigger', searchPanelOpen && 'is-active')}
            onClick={handleToggleSearchPanel}
            title="Search panel"
            aria-label="Search panel"
          >
            {searchPanelOpen ? <X className="h-5 w-5" /> : <Search className="h-5 w-5" />}
          </button>
        </div>
        <div className={clsx('workbench-dock', dockOpen && 'is-open')}>
          {dockOpen ? (
            <div className="workbench-card workbench-dock-panel">
              <div className="workbench-dock-panel-header">
                <div>
                  <p className="section-kicker">Workbench Dock</p>
                  <h3>{dockPanelMode === 'settings' ? '图谱设置' : dockPanelMode === 'status' ? '服务状态' : '快捷操作'}</h3>
                </div>
                <button type="button" onClick={() => setDockOpen(false)} className="workbench-icon-button">
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="workbench-dock-tabs">
                <DockTabButton label="工具" icon={<LayoutGrid className="h-4 w-4" />} active={dockPanelMode === 'actions'} onClick={() => handleSelectDockPanel('actions')} />
                <DockTabButton label="设置" icon={<Settings2 className="h-4 w-4" />} active={dockPanelMode === 'settings'} onClick={() => handleSelectDockPanel('settings')} />
                <DockTabButton label="状态" icon={<Info className="h-4 w-4" />} active={dockPanelMode === 'status'} onClick={() => handleSelectDockPanel('status')} />
              </div>

              {dockPanelMode === 'actions' ? (
                <div className="workbench-dock-action-grid">
                  <DockActionButton label="放大" icon={<ZoomIn className="h-4 w-4" />} onClick={() => void handleZoomIn()} />
                  <DockActionButton label="缩小" icon={<ZoomOut className="h-4 w-4" />} onClick={() => void handleZoomOut()} />
                  <DockActionButton label="复位" icon={<Crosshair className="h-4 w-4" />} onClick={() => void handleResetView()} />
                  <DockActionButton label="旋转" icon={<RotateCw className="h-4 w-4" />} onClick={() => void handleRotateView()} />
                  <DockActionButton label="重排" icon={<LayoutGrid className="h-4 w-4" />} onClick={() => void handleRefreshLayout()} />
                  <DockActionButton label="根图" icon={<Network className="h-4 w-4" />} onClick={() => void handleLoadRootGraph()} disabled={!selectedStandardId} />
                  <DockActionButton label="刷新" icon={<RefreshCw className="h-4 w-4" />} onClick={() => void handleReloadGraph()} disabled={!selectedStandardId} />
                  <DockActionButton label="图例" icon={<Workflow className="h-4 w-4" />} onClick={() => setShowLegend(!showLegend)} active={showLegend} />
                  <DockActionButton label="全屏" icon={isFullscreen ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />} onClick={() => void handleToggleFullscreen()} active={isFullscreen} />
                </div>
              ) : null}

              {dockPanelMode === 'settings' ? (
                <div className="workbench-dock-sheet">
                  <label className="workbench-field">
                    <span className="workbench-field-label">Layout</span>
                    <select value={layout} onChange={(event) => setLayout(event.target.value as GraphLayoutMode)} className="workbench-select">
                      {LAYOUT_OPTIONS.map((option) => (
                        <option key={option} value={option}>
                          {getLayoutLabel(option)}
                        </option>
                      ))}
                    </select>
                  </label>

                  <div className="workbench-field-grid">
                    <label className="workbench-field">
                      <span className="workbench-field-label">Depth</span>
                      <select value={maxDepth} onChange={(event) => setMaxDepth(Number(event.target.value))} className="workbench-select">
                        {MAX_DEPTH_OPTIONS.map((value) => (
                          <option key={value} value={value}>{value}</option>
                        ))}
                      </select>
                    </label>
                    <label className="workbench-field">
                      <span className="workbench-field-label">Max Nodes</span>
                      <select value={maxNodes} onChange={(event) => setMaxNodes(Number(event.target.value))} className="workbench-select">
                        {MAX_NODE_OPTIONS.map((value) => (
                          <option key={value} value={value}>{formatMaxNodesValue(value)}</option>
                        ))}
                      </select>
                    </label>
                  </div>

                  <div className="workbench-toggle-list">
                    <ToggleButton label="Node Labels" active={showNodeLabels} onClick={() => setShowNodeLabels(!showNodeLabels)} />
                    <ToggleButton label="Edge Labels" active={showEdgeLabels} onClick={() => setShowEdgeLabels(!showEdgeLabels)} />
                    <ToggleButton label="弱化非选中边" active={muteNonSelectedEdges} onClick={() => setMuteNonSelectedEdges(!muteNonSelectedEdges)} />
                  </div>

                  <label className="workbench-field">
                    <span className="workbench-field-label">标签阈值 {labelSizeThreshold.toFixed(0)}</span>
                    <input type="range" min={4} max={18} step={1} value={labelSizeThreshold} onChange={(event) => setLabelSizeThreshold(Number(event.target.value))} className="workbench-range" disabled={!showNodeLabels} />
                  </label>

                  {truncatedMessage ? <div className="workbench-inline-notice">{truncatedMessage}</div> : null}
                </div>
              ) : null}

              {dockPanelMode === 'status' ? (
                <div className="workbench-dock-sheet">
                  <div className="workbench-kv-list">
                    <StatusRow label="Service" value={status?.status ?? '-'} />
                    <StatusRow label="Working Directory" value={status?.workingDirectory ?? '-'} />
                    <StatusRow label="Data Directory" value={status?.dataDirectory ?? '-'} />
                    <StatusRow label="Graph Space Directory" value={status?.graphSpaceDirectory ?? '-'} />
                    <StatusRow label="Uploads Directory" value={status?.uploadsDirectory ?? '-'} />
                  </div>

                  <div className="workbench-status-section">
                    <p className="section-kicker">Graph Limits</p>
                    <div className="workbench-kv-list">
                      {Object.entries(status?.graphLimits ?? {}).map(([key, value]) => (
                        <StatusRow key={key} label={key} value={String(value)} compact />
                      ))}
                    </div>
                  </div>

                  <div className="workbench-status-section">
                    <p className="section-kicker">Model / Configuration</p>
                    <div className="workbench-kv-list">
                      {flattenedStatusConfig.length === 0 ? <StatusRow label="Configuration" value="-" compact /> : null}
                      {flattenedStatusConfig.map(([key, value]) => (
                        <StatusRow key={key} label={key} value={value} compact />
                      ))}
                    </div>
                  </div>
                </div>
              ) : null}
            </div>
          ) : null}

          <button type="button" className={clsx('workbench-dock-trigger', dockOpen && 'is-active')} onClick={handleToggleDock} title="Graph workbench dock" aria-label="Graph workbench dock">
            {dockOpen ? <X className="h-5 w-5" /> : <Settings2 className="h-5 w-5" />}
          </button>
        </div>

        {showLegend ? (
          <div className={clsx('workbench-legend-stack', inspectorCollapsed && 'is-free')}>
            <div className="workbench-legend-float-section">
              <p className="workbench-legend-caption">Node Types</p>
              <div className="workbench-legend-empty">{hiddenNodeTypeMessage}</div>
              {nodeLegend.length === 0 ? <div className="workbench-legend-empty">暂无节点类型</div> : null}
              {nodeLegend.map((item) => {
                const isHidden = hiddenNodeTypeSet.has(normalizeText(item.name))
                return (
                  <LegendBeaconRow
                    key={`node-${item.name}`}
                    label={item.name}
                    count={item.count}
                    color={item.color}
                    muted={isHidden}
                    onClick={() => handleToggleNodeTypeVisibility(item.name)}
                    title={isHidden ? '点击恢复该实体类型' : '点击隐藏该实体类型'}
                  />
                )
              })}
            </div>

            <div className="workbench-legend-float-section">
              <p className="workbench-legend-caption">Edge Types</p>
              {edgeLegend.length === 0 ? <div className="workbench-legend-empty">暂无关系类型</div> : null}
              {edgeLegend.map((item) => (
                <LegendBeaconRow key={`edge-${item.name}`} label={item.name} count={item.count} color={item.color} />
              ))}
            </div>
          </div>
        ) : null}

        <div className={clsx('workbench-inspector-wrap', inspectorCollapsed && 'is-collapsed')}>
          {inspectorCollapsed ? (
            <button type="button" onClick={() => setInspectorCollapsed(false)} className="workbench-collapsed-inspector" title="Open details" aria-label="Open details">
              <ChevronLeft className="h-4 w-4" />
            </button>
          ) : (
            <aside className="workbench-card workbench-inspector-panel">
              <div className="workbench-card-header">
                <div>
                  <h3>{selectedNode ? '节点属性' : selectedEdge ? '关系属性' : '图谱工作台'}</h3>
                </div>
                <button type="button" onClick={() => setInspectorCollapsed(true)} className="workbench-icon-button">
                  <ChevronRight className="h-4 w-4" />
                </button>
              </div>

              {selectedNode ? (
                <div className="workbench-inspector-content">
                  <div className="workbench-inspector-title-row">
                    <div>
                      <h4>{selectedNode.label}</h4>
                      <p>{selectedNode.id}</p>
                    </div>
                    <span className="workbench-node-type-chip" style={{ color: resolveNodePalette(selectedNode.nodeType).text, background: resolveNodePalette(selectedNode.nodeType).soft }}>
                      {selectedNode.nodeType}
                    </span>
                  </div>

                  <div className="workbench-action-row">
                    <button type="button" onClick={() => void handleExpandNode(selectedNode.id)} className="workbench-action-button">
                      <GitBranchPlus className="h-4 w-4" />
                      Expand
                    </button>
                    <button type="button" onClick={() => void handlePruneNode(selectedNode.id)} className="workbench-action-button">
                      <Scissors className="h-4 w-4" />
                      Prune
                    </button>
                    <button type="button" onClick={() => setNodeEditorOpen(true)} className="workbench-action-button">
                      <Pencil className="h-4 w-4" />
                      Edit
                    </button>
                  </div>

                  <div className="workbench-kv-list">
                    <StatusRow label="Type" value={selectedNode.nodeType} compact />
                    <StatusRow label="Degree" value={String(selectedNode.degree)} compact />
                    <StatusRow label="Text" value={resolveNodeText(selectedNode)} compact />
                  </div>

                  <div className="workbench-inspector-section">
                    <div className="workbench-section-head">
                      <span className="section-kicker">Properties</span>
                    </div>
                    <pre className="workbench-code-block">{JSON.stringify(selectedNode.properties ?? {}, null, 2)}</pre>
                  </div>

                  <div className="workbench-inspector-section">
                    <div className="workbench-section-head">
                      <span className="section-kicker">Relations</span>
                      <span>{relatedRelations.length}</span>
                    </div>
                    {relatedRelations.length === 0 ? (
                      <div className="workbench-empty-inline">当前节点没有可展示的关系。</div>
                    ) : (
                      <div className="workbench-list compact">
                        {relatedRelations.map((item) => (
                          <button
                            key={item.edgeId}
                            type="button"
                            onClick={() => {
                              startTransition(() => selectEdge(item.edgeId))
                              if (sigmaRef.current && runtimeGraphRef.current) {
                                centerNodeInView(sigmaRef.current, runtimeGraphRef.current, item.otherNodeId)
                              }
                            }}
                            className="workbench-list-item is-compact"
                          >
                            <div>
                              <strong>{item.otherNodeLabel}</strong>
                              <p>{item.edgeType}</p>
                            </div>
                            <span>{item.direction}</span>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              ) : selectedEdge ? (
                <div className="workbench-inspector-content">
                  <div className="workbench-inspector-title-row">
                    <div>
                      <h4>{selectedEdge.edgeType}</h4>
                      <p>{selectedEdge.id}</p>
                    </div>
                    <span className="workbench-node-type-chip" style={{ color: 'var(--text-primary)', background: 'rgba(148, 163, 184, 0.14)' }}>
                      Relation
                    </span>
                  </div>

                  <div className="workbench-action-row">
                    <button type="button" onClick={() => setRelationEditorOpen(true)} className="workbench-action-button">
                      <Pencil className="h-4 w-4" />
                      Edit
                    </button>
                    <button type="button" onClick={() => { startTransition(() => selectNode(selectedEdge.source)); if (sigmaRef.current && runtimeGraphRef.current) { centerNodeInView(sigmaRef.current, runtimeGraphRef.current, selectedEdge.source) } }} className="workbench-action-button">
                      <Crosshair className="h-4 w-4" />
                      Source
                    </button>
                    <button type="button" onClick={() => { startTransition(() => selectNode(selectedEdge.target)); if (sigmaRef.current && runtimeGraphRef.current) { centerNodeInView(sigmaRef.current, runtimeGraphRef.current, selectedEdge.target) } }} className="workbench-action-button">
                      <Crosshair className="h-4 w-4" />
                      Target
                    </button>
                  </div>

                  <div className="workbench-kv-list">
                    <StatusRow label="Source" value={nodeMap.get(selectedEdge.source)?.label ?? selectedEdge.source} compact />
                    <StatusRow label="Target" value={nodeMap.get(selectedEdge.target)?.label ?? selectedEdge.target} compact />
                    <StatusRow label="Type" value={selectedEdge.edgeType} compact />
                  </div>

                  <div className="workbench-inspector-section">
                    <div className="workbench-section-head">
                      <span className="section-kicker">Properties</span>
                    </div>
                    <pre className="workbench-code-block">{JSON.stringify(selectedEdge.properties ?? {}, null, 2)}</pre>
                  </div>
                </div>
              ) : (
                <div className="workbench-inspector-content is-empty">
                  <div className="workbench-empty-inline large">
                    <Network className="h-5 w-5" />
                    <div>
                      <strong>选择一个节点或关系</strong>
                      <p>点击画布元素后，这里会显示属性、关系和编辑入口。</p>
                    </div>
                  </div>

                  <div className="workbench-kv-list">
                    <StatusRow label="Current Start" value={activeStartDisplay} compact />
                    <StatusRow label="Layout" value={getLayoutLabel(layout)} compact />
                    <StatusRow label="Graph Status" value={selectedSpace?.graphStatus ?? '-'} compact />
                    <StatusRow label="Nodes / Edges" value={rawGraph ? `${rawGraph.nodes.length} / ${rawGraph.edges.length}` : '-'} compact />
                  </div>

                  <div className="workbench-inspector-section">
                    <div className="workbench-section-head">
                      <span className="section-kicker">操作提示</span>
                    </div>
                    <div className="workbench-hint-list">
                      <div>点击节点: 高亮并打开属性面板</div>
                      <div>拖拽节点: 直接调整局部结构</div>
                      <div>Expand / Prune: 扩展或裁剪当前工作区</div>
                      <div>顶部搜索: 切换起始节点或在页内定位</div>
                    </div>
                  </div>
                </div>
              )}
            </aside>
          )}
        </div>

        {!hasGraph && !isLoadingGraph ? (
          <div className="workbench-empty-state">
            <div className="workbench-empty-inline large">
              <Network className="h-6 w-6" />
              <div>
                <strong>图谱画布已就绪</strong>
                <p>先选择一个知识空间，然后从顶部搜索栏挑选起始节点，或直接从根节点进入。</p>
              </div>
            </div>
          </div>
        ) : null}

        {isLoadingGraph || isAnimatingLayout ? (
          <div className="workbench-loading-mask">
            <LoaderCircle className="h-5 w-5 animate-spin" />
            <span>{isLoadingGraph ? '图谱加载中...' : '布局计算中...'}</span>
          </div>
        ) : null}
      </div>

      <NodeEditDialog open={nodeEditorOpen} onOpenChange={setNodeEditorOpen} standardId={selectedStandardId} node={selectedNode} onSaved={(response) => void handleSaveNodeEdit(response)} />
      <RelationEditDialog open={relationEditorOpen} onOpenChange={setRelationEditorOpen} relation={selectedEdge} onSaved={(response) => void handleSaveRelationEdit(response)} />
    </div>
  )
}

function createSigmaSettings(options: {
  labelSizeThreshold: number
  showEdgeLabels: boolean
  showNodeLabels: boolean
  selectionContext: {
    connectedNodeIds: Set<string>
    connectedEdgeIds: Set<string>
    searchMatches: Set<string>
  }
  muteNonSelectedEdges: boolean
  hiddenNodeIds: Set<string>
}) {
  const { labelSizeThreshold, showEdgeLabels, showNodeLabels, selectionContext, muteNonSelectedEdges, hiddenNodeIds } = options

  return {
    allowInvalidContainer: true,
    defaultEdgeType: 'curve',
    edgeProgramClasses: { curve: EdgeCurveProgram },
    enableEdgeEvents: true,
    hideLabelsOnMove: true,
    labelDensity: 0.85,
    labelRenderedSizeThreshold: showNodeLabels ? labelSizeThreshold : Number.MAX_SAFE_INTEGER,
    labelFont: 'IBM Plex Sans',
    edgeLabelFont: 'IBM Plex Mono',
    renderEdgeLabels: showEdgeLabels,
    zIndex: true,
    nodeReducer: (node: string, data: Record<string, unknown>) => {
      if (hiddenNodeIds.has(node)) {
        return { ...data, hidden: true, label: showNodeLabels ? data.label : '', forceLabel: false, highlighted: false }
      }

      const isSelected = selectionContext.connectedNodeIds.has(node)
      const isSearchHit = selectionContext.searchMatches.has(node)
      const selectedNodeId = useGraphWorkbenchStore.getState().selectedNodeId
      const next: Record<string, unknown> = { hidden: false, label: showNodeLabels ? data.label : '', forceLabel: false }

      if (isSelected) {
        if (showNodeLabels) {
          next.forceLabel = true
        }
        next.zIndex = node === selectedNodeId ? 9 : 6
        next.highlighted = true
        next.size = typeof data.size === 'number' ? data.size * (node === selectedNodeId ? 1.22 : 1.08) : data.size
      }

      if (isSearchHit) {
        if (showNodeLabels) {
          next.forceLabel = true
        }
        next.highlighted = true
        next.zIndex = Math.max(Number(next.zIndex ?? data.zIndex ?? 1), 8)
        next.size = typeof data.size === 'number' ? Math.max(data.size * 1.12, Number(next.size ?? 0)) : next.size
      }

      return { ...data, ...next }
    },
    edgeReducer: (_edge: string, data: Record<string, unknown>) => {
      const edgeId = String(data.edgeId ?? '')
      const sourceNodeId = String(data.sourceNodeId ?? '')
      const targetNodeId = String(data.targetNodeId ?? '')
      const selectedEdgeId = useGraphWorkbenchStore.getState().selectedEdgeId
      const selectedNodeId = useGraphWorkbenchStore.getState().selectedNodeId
      const isSelected = edgeId === selectedEdgeId
      const isConnected = selectionContext.connectedEdgeIds.has(edgeId)
      const isHidden = hiddenNodeIds.has(sourceNodeId) || hiddenNodeIds.has(targetNodeId)
      const next: Record<string, unknown> = { label: showEdgeLabels ? data.label : null, hidden: isHidden }

      if (isHidden) {
        return { ...data, ...next }
      }

      if ((selectedNodeId || selectedEdgeId) && muteNonSelectedEdges && !isSelected && !isConnected) {
        next.color = 'rgba(15, 23, 42, 0.08)'
      }

      if (isConnected) {
        next.color = 'rgba(15, 23, 42, 0.68)'
        next.zIndex = 4
        next.size = typeof data.size === 'number' ? data.size + 0.2 : data.size
      }

      if (isSelected) {
        next.color = 'rgba(0, 0, 0, 0.94)'
        next.zIndex = 8
        next.size = typeof data.size === 'number' ? data.size + 1 : data.size
        next.forceLabel = showEdgeLabels
      }

      return { ...data, ...next }
    },
  }
}

function DockTabButton({ label, icon, active, onClick }: { label: string; icon: React.ReactNode; active: boolean; onClick: () => void }) {
  return (
    <button type="button" className={clsx('workbench-dock-tab', active && 'is-active')} onClick={onClick}>
      {icon}
      <span>{label}</span>
    </button>
  )
}

function DockActionButton({ label, icon, onClick, active = false, disabled = false }: { label: string; icon: React.ReactNode; onClick: () => void; active?: boolean; disabled?: boolean }) {
  return (
    <button type="button" className={clsx('workbench-dock-action', active && 'is-active')} onClick={onClick} disabled={disabled}>
      {icon}
      <span>{label}</span>
    </button>
  )
}

function LegendBeaconRow({
  label,
  count,
  color,
  active = false,
  muted = false,
  onClick,
  title,
}: {
  label: string
  count: number
  color: string
  active?: boolean
  muted?: boolean
  onClick?: () => void
  title?: string
}) {
  const className = clsx(
    'workbench-legend-float-row',
    active && 'is-active',
    muted && 'is-muted',
    onClick && 'is-interactive',
  )

  if (onClick) {
    return (
      <button type="button" className={className} onClick={onClick} aria-pressed={active} title={title} style={{ '--legend-color': color } as React.CSSProperties}>
        <span className="workbench-legend-beacon" />
        <span>{label}</span>
        <strong>{count}</strong>
      </button>
    )
  }

  return (
    <div className={className} title={title} style={{ '--legend-color': color } as React.CSSProperties}>
      <span className="workbench-legend-beacon" />
      <span>{label}</span>
      <strong>{count}</strong>
    </div>
  )
}

function ToggleButton({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button type="button" className={clsx('workbench-toggle-button', active && 'is-active')} onClick={onClick}>
      <span>{label}</span>
      <span>{active ? 'On' : 'Off'}</span>
    </button>
  )
}

function StatusRow({ label, value, compact = false }: { label: string; value: string; compact?: boolean }) {
  return (
    <div className={clsx('workbench-kv-row', compact && 'is-compact')}>
      <span>{label}</span>
      <strong>{value || '-'}</strong>
    </div>
  )
}

function NodeEditDialog({
  open,
  onOpenChange,
  standardId,
  node,
  onSaved,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  standardId: string | null
  node: GraphWorkbenchNode | null
  onSaved: (response: GraphEntityEditResponse) => void
}) {
  const [label, setLabel] = useState('')
  const [nodeType, setNodeType] = useState('')
  const [textContent, setTextContent] = useState('')
  const [propertiesText, setPropertiesText] = useState('{}')
  const [allowMerge, setAllowMerge] = useState(false)
  const [duplicateInfo, setDuplicateInfo] = useState<{ exists: boolean; nodeId?: string | null } | null>(null)
  const [saving, setSaving] = useState(false)

  const deferredLabel = useDeferredValue(label)
  const renamed = Boolean(node && normalizeText(label) !== normalizeText(node.label))

  useEffect(() => {
    if (!open || !node) {
      return
    }

    setLabel(node.label)
    setNodeType(node.nodeType)
    setTextContent(resolveNodeText(node))
    setPropertiesText(JSON.stringify(node.properties ?? {}, null, 2))
    setAllowMerge(false)
    setDuplicateInfo(null)
  }, [node, open])

  useEffect(() => {
    if (!open || !standardId || !node) {
      return
    }

    const trimmed = deferredLabel.trim()
    if (!trimmed || normalizeText(trimmed) === normalizeText(node.label)) {
      setDuplicateInfo(null)
      return
    }

    let cancelled = false
    checkGraphEntityExists(standardId, trimmed, node.id)
      .then((result) => {
        if (!cancelled) {
          setDuplicateInfo({ exists: result.exists, nodeId: result.nodeId })
        }
      })
      .catch(() => {
        if (!cancelled) {
          setDuplicateInfo(null)
        }
      })

    return () => {
      cancelled = true
    }
  }, [deferredLabel, node, open, standardId])

  async function handleSubmit() {
    if (!standardId || !node) {
      return
    }

    let parsedProperties: Record<string, unknown>
    try {
      parsedProperties = JSON.parse(propertiesText || '{}') as Record<string, unknown>
    } catch {
      toast.error('Properties JSON 解析失败')
      return
    }

    if (duplicateInfo?.exists && renamed && !allowMerge) {
      toast.error('发现重名节点，请勾选自动合并后再保存')
      return
    }

    setSaving(true)
    try {
      const response = await editGraphEntity({
        standardId,
        nodeId: node.id,
        entityName: node.label,
        allowRename: renamed,
        allowMerge: Boolean(duplicateInfo?.exists && allowMerge),
        updatedData: {
          label,
          node_type: nodeType,
          text_content: textContent,
          properties: parsedProperties,
        },
      })
      onSaved(response)
      onOpenChange(false)
    } catch (error) {
      toast.error(extractErrorMessage(error, '节点保存失败'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <WorkbenchDialog open={open} onOpenChange={onOpenChange} title="编辑节点">
      {!node ? null : (
        <div className="workbench-dialog-grid">
          <label className="workbench-field">
            <span className="workbench-field-label">Label</span>
            <input value={label} onChange={(event) => setLabel(event.target.value)} className="workbench-input" />
          </label>

          <div className="workbench-field-grid">
            <label className="workbench-field">
              <span className="workbench-field-label">Node Type</span>
              <input value={nodeType} onChange={(event) => setNodeType(event.target.value)} className="workbench-input" />
            </label>
            <label className="workbench-field">
              <span className="workbench-field-label">Node ID</span>
              <input value={node.id} readOnly className="workbench-input is-readonly" />
            </label>
          </div>

          <label className="workbench-field">
            <span className="workbench-field-label">Text Content</span>
            <textarea value={textContent} onChange={(event) => setTextContent(event.target.value)} rows={6} className="workbench-textarea" />
          </label>

          <label className="workbench-field">
            <span className="workbench-field-label">Properties JSON</span>
            <textarea value={propertiesText} onChange={(event) => setPropertiesText(event.target.value)} rows={10} className="workbench-textarea is-mono" />
          </label>

          {duplicateInfo?.exists ? (
            <div className="workbench-inline-notice is-warning">
              <strong>检测到重名节点</strong>
              <p>目标实体已存在，保存时可选择自动合并到现有节点。</p>
              <label className="workbench-checkbox-row">
                <input type="checkbox" checked={allowMerge} onChange={(event) => setAllowMerge(event.target.checked)} />
                <span>允许自动合并同名实体</span>
              </label>
            </div>
          ) : renamed ? (
            <div className="workbench-inline-notice">当前会执行重命名检查，若目标名称唯一则直接保存。</div>
          ) : null}

          <div className="workbench-dialog-actions">
            <button type="button" onClick={() => onOpenChange(false)} className="workbench-action-button">取消</button>
            <button type="button" onClick={() => void handleSubmit()} className="workbench-action-button is-primary" disabled={saving}>
              {saving ? '保存中...' : '保存节点'}
            </button>
          </div>
        </div>
      )}
    </WorkbenchDialog>
  )
}

function RelationEditDialog({
  open,
  onOpenChange,
  relation,
  onSaved,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  relation: GraphWorkbenchEdge | null
  onSaved: (response: GraphRelationEditResponse) => void
}) {
  const [edgeType, setEdgeType] = useState('')
  const [propertiesText, setPropertiesText] = useState('{}')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!open || !relation) {
      return
    }

    setEdgeType(relation.edgeType)
    setPropertiesText(JSON.stringify(relation.properties ?? {}, null, 2))
  }, [open, relation])

  async function handleSubmit() {
    if (!relation || !useAppStore.getState().selectedStandardId) {
      return
    }

    let parsedProperties: Record<string, unknown>
    try {
      parsedProperties = JSON.parse(propertiesText || '{}') as Record<string, unknown>
    } catch {
      toast.error('Properties JSON 解析失败')
      return
    }

    setSaving(true)
    try {
      const response = await editGraphRelation({
        standardId: useAppStore.getState().selectedStandardId!,
        edgeId: relation.id,
        sourceId: relation.source,
        targetId: relation.target,
        updatedData: {
          edge_type: edgeType,
          properties: parsedProperties,
        },
      })
      onSaved(response)
      onOpenChange(false)
    } catch (error) {
      toast.error(extractErrorMessage(error, '关系保存失败'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <WorkbenchDialog open={open} onOpenChange={onOpenChange} title="编辑关系">
      {!relation ? null : (
        <div className="workbench-dialog-grid">
          <div className="workbench-field-grid">
            <label className="workbench-field">
              <span className="workbench-field-label">Edge Type</span>
              <input value={edgeType} onChange={(event) => setEdgeType(event.target.value)} className="workbench-input" />
            </label>
            <label className="workbench-field">
              <span className="workbench-field-label">Edge ID</span>
              <input value={relation.id} readOnly className="workbench-input is-readonly" />
            </label>
          </div>

          <div className="workbench-field-grid">
            <label className="workbench-field">
              <span className="workbench-field-label">Source</span>
              <input value={relation.source} readOnly className="workbench-input is-readonly" />
            </label>
            <label className="workbench-field">
              <span className="workbench-field-label">Target</span>
              <input value={relation.target} readOnly className="workbench-input is-readonly" />
            </label>
          </div>

          <label className="workbench-field">
            <span className="workbench-field-label">Properties JSON</span>
            <textarea value={propertiesText} onChange={(event) => setPropertiesText(event.target.value)} rows={10} className="workbench-textarea is-mono" />
          </label>

          <div className="workbench-dialog-actions">
            <button type="button" onClick={() => onOpenChange(false)} className="workbench-action-button">取消</button>
            <button type="button" onClick={() => void handleSubmit()} className="workbench-action-button is-primary" disabled={saving}>
              {saving ? '保存中...' : '保存关系'}
            </button>
          </div>
        </div>
      )}
    </WorkbenchDialog>
  )
}

function WorkbenchDialog({ open, onOpenChange, title, children }: { open: boolean; onOpenChange: (open: boolean) => void; title: string; children: React.ReactNode }) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="workbench-dialog-overlay" />
        <Dialog.Content className="workbench-dialog-surface">
          <div className="workbench-dialog-header">
            <Dialog.Title>{title}</Dialog.Title>
            <Dialog.Close className="workbench-icon-button">
              <X className="h-4 w-4" />
            </Dialog.Close>
          </div>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function buildDistribution(values: string[]) {
  const counts = new Map<string, number>()
  values.forEach((value) => counts.set(value, (counts.get(value) ?? 0) + 1))
  return [...counts.entries()].sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0], 'zh-CN'))
}

function buildDistributionFromCounts(values: Record<string, number>) {
  return Object.entries(values).sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0], 'zh-CN'))
}

function formatMaxNodesValue(value: number) {
  return value === ALL_MAX_NODES_VALUE ? 'All' : String(value)
}

function resolveNodeText(node: GraphWorkbenchNode) {
  const text = node.properties?.text_content
  if (typeof text === 'string' && text.trim()) {
    return text.trim()
  }
  return '-'
}

function flattenConfig(source: Record<string, unknown>, prefix = ''): Array<[string, string]> {
  const rows: Array<[string, string]> = []
  Object.entries(source).forEach(([key, value]) => {
    const nextKey = prefix ? `${prefix}.${key}` : key
    if (value == null) {
      return
    }
    if (typeof value === 'object' && !Array.isArray(value)) {
      rows.push(...flattenConfig(value as Record<string, unknown>, nextKey))
      return
    }
    rows.push([nextKey, formatValue(value)])
  })
  return rows
}

function formatValue(value: unknown) {
  if (Array.isArray(value)) {
    return value.map((item) => formatValue(item)).join(', ')
  }
  if (typeof value === 'boolean') {
    return value ? 'true' : 'false'
  }
  return String(value)
}

function extractErrorMessage(error: unknown, fallback: string) {
  if (typeof error === 'object' && error && 'response' in error) {
    const detail = (error as { response?: { data?: { detail?: string } } }).response?.data?.detail
    if (detail) {
      return detail
    }
  }
  if (error instanceof Error && error.message) {
    return error.message
  }
  return fallback
}






