import * as Dialog from '@radix-ui/react-dialog'
import * as Tabs from '@radix-ui/react-tabs'
import { LoaderCircle, X } from 'lucide-react'
import { useEffect, useState } from 'react'

import { listDocumentJobs, type IngestionJob } from '../lib/api'
import { StatusBadge } from './StatusBadge'

export function PipelineDialog({
  documentId,
  open,
  onOpenChange,
}: {
  documentId: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [jobs, setJobs] = useState<IngestionJob[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!open || !documentId) {
      return
    }

    let cancelled = false
    setLoading(true)
    listDocumentJobs(documentId)
      .then((items) => {
        if (!cancelled) {
          setJobs(items)
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [documentId, open])

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-slate-950/18 backdrop-blur-sm" />
        <Dialog.Content className="dialog-surface fixed left-1/2 top-1/2 z-50 grid h-[min(760px,88vh)] w-[min(1040px,92vw)] -translate-x-1/2 -translate-y-1/2 overflow-hidden">
          <div className="flex items-center justify-between border-b px-6 py-4" style={{ borderColor: 'var(--line)' }}>
            <div>
              <Dialog.Title className="text-lg font-semibold text-[var(--text-primary)]">Pipeline Status</Dialog.Title>
              <Dialog.Description className="mt-1 text-sm text-[var(--text-secondary)]">Document: {documentId ?? '-'}</Dialog.Description>
            </div>
            <Dialog.Close className="surface-icon-button h-9 w-9">
              <X className="h-4 w-4" />
            </Dialog.Close>
          </div>

          <Tabs.Root defaultValue="timeline" className="grid min-h-0 grid-rows-[auto,1fr]">
            <Tabs.List className="flex gap-2 border-b px-6 py-3" style={{ borderColor: 'var(--line)' }}>
              <Tabs.Trigger value="timeline" className="tab-trigger">Timeline</Tabs.Trigger>
              <Tabs.Trigger value="raw" className="tab-trigger">Raw JSON</Tabs.Trigger>
            </Tabs.List>

            <Tabs.Content value="timeline" className="min-h-0 overflow-auto px-6 py-5">
              {loading ? (
                <div className="flex items-center gap-3 text-sm text-[var(--text-secondary)]">
                  <LoaderCircle className="h-4 w-4 animate-spin" />
                  Loading pipeline data...
                </div>
              ) : jobs.length === 0 ? (
                <div className="subtle-surface px-6 py-8 text-sm text-[var(--text-secondary)]">No job history found for this document.</div>
              ) : (
                <div className="grid gap-4">
                  {jobs.map((job) => (
                    <article key={job.jobId} className="subtle-surface grid gap-4 p-4 md:p-5">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <p className="font-mono text-xs text-[var(--text-secondary)]">{job.jobId}</p>
                          <p className="mt-2 text-sm font-medium text-[var(--text-primary)]">{job.documentType} · {job.parserProvider}</p>
                          <p className="mt-1 text-sm text-[var(--text-secondary)]">Updated {new Date(job.updatedAt).toLocaleString()}</p>
                        </div>
                        <div className="flex items-center gap-3">
                          <span className="text-sm font-medium text-[var(--text-secondary)]">{Math.round(job.progress * 100)}%</span>
                          <StatusBadge status={job.status} />
                        </div>
                      </div>

                      <div className="h-2 overflow-hidden rounded-full bg-white/70 dark:bg-slate-800">
                        <div className="h-full rounded-full bg-[var(--brand)] transition-all" style={{ width: `${Math.min(100, Math.max(4, job.progress * 100))}%` }} />
                      </div>

                      <dl className="grid gap-3 text-sm text-[var(--text-secondary)] md:grid-cols-3">
                        <div>
                          <dt className="section-kicker">Normalized</dt>
                          <dd className="mt-1 text-[var(--text-primary)]">{job.normalizedFormat ?? '-'}</dd>
                        </div>
                        <div>
                          <dt className="section-kicker">Created</dt>
                          <dd className="mt-1 text-[var(--text-primary)]">{new Date(job.createdAt).toLocaleString()}</dd>
                        </div>
                        <div>
                          <dt className="section-kicker">Error</dt>
                          <dd className="mt-1 text-[var(--text-primary)]">{job.error ?? '-'}</dd>
                        </div>
                      </dl>

                      <div className="grid gap-2">
                        <p className="section-kicker">Preprocessing Actions</p>
                        {job.preprocessingActions.length === 0 ? (
                          <p className="text-sm text-[var(--text-secondary)]">No preprocessing actions recorded.</p>
                        ) : (
                          <div className="flex flex-wrap gap-2">
                            {job.preprocessingActions.map((action) => (
                              <span key={action} className="code-chip">{action}</span>
                            ))}
                          </div>
                        )}
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </Tabs.Content>

            <Tabs.Content value="raw" className="min-h-0 overflow-auto px-6 py-5">
              <pre className="subtle-surface overflow-auto px-4 py-4 font-mono text-xs leading-6 text-[var(--text-secondary)]">
                {JSON.stringify(jobs, null, 2)}
              </pre>
            </Tabs.Content>
          </Tabs.Root>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
