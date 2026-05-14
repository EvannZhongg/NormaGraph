import EdgeCurveProgram from '@sigma/edge-curve'
import { ChevronDown, ChevronUp, FileText, FileUp, LoaderCircle, Network } from 'lucide-react'
import Sigma from 'sigma'
import { useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'

import { HtmlContent } from '../components/HtmlContent'
import { MathText } from '../components/MathText'
import { StatusBadge } from '../components/StatusBadge'
import {
  getReportComparison,
  getReportSpace,
  loadKgSubgraph,
  listDocumentJobs,
  listDocuments,
  listKgSpaces,
  startReportComparison,
  uploadDocument,
  type DocumentSummary,
  type GraphEdgeData,
  type GraphNodeData,
  type GraphWorkbenchData,
  type IngestionJob,
  type KgSpaceSummary,
  type ReportClauseSummary,
  type ReportComparisonDetail,
  type ReportComparisonItem,
  type ReportSectionSummary,
  type ReportSpaceDetail,
  type ReportUnitSummary,
} from '../lib/api'
import { createRuntimeGraph, layoutGraph, type RuntimeGraph } from '../lib/graph-workbench'

type ReportContentView = 'blocks' | 'comparison-graph'
type ComparisonStatus = ReportComparisonItem['status']
type ClauseFinalStatus = ReportClauseSummary['finalStatus']
type ComparisonGraphStatusView = 'all' | ClauseFinalStatus
type ComparisonGraphFrequencyRangeId = 'all' | 'hit' | '1' | '2-5' | '6-20' | '21-50' | '51+'

interface FullKgGraphPayload {
  standardId: string
  nodes: GraphNodeData[]
  edges: GraphEdgeData[]
}

interface ReportUnitMention {
  unitUid: string
  label: string
  pageSpan: number[]
  status: ComparisonStatus
  reason: string
  evidence?: string | null
  summary?: string | null
}

export function ReportPage() {
  const [file, setFile] = useState<File | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [loadingReports, setLoadingReports] = useState(true)
  const [reports, setReports] = useState<DocumentSummary[]>([])
  const [jobs, setJobs] = useState<IngestionJob[]>([])
  const [kgSpaces, setKgSpaces] = useState<KgSpaceSummary[]>([])
  const [selectedReportId, setSelectedReportId] = useState<string | null>(null)
  const [selectedKgSpaceId, setSelectedKgSpaceId] = useState<string | null>(null)
  const [reportDetail, setReportDetail] = useState<ReportSpaceDetail | null>(null)
  const [lastLoadedReportDetail, setLastLoadedReportDetail] = useState<ReportSpaceDetail | null>(null)
  const [selectedUnitId, setSelectedUnitId] = useState<string | null>(null)
  const [loadingComparison, setLoadingComparison] = useState(false)
  const [loadingFullKgGraph, setLoadingFullKgGraph] = useState(false)
  const [startingComparison, setStartingComparison] = useState(false)
  const [comparisonDetail, setComparisonDetail] = useState<ReportComparisonDetail | null>(null)
  const [selectedGraphNodeId, setSelectedGraphNodeId] = useState<string | null>(null)
  const [contentView, setContentView] = useState<ReportContentView>('blocks')
  const [fullKgGraph, setFullKgGraph] = useState<FullKgGraphPayload | null>(null)
  const [comparisonGraphStatusView, setComparisonGraphStatusView] = useState<ComparisonGraphStatusView>('all')
  const [comparisonGraphFrequencyRange, setComparisonGraphFrequencyRange] = useState<ComparisonGraphFrequencyRangeId>('all')

  const unitGraphContainerRef = useRef<HTMLDivElement | null>(null)
  const comparisonGraphContainerRef = useRef<HTMLDivElement | null>(null)
  const sigmaContainerRef = useRef<HTMLDivElement | null>(null)
  const sigmaRef = useRef<Sigma | null>(null)
  const runtimeGraphRef = useRef<RuntimeGraph | null>(null)
  const unitButtonRefs = useRef(new Map<string, HTMLButtonElement>())

  const selectedReport = useMemo(
    () => reports.find((item) => item.documentId === selectedReportId) ?? null,
    [reports, selectedReportId],
  )
  const selectedKgSpace = useMemo(
    () => kgSpaces.find((item) => item.standardId === selectedKgSpaceId) ?? null,
    [kgSpaces, selectedKgSpaceId],
  )
  const effectiveReportDetail = useMemo(() => {
    if (reportDetail?.reportUnits.length) {
      return reportDetail
    }
    if (lastLoadedReportDetail?.documentId === selectedReportId && lastLoadedReportDetail.reportUnits.length) {
      return lastLoadedReportDetail
    }
    return reportDetail
  }, [lastLoadedReportDetail, reportDetail, selectedReportId])
  const sectionsById = useMemo(() => {
    const next = new Map<string, ReportSectionSummary>()
    effectiveReportDetail?.sections.forEach((item) => next.set(item.sectionUid, item))
    return next
  }, [effectiveReportDetail])

  const orderedUnits = useMemo(
    () => [...(effectiveReportDetail?.reportUnits ?? [])]
      .sort((left, right) => left.orderIndex - right.orderIndex),
    [effectiveReportDetail],
  )

  const activeJobs = useMemo(
    () => jobs.filter((item) => item.status === 'queued' || item.status === 'running'),
    [jobs],
  )
  const latestReportJob = useMemo(
    () => jobs.find((item) => item.documentType === 'report') ?? null,
    [jobs],
  )
  const shouldPollWorkspace = useMemo(
    () => Boolean(selectedReportId) && (activeJobs.length > 0 || (latestReportJob?.status === 'succeeded' && reportDetail === null)),
    [activeJobs.length, latestReportJob?.status, reportDetail, selectedReportId],
  )

  const selectedUnitComparison = useMemo(
    () => comparisonDetail?.unitResults.find((item) => item.reportUnitId === selectedUnitId) ?? null,
    [comparisonDetail, selectedUnitId],
  )
  const selectedReportUnit = useMemo(
    () => orderedUnits.find((item) => item.unitUid === selectedUnitId) ?? null,
    [orderedUnits, selectedUnitId],
  )
  const comparisonSummary = useMemo(() => summarizeClauseSummaries(comparisonDetail?.clauseSummaries ?? []), [comparisonDetail?.clauseSummaries])
  const violatedUnitIds = useMemo(() => {
    if (!comparisonDetail) {
      return []
    }
    const violatedUnitIdSet = new Set(
      comparisonDetail.unitResults
        .filter((item) => item.items.some((comparisonItem) => comparisonItem.status === 'violated'))
        .map((item) => item.reportUnitId),
    )
    return orderedUnits.filter((item) => violatedUnitIdSet.has(item.unitUid)).map((item) => item.unitUid)
  }, [comparisonDetail, orderedUnits])
  const evaluationInProgress = comparisonDetail?.status === 'queued' || comparisonDetail?.status === 'running'
  const evaluationStageLabel = useMemo(() => {
    if (!comparisonDetail) {
      return '-'
    }
    const stage = comparisonDetail.processingStage
    if (stage === 'routing') {
      return 'Routing'
    }
    if (stage === 'assessment') {
      return 'Assessing'
    }
    if (stage === 'finalizing') {
      return 'Finalizing'
    }
    if (stage === 'completed') {
      return 'Completed'
    }
    if (stage === 'failed') {
      return 'Failed'
    }
    if (stage === 'queued') {
      return 'Queued'
    }
    return evaluationInProgress ? 'Processing' : '-'
  }, [comparisonDetail, evaluationInProgress])
  const comparisonGraphKey = useMemo(() => buildComparisonGraphKey(comparisonDetail), [comparisonDetail?.clauseSummaries, comparisonDetail?.unitResults])
  const comparisonGraphData = useMemo(() => {
    if (!selectedKgSpaceId || fullKgGraph?.standardId !== selectedKgSpaceId) {
      return null
    }
    return buildFullReportComparisonGraph(selectedKgSpaceId, fullKgGraph.nodes, fullKgGraph.edges, comparisonDetail, orderedUnits)
  }, [comparisonGraphKey, fullKgGraph, orderedUnits, selectedKgSpaceId])
  const comparisonGraphRangeCounts = useMemo(
    () => summarizeComparisonGraphFrequencyRanges(comparisonGraphData, comparisonGraphStatusView),
    [comparisonGraphData, comparisonGraphStatusView],
  )
  const filteredComparisonGraphData = useMemo(
    () => filterComparisonGraph(comparisonGraphData, comparisonGraphFrequencyRange, comparisonGraphStatusView),
    [comparisonGraphData, comparisonGraphFrequencyRange, comparisonGraphStatusView],
  )
  const comparisonGraphMeta = useMemo(() => summarizeComparisonGraph(filteredComparisonGraphData), [filteredComparisonGraphData])
  const activeGraphData = useMemo(
    () => (contentView === 'comparison-graph' ? filteredComparisonGraphData : hideReportUnitNodes(selectedUnitComparison?.graph ?? null)),
    [contentView, filteredComparisonGraphData, selectedUnitComparison?.graph],
  )
  const selectedGraphNode = useMemo(() => {
    const nodes = activeGraphData?.nodes ?? []
    return nodes.find((item) => item.id === selectedGraphNodeId) ?? null
  }, [activeGraphData?.nodes, selectedGraphNodeId])

  useEffect(() => {
    void initializePage()
  }, [])

  useEffect(() => {
    if (!selectedReportId) {
      setReportDetail(null)
      setLastLoadedReportDetail(null)
      setJobs([])
      setSelectedUnitId(null)
      setComparisonDetail(null)
      return
    }
    setLastLoadedReportDetail(null)
    setComparisonDetail(null)
    setSelectedUnitId(null)
    void loadReportWorkspace(selectedReportId)
  }, [selectedReportId])

  useEffect(() => {
    if (!shouldPollWorkspace || !selectedReportId) {
      return
    }
    const timer = window.setInterval(() => {
      void loadReportWorkspace(selectedReportId, false)
    }, 5000)
    return () => window.clearInterval(timer)
  }, [selectedReportId, shouldPollWorkspace])

  useEffect(() => {
    setComparisonDetail(null)
    setFullKgGraph(null)
    setSelectedGraphNodeId(null)
  }, [selectedKgSpaceId])

  useEffect(() => {
    if (!selectedReportId || !selectedKgSpaceId || !reportDetail) {
      setComparisonDetail(null)
      return
    }
    void loadReportComparison(selectedReportId, selectedKgSpaceId, false)
  }, [reportDetail, selectedKgSpaceId, selectedReportId])

  useEffect(() => {
    if (!selectedReportId || !selectedKgSpaceId || !evaluationInProgress) {
      return
    }
    const timer = window.setInterval(() => {
      void loadReportComparison(selectedReportId, selectedKgSpaceId, false)
    }, 4000)
    return () => window.clearInterval(timer)
  }, [evaluationInProgress, selectedKgSpaceId, selectedReportId])

  useEffect(() => {
    if (contentView !== 'comparison-graph' || !selectedKgSpaceId) {
      return
    }
    if (fullKgGraph?.standardId === selectedKgSpaceId) {
      return
    }

    let cancelled = false
    setLoadingFullKgGraph(true)
    loadKgSubgraph(selectedKgSpaceId)
      .then((payload) => {
        if (!cancelled) {
          setFullKgGraph({ standardId: selectedKgSpaceId, nodes: payload.nodes, edges: payload.edges })
        }
      })
      .catch((error) => {
        if (!cancelled) {
          toast.error(extractErrorMessage(error, '全量 KG 图谱加载失败'))
          setFullKgGraph(null)
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingFullKgGraph(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [contentView, fullKgGraph?.standardId, selectedKgSpaceId])

  useEffect(() => {
    setSelectedGraphNodeId(null)
  }, [contentView])

  useEffect(() => {
    const graphData = activeGraphData
    const container = contentView === 'comparison-graph'
      ? comparisonGraphContainerRef.current
      : unitGraphContainerRef.current
    if (!container) {
      return
    }

    if (sigmaRef.current && sigmaContainerRef.current !== container) {
      sigmaRef.current.kill()
      sigmaRef.current = null
      runtimeGraphRef.current = null
      sigmaContainerRef.current = null
      container.innerHTML = ''
    }
    sigmaContainerRef.current = container

    if (!graphData || graphData.nodes.length === 0) {
      sigmaRef.current?.kill()
      sigmaRef.current = null
      runtimeGraphRef.current = null
      sigmaContainerRef.current = null
      container.innerHTML = ''
      return
    }

    const runtime = createRuntimeGraph(graphData, { rootNodeId: graphData.rootNodeId ?? null })
    const targetPositions = layoutGraph(runtime, 'force-atlas')
    Object.entries(targetPositions).forEach(([nodeId, position]) => {
      runtime.mergeNodeAttributes(nodeId, position)
    })
    applyReportGraphStyling(runtime, graphData, contentView === 'comparison-graph' ? comparisonGraphStatusView : 'all')
    runtimeGraphRef.current = runtime

    if (!sigmaRef.current) {
      sigmaRef.current = new Sigma(runtime, container, createReportSigmaSettings())
      sigmaRef.current.on('clickNode', ({ node }) => setSelectedGraphNodeId(node))
      sigmaRef.current.on('clickStage', () => setSelectedGraphNodeId(null))
    } else {
      sigmaRef.current.setGraph(runtime)
      sigmaRef.current.setSettings(createReportSigmaSettings())
      sigmaRef.current.refresh()
    }

    const defaultNodeId =
      contentView === 'comparison-graph'
        ? null
        : graphData.nodes.find((item) => Number(item.properties?.comparison_frequency ?? 0) > 0)?.id
          ?? graphData.nodes.find((item) => item.id !== graphData.rootNodeId)?.id
          ?? graphData.rootNodeId
          ?? graphData.nodes[0]?.id
          ?? null
    setSelectedGraphNodeId(defaultNodeId)
    return () => {
      sigmaRef.current?.refresh()
    }
  }, [activeGraphData, comparisonGraphStatusView, contentView])

  useEffect(() => {
    return () => {
      sigmaRef.current?.kill()
      sigmaRef.current = null
      sigmaContainerRef.current = null
    }
  }, [])

  async function initializePage() {
    setLoadingReports(true)
    try {
      const [documentItems, kgItems] = await Promise.all([listDocuments(), listKgSpaces()])
      const reportItems = documentItems.filter((item) => item.documentType === 'report')
      setReports(reportItems)
      setKgSpaces(kgItems)
      setSelectedReportId((current) => current ?? reportItems[0]?.documentId ?? null)
      setSelectedKgSpaceId((current) => current ?? kgItems[0]?.standardId ?? null)
    } catch (error) {
      toast.error(extractErrorMessage(error, '页面初始化失败'))
    } finally {
      setLoadingReports(false)
    }
  }

  async function loadReportWorkspace(documentId: string, showSpinner = true) {
    if (showSpinner) {
      setLoadingReports(true)
    }
    try {
      const [documentItems, jobItems] = await Promise.all([listDocuments(), listDocumentJobs(documentId)])
      const reportItems = documentItems.filter((item) => item.documentType === 'report')
      setReports(reportItems)
      setJobs(jobItems)
      const latestJob = jobItems.find((item) => item.documentType === 'report') ?? null
      if (!latestJob || latestJob.status === 'queued' || latestJob.status === 'running') {
        setReportDetail(null)
        setSelectedUnitId(null)
        return
      }
      if (latestJob.status === 'failed') {
        setReportDetail(null)
        setLastLoadedReportDetail(null)
        setSelectedUnitId(null)
        if (showSpinner) {
          toast.error(latestJob.error || '报告处理失败')
        }
        return
      }

      try {
        const detail = await getReportSpace(documentId)
        setReportDetail(detail)
        if (detail.reportUnits.length) {
          setLastLoadedReportDetail(detail)
        }
        setSelectedUnitId((current) => current ?? detail.reportUnits[0]?.unitUid ?? null)
      } catch (error) {
        if (isNotFoundError(error)) {
          setReportDetail(null)
          setLastLoadedReportDetail(null)
          setSelectedUnitId(null)
          return
        }
        throw error
      }
    } catch (error) {
      setReportDetail(null)
      if (showSpinner) {
        toast.error(extractErrorMessage(error, '报告空间加载失败'))
      }
    } finally {
      if (showSpinner) {
        setLoadingReports(false)
      }
    }
  }

  async function handleUpload() {
    if (!file) {
      toast.error('请选择报告文件')
      return
    }

    const formData = new FormData()
    formData.append('file', file)
    formData.append('document_type', 'report')
    formData.append('build_graph', 'true')

    setSubmitting(true)
    try {
      const job = await uploadDocument(formData)
      setFile(null)
      setSelectedReportId(job.documentId)
      await initializePage()
      await loadReportWorkspace(job.documentId, false)
      toast.success(`已提交 ${job.jobId}`)
    } catch (error) {
      toast.error(extractErrorMessage(error, '报告上传失败'))
    } finally {
      setSubmitting(false)
    }
  }

  async function loadReportComparison(documentId: string, standardId: string, showSpinner = true) {
    if (showSpinner) {
      setLoadingComparison(true)
    }
    try {
      const detail = await getReportComparison(documentId, standardId)
      setComparisonDetail(detail)
    } catch (error) {
      if (isNotFoundError(error)) {
        setComparisonDetail(null)
        return
      }
      if (showSpinner) {
        toast.error(extractErrorMessage(error, '评估结果加载失败'))
      }
    } finally {
      if (showSpinner) {
        setLoadingComparison(false)
      }
    }
  }

  async function handleStartComparison() {
    if (!selectedReportId || !selectedKgSpaceId) {
      toast.error('请选择报告和 KG space')
      return
    }
    setStartingComparison(true)
    try {
      const detail = await startReportComparison(selectedReportId, selectedKgSpaceId)
      setComparisonDetail(detail)
      toast.success('已启动评估')
    } catch (error) {
      toast.error(extractErrorMessage(error, '报告评估启动失败'))
    } finally {
      setStartingComparison(false)
    }
  }

  function handleSelectUnit(unit: ReportUnitSummary) {
    setSelectedUnitId(unit.unitUid)
    if (!comparisonDetail) {
      toast.error('请先启动整份报告评估')
      return
    }
    const result = comparisonDetail.unitResults.find((item) => item.reportUnitId === unit.unitUid)
    if (!result) {
      if (evaluationInProgress) {
        toast.error('该单元评估尚未完成')
      } else {
        toast.error('该单元暂无评估结果')
      }
    }
  }

  function scrollToReportUnit(unitId: string) {
    setSelectedUnitId(unitId)
    window.requestAnimationFrame(() => {
      unitButtonRefs.current.get(unitId)?.scrollIntoView({
        behavior: 'smooth',
        block: 'center',
      })
    })
  }

  function handleJumpToViolated(direction: 'previous' | 'next') {
    if (!violatedUnitIds.length) {
      toast.error('当前没有 violated 单元')
      return
    }
    const currentIndex = selectedUnitId ? violatedUnitIds.indexOf(selectedUnitId) : -1
    const targetIndex =
      direction === 'next'
        ? currentIndex === -1
          ? 0
          : (currentIndex + 1) % violatedUnitIds.length
        : currentIndex === -1
          ? violatedUnitIds.length - 1
          : (currentIndex - 1 + violatedUnitIds.length) % violatedUnitIds.length
    scrollToReportUnit(violatedUnitIds[targetIndex])
  }

  return (
    <div className={`grid h-[calc(100vh-110px)] gap-5 ${contentView === 'comparison-graph' ? 'xl:grid-cols-[320px_minmax(0,1fr)]' : 'xl:grid-cols-[320px_minmax(0,1fr)_420px]'}`}>
      <aside className="grid min-h-0 gap-5 xl:grid-rows-[auto,1fr]">
        <section className="panel-surface p-5">
          <div className="grid gap-4">
            <input
              type="file"
              accept=".pdf,.doc,.docx"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              className="control-input"
            />
            <button
              type="button"
              onClick={() => void handleUpload()}
              disabled={submitting}
              className="surface-button primary-dark-button compact-button disabled:opacity-60"
            >
              <FileUp className="h-4 w-4" />
              {submitting ? 'Uploading...' : 'Upload'}
            </button>
            {file ? <div className="text-xs text-[var(--text-secondary)]">{file.name}</div> : null}
          </div>
        </section>

        <section className="panel-surface min-h-0 overflow-hidden">
          <div className="grid h-full min-h-0 grid-rows-[auto,auto,auto,1fr,auto] gap-4 p-5">
            <div className="grid gap-2">
              <select
                value={selectedReportId ?? ''}
                onChange={(event) => setSelectedReportId(event.target.value || null)}
                className="control-select"
              >
                <option value="">Select report</option>
                {reports.map((item) => (
                  <option key={item.documentId} value={item.documentId}>
                    {item.displayName}
                  </option>
                ))}
              </select>
              <select
                value={selectedKgSpaceId ?? ''}
                onChange={(event) => setSelectedKgSpaceId(event.target.value || null)}
                className="control-select"
              >
                <option value="">Select KG space</option>
                {kgSpaces.map((item) => (
                  <option key={item.standardId} value={item.standardId}>
                    {item.title}
                  </option>
                ))}
              </select>
            </div>

            <div className="grid gap-2 text-sm">
              <div className="flex items-center justify-between">
                <span className="text-[var(--text-secondary)]">Report</span>
                <StatusBadge status={selectedReport?.status} />
              </div>
              <div className="flex items-center justify-between">
                <span className="text-[var(--text-secondary)]">KG</span>
                <span className="text-xs text-[var(--text-primary)]">{selectedKgSpace?.code ?? '-'}</span>
              </div>
            </div>

            <div className="subtle-surface grid gap-3 px-4 py-4 text-sm">
              <button
                type="button"
                onClick={() => void handleStartComparison()}
                disabled={startingComparison || !selectedReportId || !selectedKgSpaceId}
                className="surface-button primary-dark-button compact-button disabled:opacity-60"
              >
                {startingComparison ? 'Starting...' : 'Run Evaluation'}
              </button>
              <div className="flex items-center justify-between text-xs">
                <span className="text-[var(--text-secondary)]">{evaluationStageLabel}</span>
                <StatusBadge status={comparisonDetail?.status ?? 'idle'} />
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-white/70">
                <div
                  className="h-full rounded-full bg-[var(--brand)] transition-all"
                  style={{ width: `${Math.max(comparisonDetail ? 4 : 0, Math.round((comparisonDetail?.progress ?? 0) * 100))}%` }}
                />
              </div>
              <div className="flex items-center justify-between text-xs text-[var(--text-secondary)]">
                <span>
                  {comparisonDetail ? `${comparisonDetail.completedUnits} / ${comparisonDetail.totalUnits}` : '0 / 0'}
                </span>
                <span>{comparisonDetail ? `${Math.max(1, Math.round(comparisonDetail.progress * 100))}%` : '-'}</span>
              </div>
              {comparisonDetail?.error ? (
                <div className="text-xs leading-5 text-[var(--danger, #b42318)]">
                  {comparisonDetail.error}
                </div>
              ) : null}
            </div>

            <div className="grid min-h-0 gap-3 overflow-auto">
              {jobs.map((job) => (
                <div key={job.jobId} className="subtle-surface grid gap-2 px-3 py-3 text-sm">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-[11px] text-[var(--text-secondary)]">{job.jobId.slice(0, 8)}</span>
                    <StatusBadge status={job.status} />
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-white/70">
                    <div className="h-full rounded-full bg-[var(--brand)]" style={{ width: `${Math.max(4, job.progress * 100)}%` }} />
                  </div>
                  <div className="text-xs text-[var(--text-secondary)]">{job.updatedAt ? new Date(job.updatedAt).toLocaleString() : '-'}</div>
                  {job.error ? (
                    <div className="text-xs leading-5 text-[var(--danger, #b42318)]">
                      {job.error}
                    </div>
                  ) : null}
                </div>
              ))}
              {!jobs.length ? <div className="text-sm text-[var(--text-secondary)]">No job</div> : null}
            </div>

            <div className="subtle-surface grid gap-2 px-4 py-4 text-sm">
              <div className="flex items-center justify-between">
                <span className="text-[var(--text-secondary)]">Coverage</span>
                <span className="font-semibold text-[var(--text-primary)]">
                  {comparisonDetail ? `${Math.round(comparisonDetail.coverageScore * 100)}%` : '-'}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-2 text-xs text-[var(--text-secondary)]">
                <span>规则实体 {comparisonSummary.total}</span>
                <span>Covered {comparisonSummary.covered}</span>
                <span>Missing {comparisonSummary.missing}</span>
                <div className="flex items-center justify-between gap-2">
                  <span>Violated {comparisonSummary.violated}</span>
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => handleJumpToViolated('previous')}
                      disabled={!violatedUnitIds.length}
                      className="rounded border border-[var(--line)] p-1 text-[var(--text-primary)] transition hover:bg-[var(--brand-soft)] disabled:cursor-not-allowed disabled:opacity-40"
                      aria-label="Jump to previous violated unit"
                      title="Jump to previous violated unit"
                    >
                      <ChevronUp className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => handleJumpToViolated('next')}
                      disabled={!violatedUnitIds.length}
                      className="rounded border border-[var(--line)] p-1 text-[var(--text-primary)] transition hover:bg-[var(--brand-soft)] disabled:cursor-not-allowed disabled:opacity-40"
                      aria-label="Jump to next violated unit"
                      title="Jump to next violated unit"
                    >
                      <ChevronDown className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>
      </aside>

      <section className="panel-surface min-h-0 overflow-hidden">
        <div className="grid h-full min-h-0 grid-rows-[auto,1fr]">
          <div className="flex items-center justify-between border-b px-5 py-4" style={{ borderColor: 'var(--line)' }}>
            <div className="text-sm text-[var(--text-secondary)]">
              {selectedReportId ?? 'No report selected'}
            </div>
            <div className="flex items-center gap-3">
              <div className="flex rounded border border-[var(--line)] bg-white/60 p-0.5">
                <button
                  type="button"
                  onClick={() => setContentView('blocks')}
                  className={`surface-button compact-button border-0 px-3 py-1.5 ${contentView === 'blocks' ? 'primary-button' : ''}`}
                  aria-pressed={contentView === 'blocks'}
                >
                  <FileText className="h-4 w-4" />
                  文本分块
                </button>
                <button
                  type="button"
                  onClick={() => setContentView('comparison-graph')}
                  className={`surface-button compact-button border-0 px-3 py-1.5 ${contentView === 'comparison-graph' ? 'primary-button' : ''}`}
                  aria-pressed={contentView === 'comparison-graph'}
                >
                  <Network className="h-4 w-4" />
                  对比图谱
                </button>
              </div>
            {loadingReports || loadingComparison || evaluationInProgress || loadingFullKgGraph ? (
              <div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                <LoaderCircle className="h-4 w-4 animate-spin" />
                  {loadingReports ? 'Loading...' : loadingComparison ? 'Loading comparison...' : loadingFullKgGraph ? 'Loading full KG...' : 'Evaluating...'}
              </div>
            ) : null}
            </div>
          </div>
          {contentView === 'comparison-graph' ? (
            <div key="comparison-graph-pane" className="relative min-h-0 overflow-hidden">
              <div ref={comparisonGraphContainerRef} className="h-full w-full" />
              <div className="absolute left-4 top-4 z-10 grid max-w-[360px] gap-3">
                <div className="subtle-surface grid gap-2 px-4 py-3 text-xs shadow-sm">
                  <div className="flex items-center justify-between gap-4">
                    <span className="font-semibold text-[var(--text-primary)]">全量 KG 对比图谱</span>
                    <span className="text-[var(--text-secondary)]">{comparisonGraphMeta.kgNodes} / {comparisonGraphMeta.kgEdges}</span>
                  </div>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[var(--text-secondary)]">
                    <span>规则实体 {comparisonGraphMeta.ruleEntities}</span>
                    <span>命中实体 {comparisonGraphMeta.entityHits}</span>
                    <span>涉及文本块 {comparisonGraphMeta.reportUnits}</span>
                    <span>最高频次 {comparisonGraphMeta.maxFrequency}</span>
                  </div>
                </div>
                <div className="subtle-surface grid gap-2 px-4 py-3 text-xs shadow-sm">
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-semibold text-[var(--text-primary)]">渲染视角</span>
                    <span className="text-[var(--text-secondary)]">{comparisonStatusViewLabel(comparisonGraphStatusView)}</span>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {COMPARISON_GRAPH_STATUS_VIEWS.map((view) => (
                      <button
                        key={view}
                        type="button"
                        onClick={() => {
                          setComparisonGraphStatusView(view)
                          setSelectedGraphNodeId(null)
                        }}
                        className={`surface-button compact-button border-0 px-2.5 py-1 ${comparisonGraphStatusView === view ? 'primary-button' : ''}`}
                        aria-pressed={comparisonGraphStatusView === view}
                      >
                        {comparisonStatusViewLabel(view)}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="subtle-surface grid gap-2 px-4 py-3 text-xs shadow-sm">
                  <div className="font-semibold text-[var(--text-primary)]">频次区间</div>
                  <div className="flex flex-wrap gap-1.5">
                    {COMPARISON_GRAPH_FREQUENCY_RANGES.map((range) => (
                      <button
                        key={range.id}
                        type="button"
                        onClick={() => {
                          setComparisonGraphFrequencyRange(range.id)
                          setSelectedGraphNodeId(null)
                        }}
                        className={`surface-button compact-button min-w-[74px] justify-between gap-2 border-0 px-2.5 py-1 ${comparisonGraphFrequencyRange === range.id ? 'primary-button' : ''}`}
                        aria-pressed={comparisonGraphFrequencyRange === range.id}
                      >
                        <span>{range.label}</span>
                        <span className={`rounded-full px-1.5 py-0.5 text-[10px] leading-none ${comparisonGraphFrequencyRange === range.id ? 'bg-white/25 text-white' : 'bg-[var(--surface-muted)] text-[var(--text-secondary)]'}`}>
                          {comparisonGraphRangeCounts[range.id] ?? 0}
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
                <div className="subtle-surface grid gap-2 px-4 py-3 text-xs text-[var(--text-secondary)] shadow-sm">
                  {(['covered', 'violated', 'missing'] as ClauseFinalStatus[]).map((status) => (
                    <div key={status} className="flex items-center justify-between gap-3">
                      <span className="flex items-center gap-2">
                        <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: comparisonStatusColor(status, 1) }} />
                        {status}
                      </span>
                      <span>{comparisonGraphMeta.statusCounts[status] ?? 0}</span>
                    </div>
                  ))}
                </div>
              </div>
              {selectedGraphNode ? (
                <div className="absolute bottom-4 right-4 z-10 max-h-[54%] w-[min(520px,calc(100%-2rem))] overflow-auto rounded-lg border border-[var(--line)] bg-white/92 px-4 py-4 text-sm shadow-sm backdrop-blur">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="font-semibold text-[var(--text-primary)]">{selectedGraphNode.label}</div>
                      <div className="mt-1 text-xs text-[var(--text-secondary)]">{selectedGraphNode.nodeType}</div>
                    </div>
                    {selectedGraphNode.nodeType === 'clause' ? (
                      <span className="rounded border border-[var(--line)] px-2 py-1 text-xs text-[var(--text-secondary)]">
                        {String(selectedGraphNode.properties.final_status ?? 'missing')} · hits {String(selectedGraphNode.properties.hit_count ?? 0)}
                      </span>
                    ) : null}
                  </div>
                  <MathText
                    text={resolveNodeText(selectedGraphNode.properties)}
                    className="mt-3 whitespace-pre-wrap text-xs leading-5 text-[var(--text-secondary)]"
                  />
                  {selectedGraphNode.nodeType === 'clause' && selectedGraphNode.properties.final_status === 'missing' ? (
                    <div className="mt-4 border-t pt-3 text-xs leading-5 text-[var(--text-secondary)]" style={{ borderColor: 'var(--line)' }}>
                      整份报告未发现覆盖或违规证据。
                    </div>
                  ) : readReportUnitMentions(selectedGraphNode.properties).length ? (
                    <div className="mt-4 grid gap-2 border-t pt-3" style={{ borderColor: 'var(--line)' }}>
                      <div className="text-xs font-semibold uppercase tracking-[0.14em] text-[var(--text-dim)]">
                        涉及文本块
                      </div>
                      {readReportUnitMentions(selectedGraphNode.properties).map((unit) => (
                        <button
                          key={`${selectedGraphNode.id}:${unit.unitUid}:${unit.status}`}
                          type="button"
                          onClick={() => {
                            setContentView('blocks')
                            window.setTimeout(() => scrollToReportUnit(unit.unitUid), 0)
                          }}
                          className="subtle-surface grid gap-1 px-3 py-3 text-left text-xs transition hover:border-[var(--brand)]"
                        >
                          <div className="flex items-center justify-between gap-3">
                            <span className="font-medium text-[var(--text-primary)]">{unit.label}</span>
                            <span className="text-[var(--text-secondary)]">{unit.status}</span>
                          </div>
                          <div className="text-[var(--text-secondary)]">Pages {unit.pageSpan.join('-') || '-'}</div>
                          {unit.reason ? <div className="leading-5 text-[var(--text-secondary)]">{unit.reason}</div> : null}
                          {unit.evidence ? <div className="leading-5 text-[var(--text-dim)]">{unit.evidence}</div> : null}
                        </button>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}
              {!activeGraphData?.nodes.length && !loadingFullKgGraph ? (
                <div className="absolute inset-0 grid place-items-center text-sm text-[var(--text-secondary)]">
                  请选择 KG space，并在需要时先运行整份报告评估。
                </div>
              ) : null}
            </div>
          ) : (
          <div key="report-blocks-pane" className="min-h-0 overflow-auto px-5 py-5">
            <div className="grid gap-4">
              {orderedUnits.map((unit, index) => {
                const section = unit.parentSectionUid ? sectionsById.get(unit.parentSectionUid) : null
                const previous = orderedUnits[index - 1]
                const showSectionTitle = !previous || previous.parentSectionUid !== unit.parentSectionUid
                return (
                  <div key={unit.unitUid} className="grid gap-2">
                    {showSectionTitle && section ? (
                      <div className="text-xs font-semibold uppercase tracking-[0.14em] text-[var(--text-dim)]">
                        {section.title}
                      </div>
                    ) : null}
                    <button
                      ref={(element) => {
                        if (element) {
                          unitButtonRefs.current.set(unit.unitUid, element)
                          return
                        }
                        unitButtonRefs.current.delete(unit.unitUid)
                      }}
                      type="button"
                      onClick={() => void handleSelectUnit(unit)}
                      className={`subtle-surface w-full px-4 py-4 text-left transition ${unit.unitUid === selectedUnitId ? 'border-[var(--brand)] bg-[var(--brand-soft)]' : ''}`}
                    >
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-xs text-[var(--text-secondary)]">{unit.pageSpan.join('-') || '-'}</span>
                        <span className="text-xs text-[var(--text-secondary)]">{unit.unitType}</span>
                      </div>
                      {unit.unitType === 'table' && unit.html ? (
                        <div className="mt-3 grid gap-3">
                          {unit.title ? (
                            <div className="text-sm font-medium leading-6 text-[var(--text-primary)]">{unit.title}</div>
                          ) : null}
                          <HtmlContent
                            html={unit.html}
                            className="report-unit-table overflow-x-auto rounded-xl border border-[var(--line)] bg-white/90 p-3 text-sm text-[var(--text-primary)]"
                          />
                        </div>
                      ) : (
                        <MathText
                          text={unit.textNormalized}
                          className="mt-3 whitespace-pre-wrap text-sm leading-7 text-[var(--text-primary)]"
                        />
                      )}
                    </button>
                  </div>
                )
              })}
              {!orderedUnits.length && !loadingReports ? <div className="text-sm text-[var(--text-secondary)]">No parsed unit</div> : null}
            </div>
          </div>
          )}
        </div>
      </section>

      {contentView === 'blocks' ? (
      <section className="panel-surface min-h-0 overflow-hidden">
        <div className="grid h-full min-h-0 grid-rows-[1fr,auto]">
            <div className="relative min-h-[320px]">
              <div ref={unitGraphContainerRef} className="h-full w-full" />
            {!selectedUnitComparison?.graph.nodes.length ? (
              <div className="absolute inset-0 grid place-items-center text-sm text-[var(--text-secondary)]">
                Select a report unit to view saved result.
              </div>
            ) : null}
          </div>
          <div className="border-t px-5 py-4" style={{ borderColor: 'var(--line)' }}>
            {selectedUnitComparison ? (
              <div className="grid gap-4">
                <div className="subtle-surface grid gap-2 px-4 py-4">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-xs font-semibold uppercase tracking-[0.14em] text-[var(--text-dim)]">Report Unit</span>
                    <span className="text-xs text-[var(--text-secondary)]">
                      {selectedReportUnit?.pageSpan.join('-') || '-'}
                    </span>
                  </div>
                  <div className="text-sm leading-6 text-[var(--text-primary)]">
                    {selectedUnitComparison.summary}
                  </div>
                </div>

                {selectedGraphNode && selectedGraphNode.nodeType !== 'report_unit' ? (
                  <div className="grid gap-2">
                    <div className="text-sm font-semibold text-[var(--text-primary)]">{selectedGraphNode.label}</div>
                    <div className="text-xs text-[var(--text-secondary)]">{selectedGraphNode.nodeType}</div>
                    <MathText
                      text={resolveNodeText(selectedGraphNode.properties)}
                      className="max-h-40 overflow-auto whitespace-pre-wrap text-xs leading-5 text-[var(--text-secondary)]"
                    />
                  </div>
                ) : (
                  <div className="grid gap-3">
                    {selectedUnitComparison.items.slice(0, 8).map((item) => (
                      <div key={item.clauseId} className="subtle-surface px-3 py-3 text-xs">
                        <div className="flex items-center justify-between gap-3">
                          <span className="font-medium text-[var(--text-primary)]">{item.clauseRef ?? item.label}</span>
                          <span className="text-[var(--text-secondary)]">{item.status}</span>
                        </div>
                        <div className="mt-2 leading-5 text-[var(--text-secondary)]">{item.reason}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ) : null}
          </div>
        </div>
      </section>
      ) : null}
    </div>
  )
}

const COMPARISON_STATUSES: ComparisonStatus[] = ['covered', 'violated']
const CLAUSE_FINAL_STATUSES: ClauseFinalStatus[] = ['covered', 'violated', 'missing']
const REPORT_COMPARISON_DISPLAY_NODE_TYPES = new Set(['standard', 'appendix', 'chapter', 'section', 'clause'])
const COMPARISON_GRAPH_STATUS_VIEWS: ComparisonGraphStatusView[] = ['all', 'covered', 'violated', 'missing']
const COMPARISON_GRAPH_FREQUENCY_RANGES: { id: ComparisonGraphFrequencyRangeId; label: string; min: number; max: number | null }[] = [
  { id: 'all', label: '全部', min: 0, max: null },
  { id: 'hit', label: '命中', min: 1, max: null },
  { id: '1', label: '1次', min: 1, max: 1 },
  { id: '2-5', label: '2-5次', min: 2, max: 5 },
  { id: '6-20', label: '6-20次', min: 6, max: 20 },
  { id: '21-50', label: '21-50次', min: 21, max: 50 },
  { id: '51+', label: '51+次', min: 51, max: null },
]

function buildComparisonGraphKey(comparisonDetail: ReportComparisonDetail | null) {
  if (!comparisonDetail) {
    return 'empty'
  }
  const clauseKey = comparisonDetail.clauseSummaries
    .map((summary) => `${summary.clauseId}:${summary.finalStatus}:${summary.hitCount}`)
    .sort()
    .join(',')
  const unitKey = comparisonDetail.unitResults
    .map((unit) => `${unit.reportUnitId}:${unit.items.map((item) => `${item.clauseId}:${item.status}`).sort().join(',')}`)
    .sort()
    .join('|')
  return `${comparisonDetail.status}:${clauseKey}:${unitKey}`
}

function buildFullReportComparisonGraph(
  standardId: string,
  kgNodes: GraphNodeData[],
  kgEdges: GraphEdgeData[],
  comparisonDetail: ReportComparisonDetail | null,
  reportUnits: ReportUnitSummary[],
): GraphWorkbenchData {
  const displayKgNodes = kgNodes.filter((node) => isReportComparisonDisplayNode(node))
  const kgNodeIds = new Set(displayKgNodes.map((node) => String(node.node_uid || '')).filter(Boolean))
  const reportUnitById = new Map(reportUnits.map((unit, index) => [unit.unitUid, { unit, index }]))
  const clauseSummaryById = new Map((comparisonDetail?.clauseSummaries ?? []).map((summary) => [summary.clauseId, summary]))
  const degreeMap = new Map<string, number>()
  const displayKgEdges = kgEdges.filter((edge) => {
    const source = String(edge.source_uid || '')
    const target = String(edge.target_uid || '')
    return kgNodeIds.has(source) && kgNodeIds.has(target)
  })
  displayKgEdges.forEach((edge) => {
    const source = String(edge.source_uid || '')
    const target = String(edge.target_uid || '')
    degreeMap.set(source, (degreeMap.get(source) ?? 0) + 1)
    degreeMap.set(target, (degreeMap.get(target) ?? 0) + 1)
  })

  const maxHitCount = Math.max(1, ...[...clauseSummaryById.values()].map((item) => item.hitCount))
  const nodes = displayKgNodes.map((node) => {
    const nodeId = String(node.node_uid || '')
    const clauseSummary = clauseSummaryById.get(nodeId)
    const reportUnitsForClause = clauseSummary
      ? clauseSummary.evidenceUnits.map((unit) => {
          const unitRecord = reportUnitById.get(unit.reportUnitId)
          return {
            unitUid: unit.reportUnitId,
            label: reportUnitMentionLabel(unitRecord?.unit, unitRecord?.index ?? 0),
            pageSpan: unitRecord?.unit.pageSpan ?? [],
            status: unit.status,
            reason: unit.reason,
            evidence: unit.reportEvidence ?? null,
            summary: unit.reason,
          }
        })
      : []
    return {
      id: nodeId,
      label: graphNodeLabel(node),
      nodeType: String(node.node_type || 'unknown'),
      degree: degreeMap.get(nodeId) ?? 0,
      properties: {
        ...(node.properties ?? {}),
        node_uid: nodeId,
        node_type: node.node_type,
        text_content: node.text_content,
        comparison_frequency: clauseSummary?.hitCount ?? 0,
        comparison_status: clauseSummary?.finalStatus ?? null,
        comparison_status_counts: clauseSummary ? statusCountsFromClauseSummary(clauseSummary) : createFinalStatusCountMap(),
        comparison_intensity: clauseSummary ? clauseSummary.hitCount / maxHitCount : 0,
        final_status: clauseSummary?.finalStatus,
        hit_count: clauseSummary?.hitCount ?? 0,
        covered_count: clauseSummary?.coveredCount ?? 0,
        violated_count: clauseSummary?.violatedCount ?? 0,
        report_units: reportUnitsForClause,
      },
    }
  })

  const edges = displayKgEdges.map((edge) => ({
      id: String(edge.edge_uid || `${edge.source_uid}:${edge.edge_type}:${edge.target_uid}`),
      source: String(edge.source_uid || ''),
      target: String(edge.target_uid || ''),
      edgeType: String(edge.edge_type || 'RELATED'),
      properties: (() => {
        return {
          ...(edge.properties ?? {}),
          edge_uid: edge.edge_uid,
          comparison_frequency: 0,
          comparison_status: null,
          comparison_status_counts: createFinalStatusCountMap(),
          comparison_intensity: 0,
          report_units: [],
        }
      })(),
    }))

  return {
    standardId,
    rootNodeId: kgNodeIds.has(standardId) ? standardId : nodes[0]?.id ?? null,
    maxDepth: 4,
    maxNodes: nodes.length,
    isTruncated: false,
    nodes,
    edges,
  }
}

function isReportComparisonDisplayNode(node: GraphNodeData) {
  return REPORT_COMPARISON_DISPLAY_NODE_TYPES.has(String(node.node_type || '').trim().toLowerCase())
}

function filterComparisonGraph(
  graph: GraphWorkbenchData | null,
  rangeId: ComparisonGraphFrequencyRangeId,
  statusView: ComparisonGraphStatusView,
): GraphWorkbenchData | null {
  if (!graph) {
    return graph
  }

  const range = COMPARISON_GRAPH_FREQUENCY_RANGES.find((item) => item.id === rangeId)
  if (!range) {
    return graph
  }

  const matchedClauseIds = new Set(
    graph.nodes
      .filter((node) => node.nodeType === 'clause' && comparisonNodeMatchesRange(node, range, statusView))
      .map((node) => node.id),
  )
  if (rangeId === 'all' && statusView === 'all') {
    graph.nodes.forEach((node) => {
      if (node.nodeType === 'clause') {
        matchedClauseIds.add(node.id)
      }
    })
  }

  const visibleNodeIds = includeGraphContextNodes(graph, matchedClauseIds, {
    sectionOnlyContext: statusView !== 'all',
  })
  const edges = graph.edges.filter((edge) => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target))
  return {
    ...graph,
    nodes: graph.nodes.filter((node) => visibleNodeIds.has(node.id)),
    edges,
    maxNodes: visibleNodeIds.size,
  }
}

function includeGraphContextNodes(
  graph: GraphWorkbenchData,
  matchedClauseIds: Set<string>,
  options?: { sectionOnlyContext?: boolean },
) {
  const visibleNodeIds = new Set<string>()
  if (graph.rootNodeId && !options?.sectionOnlyContext) {
    visibleNodeIds.add(graph.rootNodeId)
  }
  matchedClauseIds.forEach((nodeId) => visibleNodeIds.add(nodeId))

  if (options?.sectionOnlyContext) {
    const nodeById = new Map(graph.nodes.map((node) => [node.id, node]))
    graph.edges.forEach((edge) => {
      const source = nodeById.get(edge.source)
      const target = nodeById.get(edge.target)
      if (matchedClauseIds.has(edge.target) && source?.nodeType === 'section') {
        visibleNodeIds.add(edge.source)
      }
      if (matchedClauseIds.has(edge.source) && target?.nodeType === 'section') {
        visibleNodeIds.add(edge.target)
      }
    })
    return visibleNodeIds
  }

  let changed = true
  while (changed) {
    changed = false
    graph.edges.forEach((edge) => {
      if (!visibleNodeIds.has(edge.target)) {
        return
      }
      if (visibleNodeIds.has(edge.source)) {
        return
      }
      const sourceNode = graph.nodes.find((node) => node.id === edge.source)
      if (sourceNode?.nodeType === 'clause' && !matchedClauseIds.has(edge.source)) {
        return
      }
      visibleNodeIds.add(edge.source)
      changed = true
    })
  }
  return visibleNodeIds
}

function hideReportUnitNodes(graph: GraphWorkbenchData | null): GraphWorkbenchData | null {
  if (!graph) {
    return graph
  }
  const visibleNodeIds = new Set(
    graph.nodes
      .filter((node) => node.nodeType !== 'report_unit')
      .map((node) => node.id),
  )
  const nodes = graph.nodes.filter((node) => visibleNodeIds.has(node.id))
  const edges = graph.edges.filter((edge) => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target))
  return {
    ...graph,
    rootNodeId: graph.rootNodeId && visibleNodeIds.has(graph.rootNodeId)
      ? graph.rootNodeId
      : nodes[0]?.id ?? null,
    nodes,
    edges,
    maxNodes: nodes.length,
  }
}

function summarizeComparisonGraphFrequencyRanges(graph: GraphWorkbenchData | null, statusView: ComparisonGraphStatusView) {
  const counts = COMPARISON_GRAPH_FREQUENCY_RANGES.reduce(
    (accumulator, range) => {
      accumulator[range.id] = 0
      return accumulator
    },
    {} as Record<ComparisonGraphFrequencyRangeId, number>,
  )
  if (!graph) {
    return counts
  }

  graph.nodes
    .filter((node) => node.nodeType === 'clause')
    .forEach((node) => {
    const frequency = getComparisonGraphNodeFrequency(node, statusView)
    COMPARISON_GRAPH_FREQUENCY_RANGES.forEach((range) => {
      if (frequencyInRange(frequency, range)) {
        counts[range.id] += 1
      }
    })
  })
  return counts
}

function summarizeComparisonGraph(graph: GraphWorkbenchData | null) {
  const statusCounts = createFinalStatusCountMap()
  if (!graph) {
    return {
      kgNodes: 0,
      kgEdges: 0,
      reportUnits: 0,
      ruleEntities: 0,
      entityHits: 0,
      maxFrequency: 0,
      statusCounts,
    }
  }

  const reportUnitIds = new Set<string>()
  let maxFrequency = 0
  let entityHits = 0
  let ruleEntities = 0

  graph.nodes.forEach((node) => {
    if (node.nodeType !== 'clause') {
      return
    }
    ruleEntities += 1
    const frequency = Number(node.properties?.comparison_frequency ?? 0)
    if (frequency > 0) {
      entityHits += 1
      maxFrequency = Math.max(maxFrequency, frequency)
    }

    const status = normalizeFinalStatus(node.properties?.comparison_status)
    if (status) {
      statusCounts[status] += 1
    }
    readReportUnitMentions(node.properties).forEach((unit) => reportUnitIds.add(unit.unitUid))
  })

  return {
    kgNodes: graph.nodes.length,
    kgEdges: graph.edges.length,
    reportUnits: reportUnitIds.size,
    ruleEntities,
    entityHits,
    maxFrequency,
    statusCounts,
  }
}

function applyReportGraphStyling(runtime: RuntimeGraph, rawGraph: GraphWorkbenchData, _statusView: ComparisonGraphStatusView) {
  const maxNodeFrequency = Math.max(1, ...rawGraph.nodes.map((node) => Number(node.properties?.comparison_frequency ?? 0)))

  rawGraph.nodes.forEach((node) => {
    if (!runtime.hasNode(node.id)) {
      return
    }

    const frequency = Number(node.properties?.comparison_frequency ?? 0)
    const status = normalizeFinalStatus(node.properties?.comparison_status)
    if (!status || node.nodeType !== 'clause') {
      return
    }

    const intensity = status === 'missing' ? 0 : clampNumber(frequency / maxNodeFrequency, 0, 1)
    runtime.mergeNodeAttributes(node.id, {
      color: comparisonStatusColor(status, intensity),
      size: status === 'missing' ? 8 : 12 + intensity * 14,
      zIndex: status === 'violated' ? 12 : 7 + Math.round(intensity * 2),
      forceLabel: intensity > 0.55,
    })
  })
}

function comparisonNodeMatchesRange(
  node: GraphWorkbenchData['nodes'][number],
  range: { min: number; max: number | null },
  statusView: ComparisonGraphStatusView,
) {
  if (statusView !== 'all' && normalizeFinalStatus(node.properties?.comparison_status) !== statusView) {
    return false
  }
  return frequencyInRange(getComparisonGraphNodeFrequency(node, statusView), range)
}

function getComparisonGraphNodeFrequency(node: GraphWorkbenchData['nodes'][number], statusView: ComparisonGraphStatusView) {
  return getComparisonGraphFrequency(node.properties, statusView)
}

function getComparisonGraphFrequency(properties: Record<string, unknown>, statusView: ComparisonGraphStatusView) {
  const hitCount = Number(properties.comparison_frequency ?? 0)
  if (statusView === 'all') {
    return normalizeFinalStatus(properties.comparison_status) === 'missing' ? 0 : hitCount
  }
  if (statusView === 'missing') {
    return 0
  }
  return normalizeFinalStatus(properties.comparison_status) === statusView
    ? hitCount
    : 0
}

function frequencyInRange(frequency: number, range: { min: number; max: number | null }) {
  if (!Number.isFinite(frequency)) {
    return false
  }
  if (frequency < range.min) {
    return false
  }
  return range.max === null || frequency <= range.max
}

function comparisonStatusViewLabel(view: ComparisonGraphStatusView) {
  if (view === 'all') {
    return '全部'
  }
  return view
}

function reportUnitMentionLabel(unit: ReportUnitSummary | undefined, index: number) {
  if (!unit) {
    return `Report Unit ${index + 1}`
  }
  const sectionTitle = unit.sectionPath[unit.sectionPath.length - 1]
  return unit.title || sectionTitle || `Report Unit ${index + 1}`
}

function readReportUnitMentions(properties: Record<string, unknown>): ReportUnitMention[] {
  const rawUnits = properties.report_units
  if (!Array.isArray(rawUnits)) {
    return []
  }
  return rawUnits
    .map((item) => {
      if (!item || typeof item !== 'object') {
        return null
      }
      const record = item as Record<string, unknown>
      const status = normalizeComparisonStatus(record.status)
      const unitUid = typeof record.unitUid === 'string' ? record.unitUid : ''
      if (!unitUid || !status) {
        return null
      }
      return {
        unitUid,
        label: typeof record.label === 'string' && record.label.trim() ? record.label : unitUid,
        pageSpan: Array.isArray(record.pageSpan) ? record.pageSpan.filter((page): page is number => typeof page === 'number') : [],
        status,
        reason: typeof record.reason === 'string' ? record.reason : '',
        evidence: typeof record.evidence === 'string' ? record.evidence : null,
        summary: typeof record.summary === 'string' ? record.summary : null,
      }
    })
    .filter((item): item is ReportUnitMention => item !== null)
}

function normalizeComparisonStatus(value: unknown): ComparisonStatus | null {
  const normalized = String(value ?? '').trim().toLowerCase()
  return COMPARISON_STATUSES.find((status) => status === normalized) ?? null
}

function createFinalStatusCountMap(): Record<ClauseFinalStatus, number> {
  return {
    covered: 0,
    violated: 0,
    missing: 0,
  }
}

function normalizeFinalStatus(value: unknown): ClauseFinalStatus | null {
  const normalized = String(value ?? '').trim().toLowerCase()
  return CLAUSE_FINAL_STATUSES.find((status) => status === normalized) ?? null
}

function statusCountsFromClauseSummary(summary: ReportClauseSummary): Record<ClauseFinalStatus, number> {
  const counts = createFinalStatusCountMap()
  counts[summary.finalStatus] = 1
  return counts
}

function comparisonStatusColor(status: ClauseFinalStatus, intensity: number) {
  const strong = intensity >= 0.66
  const medium = intensity >= 0.33
  if (status === 'violated') {
    return strong ? '#b91c1c' : medium ? '#ef4444' : '#fca5a5'
  }
  if (status === 'covered') {
    return strong ? '#15803d' : medium ? '#22c55e' : '#86efac'
  }
  return strong ? '#475569' : medium ? '#94a3b8' : '#cbd5e1'
}

function graphNodeLabel(node: GraphNodeData) {
  const label = node.label ?? node.properties?.label ?? node.properties?.title ?? node.node_uid
  return String(label || 'unnamed')
}

function clampNumber(value: number, min: number, max: number) {
  if (!Number.isFinite(value)) {
    return min
  }
  return Math.max(min, Math.min(max, value))
}

function createReportSigmaSettings() {
  return {
    allowInvalidContainer: true,
    defaultEdgeType: 'curve',
    edgeProgramClasses: { curve: EdgeCurveProgram },
    renderEdgeLabels: false,
    labelDensity: 0.8,
    labelRenderedSizeThreshold: 6,
    labelFont: 'IBM Plex Sans',
    edgeLabelFont: 'IBM Plex Mono',
    zIndex: true,
  }
}

function summarizeClauseSummaries(items: ReportClauseSummary[]) {
  return items.reduce(
    (accumulator, item) => {
      accumulator.total += 1
      accumulator[item.finalStatus] += 1
      return accumulator
    },
    {
      total: 0,
      covered: 0,
      violated: 0,
      missing: 0,
    } as Record<ClauseFinalStatus, number> & { total: number },
  )
}

function resolveNodeText(properties: Record<string, unknown>) {
  const summary = properties.summary
  const textContent = properties.text_content
  if (typeof summary === 'string' && summary.trim()) {
    if (typeof textContent === 'string' && textContent.trim()) {
      return `${summary.trim()}\n\n${textContent.trim()}`
    }
    return summary.trim()
  }
  if (typeof textContent === 'string' && textContent.trim()) {
    return textContent.trim()
  }
  return JSON.stringify(properties, null, 2)
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

function isNotFoundError(error: unknown) {
  if (typeof error !== 'object' || !error || !('response' in error)) {
    return false
  }
  return (error as { response?: { status?: number } }).response?.status === 404
}
