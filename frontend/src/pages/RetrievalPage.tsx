import { MessageSquareText, Send, Settings2, SlidersHorizontal, Sparkles } from 'lucide-react'

import { useAppStore } from '../store/app-store'

export function RetrievalPage() {
  const retrieval = useAppStore((state) => state.retrieval)
  const patchRetrieval = useAppStore((state) => state.patchRetrieval)

  return (
    <div className="page-stack">

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr),340px]">
        <section className="panel-surface grid min-h-[760px] grid-rows-[auto,1fr,auto] gap-4 p-5">
          <div className="toolbar-strip">
            <div className="toolbar-group">
              <span className="metric-pill">history {retrieval.historyTurns}</span>
              <span className="metric-pill">chunk_top_k {retrieval.chunkTopK}</span>
              <span className="metric-pill">rerank {retrieval.rerank ? 'on' : 'off'}</span>
            </div>
            <div className="toolbar-group text-sm text-[var(--text-secondary)]">
              <Sparkles className="h-4 w-4" />
              Backend not implemented yet
            </div>
          </div>

          <div className="subtle-surface grid min-h-0 grid-rows-[auto,1fr] overflow-hidden p-4">
            <div className="flex items-center justify-between gap-3 border-b pb-4" style={{ borderColor: 'var(--line)' }}>
              <div className="flex items-center gap-3">
                <div className="grid h-10 w-10 place-items-center rounded-xl bg-[var(--brand-soft)] text-[var(--brand)]">
                  <MessageSquareText className="h-5 w-5" />
                </div>
                <div>
                  <h2 className="text-lg font-semibold text-[var(--text-primary)]">Chat Console</h2>
                  <p className="text-sm text-[var(--text-secondary)]">预留 SSE / chunked streaming 输出区</p>
                </div>
              </div>
            </div>

            <div className="grid gap-4 overflow-auto py-4">
              <article className="max-w-[80%] rounded-3xl rounded-tl-md border border-[var(--line)] bg-[var(--bg-elevated)] px-5 py-4 text-sm leading-6 text-[var(--text-secondary)]">
                <p className="font-semibold text-[var(--text-primary)]">Assistant</p>
                <p className="mt-2">
                  Retrieval backend is not implemented yet. This workspace is already laid out for streaming answers, citations, and parameter-controlled retrieval experiments.
                </p>
              </article>

              <article className="ml-auto max-w-[72%] rounded-3xl rounded-tr-md border border-[var(--line)] bg-[var(--bg-muted)] px-5 py-4 text-sm leading-6 text-[var(--text-secondary)]">
                <p className="font-semibold text-[var(--text-primary)]">You</p>
                <p className="mt-2">后续这里会直接接问答输入、历史轮次和流式返回内容。</p>
              </article>
            </div>
          </div>

          <div className="subtle-surface grid gap-3 p-4">
            <textarea className="control-textarea min-h-[120px]" placeholder="输入问题，后续这里会接流式输出。" disabled />
            <div className="flex items-center justify-between gap-3">
              <p className="text-sm text-[var(--text-secondary)]">当前为占位工作台，发送动作已禁用。</p>
              <button type="button" disabled className="surface-button primary-button compact-button opacity-60">
                <Send className="h-4 w-4" />
                Send
              </button>
            </div>
          </div>
        </section>

        <aside className="panel-surface grid gap-4 p-5">
          <div className="flex items-center gap-2 text-[var(--text-primary)]">
            <Settings2 className="h-4 w-4" />
            <h2 className="text-lg font-semibold">参数面板</h2>
          </div>

          <div className="subtle-surface grid gap-4 p-4">
            <div className="flex items-center gap-2 text-sm font-medium text-[var(--text-primary)]">
              <SlidersHorizontal className="h-4 w-4" />
              Retrieval Settings
            </div>

            <label className="grid gap-2 text-sm text-[var(--text-secondary)]">
              <span>Query Mode</span>
              <select value={retrieval.queryMode} onChange={(event) => patchRetrieval({ queryMode: event.target.value as 'hybrid' | 'graph' | 'vector' })} className="control-select">
                <option value="hybrid">hybrid</option>
                <option value="graph">graph</option>
                <option value="vector">vector</option>
              </select>
            </label>

            <NumberField label="top_k" value={retrieval.topK} onChange={(value) => patchRetrieval({ topK: value })} />
            <NumberField label="chunk_top_k" value={retrieval.chunkTopK} onChange={(value) => patchRetrieval({ chunkTopK: value })} />
            <NumberField label="history turns" value={retrieval.historyTurns} onChange={(value) => patchRetrieval({ historyTurns: value })} />

            <label className="grid gap-2 text-sm text-[var(--text-secondary)]">
              <span>User Prompt</span>
              <textarea value={retrieval.userPrompt} onChange={(event) => patchRetrieval({ userPrompt: event.target.value })} rows={8} className="control-textarea" />
            </label>

            <label className="subtle-surface flex items-center justify-between px-4 py-3 text-sm text-[var(--text-secondary)]">
              <span>Rerank</span>
              <button
                type="button"
                onClick={() => patchRetrieval({ rerank: !retrieval.rerank })}
                className={`inline-flex h-7 w-14 items-center rounded-full border p-1 transition ${retrieval.rerank ? 'border-emerald-200 bg-emerald-100 dark:border-emerald-900 dark:bg-emerald-950/40' : 'border-[var(--line)] bg-[var(--bg-elevated)]'}`}
              >
                <span className={`h-5 w-5 rounded-full bg-white shadow-sm transition ${retrieval.rerank ? 'translate-x-6' : 'translate-x-0'}`} />
              </button>
            </label>
          </div>
        </aside>
      </div>
    </div>
  )
}

function NumberField({ label, value, onChange }: { label: string; value: number; onChange: (value: number) => void }) {
  return (
    <label className="grid gap-2 text-sm text-[var(--text-secondary)]">
      <span>{label}</span>
      <input value={value} type="number" min={1} onChange={(event) => onChange(Number(event.target.value))} className="control-input" />
    </label>
  )
}

