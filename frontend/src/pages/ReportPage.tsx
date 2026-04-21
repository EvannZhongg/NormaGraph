import { FileUp, FileText, Layers3 } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import { uploadDocument } from '../lib/api'

export function ReportPage() {
  const [file, setFile] = useState<File | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [lastJobId, setLastJobId] = useState<string | null>(null)

  async function handleUpload() {
    if (!file) {
      toast.error('请先选择一份报告文件')
      return
    }

    const formData = new FormData()
    formData.append('file', file)
    formData.append('document_type', 'report')
    formData.append('build_graph', 'true')

    setSubmitting(true)
    try {
      const job = await uploadDocument(formData)
      setLastJobId(job.jobId)
      setFile(null)
      toast.success(`已提交报告处理任务 ${job.jobId}`)
    } catch (error) {
      toast.error(extractErrorMessage(error, '报告上传失败'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="page-stack">
      <section className="page-header">
        <div>
          <p className="section-kicker">Report Workflow</p>
          <h1 className="page-title">Report Upload</h1>
          <p className="page-subtitle">
            上传报告文件后，系统会按报告标题规划流程进行分块处理。当前页面只保留上传入口，避免混入标准文档的额外参数。
          </p>
        </div>
      </section>

      <section className="panel-surface mx-auto w-full max-w-5xl overflow-hidden">
        <div className="grid lg:grid-cols-[1fr,1.15fr]">
          <div className="grid gap-5 border-b px-6 py-6 lg:border-b-0 lg:border-r lg:px-7 lg:py-7" style={{ borderColor: 'var(--line)' }}>
            <div className="inline-flex h-12 w-12 items-center justify-center rounded-2xl" style={{ background: 'var(--brand-soft)', color: 'var(--brand)' }}>
              <FileText className="h-5 w-5" />
            </div>
            <div className="grid gap-3">
              <div>
                <h2 className="text-[1.2rem] font-semibold text-[var(--text-primary)]">上传窗口</h2>
                <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">
                  支持 `PDF`、`DOC`、`DOCX`。上传后会直接创建 `report` 类型任务，并进入报告标题规划与分块流程。
                </p>
              </div>

              <div className="subtle-surface grid gap-3 px-4 py-4 text-sm text-[var(--text-secondary)]">
                <div className="flex items-start gap-3">
                  <Layers3 className="mt-0.5 h-4 w-4 shrink-0 text-[var(--brand)]" />
                  <span>后端会写出 `data/report_spaces/&lt;document_id&gt;/` 下的报告分块产物。</span>
                </div>
                <div className="flex items-start gap-3">
                  <FileUp className="mt-0.5 h-4 w-4 shrink-0 text-[var(--brand)]" />
                  <span>当前不附加额外命令参数，也不增加额外兜底选项。</span>
                </div>
              </div>

              {lastJobId ? (
                <div className="subtle-surface px-4 py-4 text-sm text-[var(--text-secondary)]">
                  <p className="font-medium text-[var(--text-primary)]">最近一次任务已提交</p>
                  <p className="mt-2 font-mono text-xs text-[var(--text-secondary)]">{lastJobId}</p>
                </div>
              ) : null}
            </div>
          </div>

          <div className="px-6 py-6 lg:px-7 lg:py-7">
            <div className="grid gap-5">
              <label className="grid gap-2 text-sm text-[var(--text-secondary)]">
                <span className="font-medium text-[var(--text-primary)]">报告文件</span>
                <input
                  type="file"
                  accept=".pdf,.doc,.docx"
                  onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                  className="control-input"
                />
                <span>{file ? file.name : '请选择 PDF / DOC / DOCX 文件'}</span>
              </label>

              <div className="subtle-surface px-4 py-4 text-sm text-[var(--text-secondary)]">
                <p className="font-medium text-[var(--text-primary)]">处理说明</p>
                <p className="mt-2 leading-6">
                  上传后将复用现有文档上传接口，但会固定使用 `document_type=report`，以便进入报告分块流程。
                </p>
              </div>

              <div className="flex flex-wrap justify-end gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setFile(null)
                    setLastJobId(null)
                  }}
                  className="surface-button compact-button"
                  disabled={submitting}
                >
                  Clear
                </button>
                <button
                  type="button"
                  onClick={() => void handleUpload()}
                  disabled={submitting}
                  className="surface-button primary-dark-button compact-button disabled:opacity-60"
                >
                  <FileUp className="h-4 w-4" />
                  {submitting ? 'Uploading...' : 'Upload report'}
                </button>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
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
