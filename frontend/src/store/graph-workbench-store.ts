import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'

import type { GraphServiceStatus, GraphWorkbenchData } from '../lib/api'
import type { GraphLayoutMode, RuntimeGraph } from '../lib/graph-workbench'

interface GraphWorkbenchState {
  layout: GraphLayoutMode
  maxDepth: number
  maxNodes: number
  showLegend: boolean
  showStatusPanel: boolean
  inspectorCollapsed: boolean
  showEdgeLabels: boolean
  muteNonSelectedEdges: boolean
  labelSizeThreshold: number
  rawGraph: GraphWorkbenchData | null
  runtimeGraph: RuntimeGraph | null
  status: GraphServiceStatus | null
  selectedNodeId: string | null
  selectedEdgeId: string | null
  activeStartNodeId: string | null
  activeStartLabel: string
  canvasSearchQuery: string
  labelSearchQuery: string
  isLoadingGraph: boolean
  isAnimatingLayout: boolean
  setLayout: (layout: GraphLayoutMode) => void
  setMaxDepth: (value: number) => void
  setMaxNodes: (value: number) => void
  setShowLegend: (value: boolean) => void
  setShowStatusPanel: (value: boolean) => void
  setInspectorCollapsed: (value: boolean) => void
  setShowEdgeLabels: (value: boolean) => void
  setMuteNonSelectedEdges: (value: boolean) => void
  setLabelSizeThreshold: (value: number) => void
  setGraphData: (rawGraph: GraphWorkbenchData | null, runtimeGraph: RuntimeGraph | null) => void
  setRuntimeGraph: (runtimeGraph: RuntimeGraph | null) => void
  setStatus: (status: GraphServiceStatus | null) => void
  setLoadingGraph: (value: boolean) => void
  setAnimatingLayout: (value: boolean) => void
  setActiveStart: (nodeId: string | null, label: string) => void
  selectNode: (nodeId: string | null) => void
  selectEdge: (edgeId: string | null) => void
  clearSelection: () => void
  setCanvasSearchQuery: (value: string) => void
  setLabelSearchQuery: (value: string) => void
  resetGraphState: () => void
}

export const useGraphWorkbenchStore = create<GraphWorkbenchState>()(
  persist(
    (set) => ({
      layout: 'force-atlas',
      maxDepth: 2,
      maxNodes: 220,
      showLegend: true,
      showStatusPanel: false,
      inspectorCollapsed: true,
      showEdgeLabels: false,
      muteNonSelectedEdges: true,
      labelSizeThreshold: 8,
      rawGraph: null,
      runtimeGraph: null,
      status: null,
      selectedNodeId: null,
      selectedEdgeId: null,
      activeStartNodeId: null,
      activeStartLabel: '',
      canvasSearchQuery: '',
      labelSearchQuery: '',
      isLoadingGraph: false,
      isAnimatingLayout: false,
      setLayout: (layout) => set({ layout }),
      setMaxDepth: (maxDepth) => set({ maxDepth }),
      setMaxNodes: (maxNodes) => set({ maxNodes }),
      setShowLegend: (showLegend) => set({ showLegend }),
      setShowStatusPanel: (showStatusPanel) => set({ showStatusPanel }),
      setInspectorCollapsed: (inspectorCollapsed) => set({ inspectorCollapsed }),
      setShowEdgeLabels: (showEdgeLabels) => set({ showEdgeLabels }),
      setMuteNonSelectedEdges: (muteNonSelectedEdges) => set({ muteNonSelectedEdges }),
      setLabelSizeThreshold: (labelSizeThreshold) => set({ labelSizeThreshold }),
      setGraphData: (rawGraph, runtimeGraph) =>
        set({
          rawGraph,
          runtimeGraph,
          selectedNodeId: null,
          selectedEdgeId: null,
          inspectorCollapsed: true,
        }),
      setRuntimeGraph: (runtimeGraph) => set({ runtimeGraph }),
      setStatus: (status) => set({ status }),
      setLoadingGraph: (isLoadingGraph) => set({ isLoadingGraph }),
      setAnimatingLayout: (isAnimatingLayout) => set({ isAnimatingLayout }),
      setActiveStart: (activeStartNodeId, activeStartLabel) => set({ activeStartNodeId, activeStartLabel }),
      selectNode: (selectedNodeId) => set({ selectedNodeId, selectedEdgeId: null, inspectorCollapsed: false }),
      selectEdge: (selectedEdgeId) => set({ selectedEdgeId, selectedNodeId: null, inspectorCollapsed: false }),
      clearSelection: () => set({ selectedNodeId: null, selectedEdgeId: null, inspectorCollapsed: true }),
      setCanvasSearchQuery: (canvasSearchQuery) => set({ canvasSearchQuery }),
      setLabelSearchQuery: (labelSearchQuery) => set({ labelSearchQuery }),
      resetGraphState: () =>
        set({
          rawGraph: null,
          runtimeGraph: null,
          selectedNodeId: null,
          selectedEdgeId: null,
          inspectorCollapsed: true,
          activeStartNodeId: null,
          activeStartLabel: '',
          canvasSearchQuery: '',
          labelSearchQuery: '',
          isLoadingGraph: false,
          isAnimatingLayout: false,
        }),
    }),
    {
      name: 'kg-agent-hhu-graph-workbench',
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        layout: state.layout,
        maxDepth: state.maxDepth,
        maxNodes: state.maxNodes,
        showLegend: state.showLegend,
        showStatusPanel: state.showStatusPanel,
        inspectorCollapsed: state.inspectorCollapsed,
        showEdgeLabels: state.showEdgeLabels,
        muteNonSelectedEdges: state.muteNonSelectedEdges,
        labelSizeThreshold: state.labelSizeThreshold,
      }),
    },
  ),
)