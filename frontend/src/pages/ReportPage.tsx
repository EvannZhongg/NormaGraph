import EdgeCurveProgram from '@sigma/edge-curve'
import { FileUp, LoaderCircle } from 'lucide-react'
import Sigma from 'sigma'
import { useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'

import { StatusBadge } from '../components/StatusBadge'
import {
  getReportComparison,
  getReportSpace,
  listDocumentJobs,
  listDocuments,
  listKgSpaces,
  startReportComparison,
  uploadDocument,
  type DocumentSummary,
  type IngestionJob,
  type KgSpaceSummary,
  type ReportComparisonDetail,
  type ReportComparisonItem,
  type ReportSectionSummary,
  type ReportSpaceDetail,
  type ReportUnitSummary,
} from '../lib/api'
import { createRuntimeGraph, layoutGraph, type RuntimeGraph } from '../lib/graph-workbench'

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
  const [selectedUnitId, setSelectedUnitId] = useState<string | null>(null)
  const [loadingComparison, setLoadingComparison] = useState(false)
  const [startingComparison, setStartingComparison] = useState(false)
  const [comparisonDetail, setComparisonDetail] = useState<ReportComparisonDetail | null>(null)
  const [selectedGraphNodeId, setSelectedGraphNodeId] = useState<string | null>(null)

  const graphContainerRef = useRef<HTMLDivElement | null>(null)
  const sigmaRef = useRef<Sigma | null>(null)
  const runtimeGraphRef = useRef<RuntimeGraph | null>(null)

  const selectedReport = useMemo(
    () => reports.find((item) => item.documentId === selectedReportId) ?? null,
    [reports, selectedReportId],
  )
  const selectedKgSpace = useMemo(
    () => kgSpaces.find((item) => item.standardId === selectedKgSpaceId) ?? null,
    [kgSpaces, selectedKgSpaceId],
  )
  const sectionsById = useMemo(() => {
    const next = new Map<string, ReportSectionSummary>()
    reportDetail?.sections.forEach((item) => next.set(item.sectionUid, item))
    return next
  }, [reportDetail])

  const orderedUnits = useMemo(
    () => [...(reportDetail?.reportUnits ?? [])]
      .filter((item) => item.unitType === 'text')
      .sort((left, right) => left.orderIndex - right.orderIndex),
    [reportDetail],
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
  const comparisonSummary = useMemo(() => summarizeComparison(comparisonDetail?.items ?? []), [comparisonDetail?.items])
  const evaluationInProgress = comparisonDetail?.status === 'queued' || comparisonDetail?.status === 'running'
  const selectedGraphNode = useMemo(() => {
    const nodes = selectedUnitComparison?.graph.nodes ?? []
    return nodes.find((item) => item.id === selectedGraphNodeId) ?? null
  }, [selectedGraphNodeId, selectedUnitComparison?.graph.nodes])

  useEffect(() => {
    void initializePage()
  }, [])

  useEffect(() => {
    if (!selectedReportId) {
      setReportDetail(null)
      setJobs([])
      setSelectedUnitId(null)
      setComparisonDetail(null)
      return
    }
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
    const graphData = selectedUnitComparison?.graph ?? null
    const container = graphContainerRef.current
    if (!container) {
      return
    }

    if (!graphData || graphData.nodes.length === 0) {
      sigmaRef.current?.kill()
      sigmaRef.current = null
      runtimeGraphRef.current = null
      container.innerHTML = ''
      return
    }

    const runtime = createRuntimeGraph(graphData, { rootNodeId: graphData.rootNodeId ?? null })
    const targetPositions = layoutGraph(runtime, 'force-atlas')
    Object.entries(targetPositions).forEach(([nodeId, position]) => {
      runtime.mergeNodeAttributes(nodeId, position)
    })
    runtimeGraphRef.current = runtime

    if (!sigmaRef.current) {
      sigmaRef.current = new Sigma(runtime, container, createReportSigmaSettings())
      sigmaRef.current.on('clickNode', ({ node }) => setSelectedGraphNodeId(node))
    } else {
      sigmaRef.current.setGraph(runtime)
      sigmaRef.current.setSettings(createReportSigmaSettings())
      sigmaRef.current.refresh()
    }

    setSelectedGraphNodeId(graphData.rootNodeId ?? graphData.nodes[0]?.id ?? null)
    return () => {
      sigmaRef.current?.refresh()
    }
  }, [selectedUnitComparison?.graph])

  useEffect(() => {
    return () => {
      sigmaRef.current?.kill()
      sigmaRef.current = null
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
        setSelectedUnitId(null)
        if (showSpinner) {
          toast.error(latestJob.error || '报告处理失败')
        }
        return
      }

      try {
        const detail = await getReportSpace(documentId)
        setReportDetail(detail)
        setSelectedUnitId((current) => current ?? detail.reportUnits.find((item) => item.unitType === 'text')?.unitUid ?? detail.reportUnits[0]?.unitUid ?? null)
      } catch (error) {
        if (isNotFoundError(error)) {
          setReportDetail(null)
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
        toast.error('该段落评估尚未完成')
      } else {
        toast.error('该段落暂无评估结果')
      }
    }
  }

  return (
    <div className="grid h-[calc(100vh-110px)] gap-5 xl:grid-cols-[320px_minmax(0,1fr)_420px]">
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
                <span className="text-[var(--text-secondary)]">Evaluation</span>
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
                <span>{comparisonDetail ? `${Math.round(comparisonDetail.progress * 100)}%` : '-'}</span>
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
                <span>Covered {comparisonSummary.covered}</span>
                <span>Partial {comparisonSummary.partial}</span>
                <span>Missing {comparisonSummary.missing}</span>
                <span>Violated {comparisonSummary.violated}</span>
              </div>
              {comparisonDetail?.summary ? <div className="text-xs leading-5 text-[var(--text-primary)]">{comparisonDetail.summary}</div> : null}
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
            {loadingReports || loadingComparison || evaluationInProgress ? (
              <div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                <LoaderCircle className="h-4 w-4 animate-spin" />
                {loadingReports ? 'Loading...' : loadingComparison ? 'Loading comparison...' : 'Evaluating...'}
              </div>
            ) : null}
          </div>
          <div className="min-h-0 overflow-auto px-5 py-5">
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
                      type="button"
                      onClick={() => void handleSelectUnit(unit)}
                      className={`subtle-surface w-full px-4 py-4 text-left transition ${unit.unitUid === selectedUnitId ? 'border-[var(--brand)] bg-[var(--brand-soft)]' : ''}`}
                    >
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-xs text-[var(--text-secondary)]">{unit.pageSpan.join('-') || '-'}</span>
                        <span className="text-xs text-[var(--text-secondary)]">{unit.unitType}</span>
                      </div>
                      <div className="mt-3 whitespace-pre-wrap text-sm leading-7 text-[var(--text-primary)]">{unit.textNormalized}</div>
                    </button>
                  </div>
                )
              })}
              {!orderedUnits.length && !loadingReports ? <div className="text-sm text-[var(--text-secondary)]">No parsed text</div> : null}
            </div>
          </div>
        </div>
      </section>

      <section className="panel-surface min-h-0 overflow-hidden">
        <div className="grid h-full min-h-0 grid-rows-[1fr,auto]">
            <div className="relative min-h-[320px]">
              <div ref={graphContainerRef} className="h-full w-full" />
            {!selectedUnitComparison?.graph.nodes.length ? (
              <div className="absolute inset-0 grid place-items-center text-sm text-[var(--text-secondary)]">
                Select a report paragraph to view saved result.
              </div>
            ) : null}
          </div>
          <div className="border-t px-5 py-4" style={{ borderColor: 'var(--line)' }}>
            {selectedGraphNode ? (
              <div className="grid gap-2">
                <div className="text-sm font-semibold text-[var(--text-primary)]">{selectedGraphNode.label}</div>
                <div className="text-xs text-[var(--text-secondary)]">{selectedGraphNode.nodeType}</div>
                <div className="max-h-40 overflow-auto whitespace-pre-wrap text-xs leading-5 text-[var(--text-secondary)]">
                  {resolveNodeText(selectedGraphNode.properties)}
                </div>
              </div>
            ) : selectedUnitComparison ? (
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
            ) : null}
          </div>
        </div>
      </section>
    </div>
  )
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

function summarizeComparison(items: ReportComparisonItem[]) {
  return items.reduce(
    (accumulator, item) => {
      accumulator[item.status] += 1
      return accumulator
    },
    {
      covered: 0,
      partial: 0,
      missing: 0,
      violated: 0,
      not_applicable: 0,
    } as Record<ReportComparisonItem['status'], number>,
  )
}

function resolveNodeText(properties: Record<string, unknown>) {
  const textContent = properties.text_content
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
