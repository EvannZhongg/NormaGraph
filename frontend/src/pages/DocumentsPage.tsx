import * as Dialog from '@radix-ui/react-dialog'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import { CheckCircle2, Ellipsis, Eraser, FileUp, RefreshCw, Trash2, Upload, Workflow, X } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'

import { PipelineDialog } from '../components/PipelineDialog'
import { StatusBadge } from '../components/StatusBadge'
import { deleteDocument, listDocuments, retryDocument, uploadDocument, type DocumentSummary } from '../lib/api'
import { useAppStore } from '../store/app-store'

const PAGE_SIZE = 8

const statusFilters = [
  { key: 'all', label: 'All', matcher: (_item: DocumentSummary) => true },
  { key: 'completed', label: 'Completed', matcher: (item: DocumentSummary) => ['ready', 'succeeded'].includes(item.status) },
  { key: 'preprocessed', label: 'Preprocessed', matcher: (item: DocumentSummary) => item.status === 'idle' },
  { key: 'processing', label: 'Processing', matcher: (item: DocumentSummary) => item.status === 'running' },
  { key: 'pending', label: 'Pending', matcher: (item: DocumentSummary) => item.status === 'queued' },
  { key: 'failed', label: 'Failed', matcher: (item: DocumentSummary) => item.status === 'failed' },
] as const

