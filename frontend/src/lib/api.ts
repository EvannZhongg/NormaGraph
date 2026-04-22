import axios from 'axios'

import { useAppStore } from '../store/app-store'

export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed'
export type DocumentStatus = 'idle' | 'queued' | 'running' | 'succeeded' | 'failed' | 'ready'

export interface IngestionJob {
  jobId: string
  status: JobStatus
  documentId: string
  documentType: 'standard' | 'report'
  parserProvider: string
  parserEndpoint?: string | null
  normalizedFormat?: string | null
  preprocessingActions: string[]
  progress: number
  result: Record<string, unknown>
  error?: string | null
  createdAt: string
  updatedAt: string
}

export interface DocumentSummary {
  documentId: string
  displayName: string
  standardId?: string | null
  title?: string | null
  documentType: 'standard' | 'report'
  sourcePath?: string | null
  sourceName?: string | null
  sourceFormat?: string | null
  status: DocumentStatus
  graphStatus?: 'not_built' | 'building' | 'ready' | 'failed' | null
  progress: number
  latestJobId?: string | null
  latestError?: string | null
  parserProvider?: string | null
  artifactDir?: string | null
  graphSpaceDir?: string | null
  metadata: Record<string, unknown>
  createdAt?: string | null
  updatedAt?: string | null
}

export interface ReportSectionSummary {
  sectionUid: string
  parentSectionUid?: string | null
  title: string
  sectionKind: string
  path: string[]
  orderIndex: number
  pageSpan: number[]
  memberCount: number
}

export interface ReportUnitSummary {
  unitUid: string
  parentSectionUid?: string | null
  unitType: string
  sectionPath: string[]
  structuralPath: string[]
  text: string
  textNormalized: string
  orderIndex: number
  pageSpan: number[]
}

export interface ReportSpaceDetail {
  documentId: string
  reportSpaceDir: string
  artifactDir?: string | null
  metrics: Record<string, unknown>
  sections: ReportSectionSummary[]
  reportUnits: ReportUnitSummary[]
}

export interface ReportComparisonItem {
  clauseId: string
  clauseRef?: string | null
  sectionId?: string | null
  chapterId?: string | null
  label: string
  status: 'covered' | 'partial' | 'missing' | 'violated' | 'not_applicable'
  reason: string
  reportEvidence?: string | null
}

export interface ReportUnitComparisonResult {
  documentId: string
  reportUnitId: string
  parentSectionUid?: string | null
  standardId: string
  summary: string
  coverageScore: number
  chapterRoutingReasoning?: string | null
  sectionRoutingReasoning?: string | null
  matchedChapterIds: string[]
  matchedSectionIds: string[]
  items: ReportComparisonItem[]
  graph: GraphWorkbenchData
}

export interface ReportComparisonResult extends ReportUnitComparisonResult {}

export interface ReportComparisonDetail {
  documentId: string
  standardId: string
  status: JobStatus
  progress: number
  totalUnits: number
  completedUnits: number
  startedAt?: string | null
  updatedAt?: string | null
  completedAt?: string | null
  summary: string
  coverageScore: number
  matchedChapterIds: string[]
  matchedSectionIds: string[]
  items: ReportComparisonItem[]
  unitResults: ReportUnitComparisonResult[]
  error?: string | null
}

export interface KgSpaceSummary {
  standardId: string
  code: string
  title: string
  graphStatus: 'not_built' | 'building' | 'ready' | 'failed'
  graphSpaceDir?: string | null
  artifactDir?: string | null
  nodeCount: number
  edgeCount: number
  requirementCount: number
  nodeTypes: Record<string, number>
  edgeTypes: Record<string, number>
  updatedAt?: string | null
}

export interface KgSpaceDetail extends KgSpaceSummary {
  aliases: string[]
  documentId?: string | null
  files: string[]
}

