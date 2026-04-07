import clsx from 'clsx'

import type { DocumentStatus, JobStatus } from '../lib/api'

const toneMap: Record<string, string> = {
  idle: 'border border-slate-200 bg-slate-100 text-slate-600 dark:border-slate-700 dark:bg-slate-800/70 dark:text-slate-200',
  queued: 'border border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200',
  running: 'border border-sky-200 bg-sky-50 text-sky-700 dark:border-sky-900 dark:bg-sky-950/40 dark:text-sky-200',
  building: 'border border-sky-200 bg-sky-50 text-sky-700 dark:border-sky-900 dark:bg-sky-950/40 dark:text-sky-200',
  succeeded: 'border border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200',
  ready: 'border border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200',
  failed: 'border border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-200',
  not_built: 'border border-slate-200 bg-slate-100 text-slate-600 dark:border-slate-700 dark:bg-slate-800/70 dark:text-slate-200',
}

const labelMap: Record<string, string> = {
  idle: 'Preprocessed',
  queued: 'Pending',
  running: 'Processing',
  building: 'Building',
  succeeded: 'Completed',
  ready: 'Ready',
  failed: 'Failed',
  not_built: 'Not Built',
}

export function StatusBadge({ status }: { status: DocumentStatus | JobStatus | string | null | undefined }) {
  const normalized = (status ?? 'idle').toString().trim()

  return (
    <span
      className={clsx(
        'inline-flex min-w-[84px] items-center justify-center rounded-full px-2.5 py-1 text-[11px] font-semibold tracking-[0.04em]',
        toneMap[normalized] ?? toneMap.idle,
      )}
    >
      {labelMap[normalized] ?? normalized}
    </span>
  )
}