export function DocumentsPage() {
  const navigate = useNavigate()
  const setSelectedStandardId = useAppStore((state) => state.setSelectedStandardId)
  const [documents, setDocuments] = useState<DocumentSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [statusFilter, setStatusFilter] = useState<(typeof statusFilters)[number]['key']>('all')
  const [page, setPage] = useState(1)
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(null)
  const [pipelineOpen, setPipelineOpen] = useState(false)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [command, setCommand] = useState('--standard-id sl274:2001')
  const [buildGraph, setBuildGraph] = useState(true)


  const filteredDocuments = useMemo(() => {
    const activeFilter = statusFilters.find((item) => item.key === statusFilter) ?? statusFilters[0]
    return documents.filter((item) => activeFilter.matcher(item))
  }, [documents, statusFilter])

  const pageCount = Math.max(1, Math.ceil(filteredDocuments.length / PAGE_SIZE))
  const pageItems = filteredDocuments.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  useEffect(() => {
    void refreshDocuments()
  }, [])

  useEffect(() => {
    setPage(1)
  }, [statusFilter])

  useEffect(() => {
    if (selectedDocumentId && !documents.some((item) => item.documentId === selectedDocumentId)) {
      setSelectedDocumentId(null)
    }
  }, [documents, selectedDocumentId])

  useEffect(() => {
    const hasActiveJobs = documents.some((item) => item.status === 'queued' || item.status === 'running')
    if (!hasActiveJobs) {
      return
    }
    const timer = window.setInterval(() => {
      void refreshDocuments(false)
    }, 5000)
    return () => window.clearInterval(timer)
  }, [documents])

  async function refreshDocuments(showSpinner = true) {
    if (showSpinner) {
      setLoading(true)
    }
    try {
      const items = await listDocuments()
      setDocuments(items)
      setPage((current) => Math.min(current, Math.max(1, Math.ceil(items.length / PAGE_SIZE))))
    } catch (error) {
      toast.error(extractErrorMessage(error, '文档列表加载失败'))
    } finally {
      if (showSpinner) {
        setLoading(false)
      }
    }
  }

  async function handleUpload() {
    if (!file) {
      toast.error('请先选择一个规范文件')
      return
    }
    const formData = new FormData()
    formData.append('file', file)
    formData.append('command', command)
    formData.append('build_graph', String(buildGraph))
    formData.append('document_type', 'standard')

    setSubmitting(true)
    try {
      const job = await uploadDocument(formData)
      toast.success(`已提交扫描任务 ${job.jobId}`)
      setFile(null)
      setUploadOpen(false)
      await refreshDocuments(false)
    } catch (error) {
      toast.error(extractErrorMessage(error, '上传失败'))
    } finally {
      setSubmitting(false)
    }
  }

  async function handleRetry(documentId: string) {
    try {
      const job = await retryDocument(documentId)
      toast.success(`已提交重试任务 ${job.jobId}`)
      await refreshDocuments(false)
    } catch (error) {
      toast.error(extractErrorMessage(error, '重试失败'))
    }
  }

  async function handleRetrySelected() {
    if (!selectedDocumentId) {
      toast.error('请先选择一条文档记录')
      return
    }
    await handleRetry(selectedDocumentId)
  }

  async function handleDelete(documentId: string) {
    try {
      await deleteDocument(documentId)
      toast.success('文档已删除')
      await refreshDocuments(false)
    } catch (error) {
      toast.error(extractErrorMessage(error, '删除失败'))
    }
  }

  function handlePipelineSelected() {
    if (!selectedDocumentId) {
      toast.error('请先选择一条文档记录')
      return
    }
    setPipelineOpen(true)
  }

  function openGraph(document: DocumentSummary) {
    if (!document.standardId) {
      toast.error('当前文档还没有关联的 standard id')
      return
    }
    setSelectedStandardId(document.standardId)
    navigate('/knowledge-graph')
  }

  function handleClear() {
    setStatusFilter('all')
    setSelectedDocumentId(null)
    setFile(null)
    setCommand('--standard-id sl274:2001')
    setBuildGraph(true)
  }

  return (
    <div className="page-stack">

      <div className="toolbar-strip">
        <div className="toolbar-group">
          <button type="button" onClick={() => void handleRetrySelected()} className="surface-button compact-button" disabled={!selectedDocumentId}>
            <RefreshCw className="h-4 w-4" />
            Scan/Retry
          </button>
          <button type="button" onClick={handlePipelineSelected} className="surface-button compact-button" disabled={!selectedDocumentId}>
            <Workflow className="h-4 w-4" />
            Pipeline
          </button>
        </div>
        <div className="toolbar-group">
          <button type="button" onClick={handleClear} className="surface-button compact-button">
            <Eraser className="h-4 w-4" />
            Clear
          </button>
          <button type="button" onClick={() => setUploadOpen(true)} className="surface-button primary-dark-button compact-button">
            <Upload className="h-4 w-4" />
            Upload
          </button>
        </div>
      </div>

      <section className="panel-surface overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-4 border-b px-5 py-4" style={{ borderColor: 'var(--line)' }}>
          <div>
            <h2 className="text-[1.1rem] font-semibold text-[var(--text-primary)]">Uploaded Documents</h2>
            <p className="mt-1 text-sm text-[var(--text-secondary)]">点击某一行即可选中，顶部动作会作用于当前选中的文档。</p>
          </div>
          <div className="toolbar-group">
            {statusFilters.map((item) => {
              const count = item.key === 'all' ? documents.length : documents.filter((document) => item.matcher(document)).length
              return (
                <button
                  key={item.key}
                  type="button"
                  onClick={() => setStatusFilter(item.key)}
                  className={`filter-chip ${statusFilter === item.key ? 'is-active' : ''}`}
                >
                  <span>{item.label}</span>
                  <span>({count})</span>
                </button>
              )
            })}
            <button type="button" onClick={() => void refreshDocuments()} className="surface-icon-button" aria-label="Refresh documents">
              <RefreshCw className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th className="min-w-[240px]">ID</th>
                <th className="min-w-[360px]">Summary</th>
                <th>Status</th>
                <th>Graph</th>
                <th>Created</th>
                <th>Updated</th>
                <th className="w-[110px] text-right">Action</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={7} className="px-4 py-16 text-center text-sm text-[var(--text-secondary)]">
                    加载文档列表中...
                  </td>
                </tr>
              ) : pageItems.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-4 py-16 text-center text-sm text-[var(--text-secondary)]">
                    当前过滤条件下没有文档。
                  </td>
                </tr>
              ) : (
                pageItems.map((document) => (
                  <tr
                    key={document.documentId}
                    className={document.documentId === selectedDocumentId ? 'is-selected' : ''}
                    onClick={() => setSelectedDocumentId(document.documentId)}
                  >
                    <td>
                      <div className="grid gap-1">
                        <span className="font-mono text-xs text-[var(--text-secondary)]">{document.documentId}</span>
                        <span className="text-sm text-[var(--text-primary)]">{document.sourceName ?? document.displayName}</span>
                      </div>
                    </td>
                    <td>
                      <div className="grid gap-1">
                        <span className="font-medium text-[var(--text-primary)]">{document.displayName}</span>
                        <span className="line-clamp-1 text-sm text-[var(--text-secondary)]">{document.title ?? document.standardId ?? document.sourceName ?? '-'}</span>
                      </div>
                    </td>
                    <td><StatusBadge status={document.status} /></td>
                    <td><StatusBadge status={document.graphStatus ?? 'idle'} /></td>
                    <td className="text-sm text-[var(--text-secondary)]">{document.createdAt ? new Date(document.createdAt).toLocaleString() : '-'}</td>
                    <td className="text-sm text-[var(--text-secondary)]">{document.updatedAt ? new Date(document.updatedAt).toLocaleString() : '-'}</td>
                    <td className="text-right">
                      <DropdownMenu.Root>
                        <DropdownMenu.Trigger
                          className="surface-icon-button ml-auto h-9 w-9"
                          onClick={(event) => event.stopPropagation()}
                        >
                          <Ellipsis className="h-4 w-4" />
                        </DropdownMenu.Trigger>
                        <DropdownMenu.Portal>
                          <DropdownMenu.Content sideOffset={8} className="menu-surface min-w-52">
                            <ActionItem onSelect={() => setPipelineOpen(true)} icon={<Workflow className="h-4 w-4" />} label="查看流水线" />
                            <ActionItem onSelect={() => void handleRetry(document.documentId)} icon={<RefreshCw className="h-4 w-4" />} label="重试" />
                            <ActionItem onSelect={() => openGraph(document)} icon={<CheckCircle2 className="h-4 w-4" />} label="打开图谱" />
                            <ActionItem onSelect={() => void handleDelete(document.documentId)} icon={<Trash2 className="h-4 w-4" />} label="删除" danger />
                          </DropdownMenu.Content>
                        </DropdownMenu.Portal>
                      </DropdownMenu.Root>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t px-5 py-4 text-sm text-[var(--text-secondary)]" style={{ borderColor: 'var(--line)' }}>
          <span>
            Page {page} / {pageCount} · {filteredDocuments.length} rows
          </span>
          <div className="toolbar-group">
            <button type="button" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))} className="surface-button compact-button disabled:opacity-40">
              Previous
            </button>
            <button type="button" disabled={page >= pageCount} onClick={() => setPage((value) => Math.min(pageCount, value + 1))} className="surface-button compact-button disabled:opacity-40">
              Next
            </button>
          </div>
        </div>
      </section>

      <Dialog.Root open={uploadOpen} onOpenChange={setUploadOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-40 bg-slate-950/14 backdrop-blur-sm" />
          <Dialog.Content className="dialog-surface fixed left-1/2 top-1/2 z-50 w-[min(720px,92vw)] -translate-x-1/2 -translate-y-1/2 p-6">
            <div className="flex items-start justify-between gap-4">
              <div>
                <Dialog.Title className="text-xl font-semibold text-[var(--text-primary)]">Upload Standard Document</Dialog.Title>
                <Dialog.Description className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">
                  支持通过命令字段覆盖 standard id，例如 <code className="code-chip">--standard-id sl274:2001</code>。
                </Dialog.Description>
              </div>
              <Dialog.Close className="surface-icon-button">
                <X className="h-4 w-4" />
              </Dialog.Close>
            </div>

            <div className="mt-5 grid gap-4">
              <label className="grid gap-2 text-sm text-[var(--text-secondary)]">
                <span className="font-medium text-[var(--text-primary)]">规范文件</span>
                <input
                  type="file"
                  accept=".pdf,.doc,.docx"
                  onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                  className="control-input"
                />
                <span>{file ? file.name : '支持 PDF / DOC / DOCX'}</span>
              </label>

              <label className="grid gap-2 text-sm text-[var(--text-secondary)]">
                <span className="font-medium text-[var(--text-primary)]">命令参数</span>
                <textarea
                  value={command}
                  onChange={(event) => setCommand(event.target.value)}
                  rows={5}
                  className="control-textarea font-mono"
                />
              </label>

              <label className="subtle-surface flex items-center justify-between px-4 py-3 text-sm text-[var(--text-secondary)]">
                <div>
                  <p className="font-medium text-[var(--text-primary)]">构建图谱</p>
                  <p className="mt-1">上传后直接执行标准流水线并写入当前 kg space。</p>
                </div>
                <button
                  type="button"
                  onClick={() => setBuildGraph((value) => !value)}
                  className={`inline-flex h-7 w-14 items-center rounded-full border p-1 transition ${buildGraph ? 'border-emerald-200 bg-emerald-100' : 'border-[var(--line)] bg-[var(--bg-elevated)]'}`}
                >
                  <span className={`h-5 w-5 rounded-full bg-white shadow-sm transition ${buildGraph ? 'translate-x-6' : 'translate-x-0'}`} />
                </button>
              </label>
            </div>

            <div className="mt-6 flex justify-end gap-2">
              <button type="button" onClick={() => setUploadOpen(false)} className="surface-button compact-button">
                Cancel
              </button>
              <button type="button" onClick={() => void handleUpload()} disabled={submitting} className="surface-button primary-dark-button compact-button disabled:opacity-60">
                <FileUp className="h-4 w-4" />
                {submitting ? 'Uploading...' : 'Upload and scan'}
              </button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      <PipelineDialog documentId={selectedDocumentId} open={pipelineOpen} onOpenChange={setPipelineOpen} />
    </div>
  )
}

function ActionItem({
  icon,
  label,
  onSelect,
  danger = false,
}: {
  icon: ReactNode
  label: string
  onSelect: () => void
  danger?: boolean
}) {
  return (
    <DropdownMenu.Item
      onSelect={onSelect}
      className={`menu-item flex items-center gap-2 ${danger ? 'text-rose-600' : ''}`}
    >
      {icon}
      {label}
    </DropdownMenu.Item>
  )
}

function extractErrorMessage(error: unknown, fallback: string) {
  if (typeof error === 'object' && error && 'response' in error) {
    const detail = (error as { response?: { data?: { detail?: string } } }).response?.data?.detail
    if (detail) {
      return detail
    }
  }
  return fallback
}