export interface GraphNodeData extends Record<string, unknown> {
  node_uid: string
  node_type: string
  label?: string
  text_content?: string
  properties?: Record<string, unknown>
}

export interface GraphEdgeData extends Record<string, unknown> {
  edge_uid: string
  edge_type: string
  source_uid: string
  target_uid: string
  properties?: Record<string, unknown>
}

export interface GraphSearchItem {
  nodeId: string
  nodeType: string
  label: string
  excerpt?: string | null
}

export interface GraphLabelItem {
  standardId: string
  nodeId: string
  label: string
  nodeType: string
  degree: number
  excerpt?: string | null
}

export interface GraphWorkbenchNode {
  id: string
  label: string
  nodeType: string
  properties: Record<string, unknown>
  degree: number
}

export interface GraphWorkbenchEdge {
  id: string
  source: string
  target: string
  edgeType: string
  properties: Record<string, unknown>
}

export interface GraphWorkbenchData {
  standardId: string
  rootNodeId?: string | null
  maxDepth: number
  maxNodes: number
  isTruncated: boolean
  nodes: GraphWorkbenchNode[]
  edges: GraphWorkbenchEdge[]
}

export interface GraphServiceStatus {
  status: string
  workingDirectory: string
  dataDirectory: string
  graphSpaceDirectory: string
  uploadsDirectory: string
  configuration: Record<string, unknown>
  graphLimits: Record<string, number>
  selectedSpace?: KgSpaceDetail | null
}

export interface GraphEntityExistsResponse {
  exists: boolean
  nodeId?: string | null
  standardId?: string | null
}

export interface GraphEntityEditRequest {
  standardId: string
  nodeId?: string
  entityName?: string
  updatedData: Record<string, unknown>
  allowRename?: boolean
  allowMerge?: boolean
}

export interface GraphEntityEditResponse {
  status: string
  message: string
  data: GraphWorkbenchNode
  operation_summary?: {
    merged: boolean
    merge_status: string
    merge_error?: string | null
    operation_status: string
    target_entity?: string | null
    final_entity: string
    final_node_id?: string | null
    renamed: boolean
  }
}

export interface GraphRelationEditRequest {
  standardId: string
  edgeId?: string
  sourceId?: string
  targetId?: string
  updatedData: Record<string, unknown>
}

export interface GraphRelationEditResponse {
  status: string
  message: string
  data: GraphWorkbenchEdge
}

const api = axios.create({
  baseURL: '/',
})

api.interceptors.request.use((config) => {
  const token = useAppStore.getState().token
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

export async function listDocuments(params?: { status?: string; q?: string }) {
  const response = await api.get<{ items: DocumentSummary[] }>('/v1/documents', { params })
  return response.data.items
}

export async function uploadDocument(formData: FormData) {
  const response = await api.post<IngestionJob>('/v1/documents/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return response.data
}

export async function listDocumentJobs(documentId: string) {
  const response = await api.get<{ items: IngestionJob[] }>(`/v1/documents/${documentId}/jobs`)
  return response.data.items
}

export async function getReportSpace(documentId: string) {
  const response = await api.get<ReportSpaceDetail>(`/v1/report-spaces/${documentId}`)
  return response.data
}

export async function compareReportUnit(documentId: string, unitUid: string, standardId: string) {
  const response = await api.post<ReportComparisonResult>(`/v1/report-spaces/${documentId}/units/${encodeURIComponent(unitUid)}/compare`, {
    standardId,
  })
  return response.data
}

export async function startReportComparison(documentId: string, standardId: string) {
  const response = await api.post<ReportComparisonDetail>(`/v1/report-spaces/${documentId}/comparisons`, {
    standardId,
  })
  return response.data
}

export async function getReportComparison(documentId: string, standardId: string) {
  const response = await api.get<ReportComparisonDetail>(`/v1/report-spaces/${documentId}/comparisons/${encodeURIComponent(standardId)}`)
  return response.data
}

export async function retryDocument(documentId: string) {
  const response = await api.post<IngestionJob>(`/v1/documents/${documentId}/retry`)
  return response.data
}

export async function deleteDocument(documentId: string) {
  await api.delete(`/v1/documents/${documentId}`)
}

export async function listKgSpaces() {
  const response = await api.get<{ items: KgSpaceSummary[] }>('/v1/kg-spaces')
  return response.data.items
}

export async function getKgSpace(standardId: string) {
  const response = await api.get<KgSpaceDetail>(`/v1/kg-spaces/${standardId}`)
  return response.data
}

export async function searchKgNodes(standardId: string, q: string, limit = 20) {
  const response = await api.get<{ items: GraphSearchItem[] }>(`/v1/kg-spaces/${standardId}/search`, {
    params: { q, limit },
  })
  return response.data.items
}

export async function loadKgSubgraph(standardId: string, params?: { node_id?: string; depth?: number }) {
  const response = await api.get<{ nodes: GraphNodeData[]; edges: GraphEdgeData[] }>(`/v1/kg-spaces/${standardId}/subgraph`, {
    params,
  })
  return response.data
}

export async function updateKgNode(standardId: string, nodeId: string, payload: Record<string, unknown>) {
  const response = await api.patch<GraphNodeData>(`/v1/kg-spaces/${standardId}/nodes/${nodeId}`, payload)
  return response.data
}

export async function updateKgEdge(standardId: string, edgeId: string, payload: Record<string, unknown>) {
  const response = await api.patch<GraphEdgeData>(`/v1/kg-spaces/${standardId}/edges/${edgeId}`, payload)
  return response.data
}

export async function fetchGraphServiceStatus(standardId?: string) {
  const response = await api.get<GraphServiceStatus>('/health', {
    params: standardId ? { standard_id: standardId } : undefined,
  })
  return response.data
}

export async function loadWorkbenchGraph(params: {
  standardId: string
  label?: string | null
  nodeId?: string | null
  preferredNodeTypes?: string[]
  maxDepth?: number
  maxNodes?: number
}) {
  const response = await api.get<GraphWorkbenchData>('/graphs', {
    params: {
      standard_id: params.standardId,
      label: params.label || undefined,
      node_id: params.nodeId || undefined,
      preferred_node_types: params.preferredNodeTypes?.length ? params.preferredNodeTypes.join(',') : undefined,
      max_depth: params.maxDepth,
      max_nodes: params.maxNodes,
    },
  })
  return response.data
}

export async function fetchPopularGraphLabels(standardId: string, limit = 120) {
  const response = await api.get<{ items: GraphLabelItem[] }>('/graph/label/popular', {
    params: { standard_id: standardId, limit },
  })
  return response.data.items
}

export async function searchGraphLabels(standardId: string, query: string, limit = 50) {
  if (!query.trim()) {
    return [] as GraphLabelItem[]
  }
  const response = await api.get<{ items: GraphLabelItem[] }>('/graph/label/search', {
    params: { standard_id: standardId, q: query.trim(), limit },
  })
  return response.data.items
}

export async function checkGraphEntityExists(standardId: string, name: string, excludeNodeId?: string) {
  const response = await api.get<GraphEntityExistsResponse>('/graph/entity/exists', {
    params: {
      standard_id: standardId,
      name,
      exclude_node_id: excludeNodeId,
    },
  })
  return response.data
}

export async function editGraphEntity(payload: GraphEntityEditRequest) {
  const response = await api.post<GraphEntityEditResponse>('/graph/entity/edit', payload)
  return response.data
}

export async function editGraphRelation(payload: GraphRelationEditRequest) {
  const response = await api.post<GraphRelationEditResponse>('/graph/relation/edit', payload)
  return response.data
}

export async function fetchOpenApiSpec() {
  const response = await api.get('/openapi.json')
  return response.data
}
