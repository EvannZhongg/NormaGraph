import axios from 'axios'
import {
  AlertCircle,
  BotMessageSquare,
  Database,
  Loader2,
  MessageSquareText,
  RefreshCw,
  Send,
  Settings2,
  SlidersHorizontal,
} from 'lucide-react'
import { FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import {
  askQuestion,
  fetchGraphServiceStatus,
  listKgSpaces,
  type GraphServiceStatus,
  type KgSpaceSummary,
  type QuestionResponse,
} from '../lib/api'
import { useAppStore } from '../store/app-store'

type ChatMessage = {
  id: string
  role: 'assistant' | 'user' | 'system'
  content: string
  status?: 'pending' | 'error' | 'ok'
  response?: QuestionResponse
}

export function RetrievalPage() {
  const retrieval = useAppStore((state) => state.retrieval)
  const patchRetrieval = useAppStore((state) => state.patchRetrieval)
  const selectedStandardId = useAppStore((state) => state.selectedStandardId)
  const setSelectedStandardId = useAppStore((state) => state.setSelectedStandardId)
  const [kgSpaces, setKgSpaces] = useState<KgSpaceSummary[]>([])
  const [serviceStatus, setServiceStatus] = useState<GraphServiceStatus | null>(null)
  const [isLoadingSpaces, setIsLoadingSpaces] = useState(true)
  const [spacesError, setSpacesError] = useState<string | null>(null)
  const [question, setQuestion] = useState('')
  const [isAsking, setIsAsking] = useState(false)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const scrollRef = useRef<HTMLDivElement | null>(null)

  const selectedSpace = useMemo(
    () => kgSpaces.find((item) => item.standardId === selectedStandardId) ?? kgSpaces[0] ?? null,
    [kgSpaces, selectedStandardId],
  )

  useEffect(() => {
    void loadSpaces()
  }, [])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    if (!selectedStandardId && kgSpaces.length > 0) {
      setSelectedStandardId(kgSpaces[0].standardId)
    }
  }, [kgSpaces, selectedStandardId, setSelectedStandardId])

  async function loadSpaces() {
    setIsLoadingSpaces(true)
    setSpacesError(null)
    try {
      const [spaces, status] = await Promise.all([listKgSpaces(), fetchGraphServiceStatus()])
      setKgSpaces(spaces)
      setServiceStatus(status)
    } catch (error) {
      setSpacesError(error instanceof Error ? error.message : 'KG spaces 加载失败')
    } finally {
      setIsLoadingSpaces(false)
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmedQuestion = question.trim()
    if (!trimmedQuestion || isAsking) {
      return
    }

    const activeStandardId = selectedSpace?.standardId
    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      status: 'ok',
      content: trimmedQuestion,
    }
    setMessages((items) => [...items, userMessage])
    setQuestion('')
    setIsAsking(true)

    try {
      const response = await askQuestion({
        question: trimmedQuestion,
        standardIds: activeStandardId ? [activeStandardId] : [],
        queryMode: retrieval.queryMode,
        topK: clampNumber(retrieval.topK, 1, 100),
        chunkTopK: clampNumber(retrieval.chunkTopK, 1, 100),
        historyTurns: clampNumber(retrieval.historyTurns, 0, 20),
        rerank: retrieval.rerank,
        userPrompt: retrieval.userPrompt.trim() || null,
        expandCitations: true,
      })
      setMessages((items) => [
        ...items,
        {
          id: crypto.randomUUID(),
          role: 'assistant',
          status: 'ok',
          content: response.answer,
          response,
        },
      ])
    } catch (error) {
      const message = axios.isAxiosError(error)
        ? buildApiErrorMessage(error.response?.status, error.response?.data)
        : error instanceof Error
          ? error.message
          : 'QA 请求失败'
      setMessages((items) => [
        ...items,
        {
          id: crypto.randomUUID(),
          role: 'assistant',
          status: 'error',
          content: message,
        },
      ])
    } finally {
      setIsAsking(false)
    }
  }

  return (
    <div className="page-stack">
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr),360px]">
        <section className="panel-surface grid min-h-[calc(100svh-132px)] grid-rows-[1fr,auto] gap-4 p-5">
          <div className="subtle-surface grid min-h-0 overflow-hidden p-4">
            <div ref={scrollRef} className="grid content-start gap-4 overflow-auto py-4">
              {messages.map((message) => (
                <ChatBubble key={message.id} message={message} />
              ))}
            </div>
          </div>

          <form onSubmit={handleSubmit} className="subtle-surface grid gap-3 px-4 pb-4 pt-2">
            <textarea
              className="control-textarea min-h-[112px]"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="输入要面向当前 kg-space 提问的问题"
              disabled={isAsking}
            />
            <div className="flex flex-wrap items-center justify-end gap-3">
              <button type="submit" disabled={isAsking || !question.trim()} className="surface-button primary-button compact-button disabled:opacity-55">
                {isAsking ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                Send
              </button>
            </div>
          </form>
        </section>

        <aside className="panel-surface grid content-start gap-4 p-5">
          <div className="flex items-center justify-between gap-3 text-[var(--text-primary)]">
            <div className="flex items-center gap-2">
              <Settings2 className="h-4 w-4" />
              <h2 className="text-lg font-semibold">RAG Settings</h2>
            </div>
            <button type="button" onClick={() => void loadSpaces()} className="surface-icon-button" aria-label="Refresh KG spaces">
              <RefreshCw className={`h-4 w-4 ${isLoadingSpaces ? 'animate-spin' : ''}`} />
            </button>
          </div>

          <div className="subtle-surface grid gap-3 p-4">
            <div className="flex items-center gap-2 text-sm font-medium text-[var(--text-primary)]">
              <Database className="h-4 w-4" />
              KG Space
            </div>

            {spacesError ? <InlineNotice tone="error" text={spacesError} /> : null}
            {isLoadingSpaces ? <InlineNotice text="正在读取 data/kg_spaces 产物..." /> : null}

            <div className="grid gap-2">
              {kgSpaces.map((space) => (
                <button
                  key={space.standardId}
                  type="button"
                  onClick={() => setSelectedStandardId(space.standardId)}
                  className={`kg-space-option ${space.standardId === selectedSpace?.standardId ? 'is-active' : ''}`}
                >
                  <span className="flex min-w-0 items-center justify-between gap-2">
                    <strong className="truncate">{space.standardId}</strong>
                    <span className="status-dot text-emerald-500" />
                  </span>
                  <span className="truncate">{space.title}</span>
                  <span className="flex flex-wrap gap-2">
                    <small>{space.nodeCount} nodes</small>
                    <small>{space.edgeCount} edges</small>
                    <small>{space.requirementCount} reqs</small>
                  </span>
                </button>
              ))}
              {!isLoadingSpaces && kgSpaces.length === 0 ? <InlineNotice text="未发现可用 kg-space。" /> : null}
            </div>
          </div>

          <div className="subtle-surface grid gap-4 p-4">
            <div className="flex items-center gap-2 text-sm font-medium text-[var(--text-primary)]">
              <SlidersHorizontal className="h-4 w-4" />
              Retrieval
            </div>

            <label className="grid gap-2 text-sm text-[var(--text-secondary)]">
              <span>Query Mode</span>
              <select value={retrieval.queryMode} onChange={(event) => patchRetrieval({ queryMode: event.target.value as 'hybrid' | 'graph' | 'vector' })} className="control-select">
                <option value="hybrid">hybrid</option>
                <option value="graph">graph</option>
                <option value="vector">vector</option>
              </select>
            </label>

            <NumberField label="top_k" min={1} max={100} value={retrieval.topK} onChange={(value) => patchRetrieval({ topK: value })} />
            <NumberField label="chunk_top_k" min={1} max={100} value={retrieval.chunkTopK} onChange={(value) => patchRetrieval({ chunkTopK: value })} />
            <NumberField label="history turns" min={0} max={20} value={retrieval.historyTurns} onChange={(value) => patchRetrieval({ historyTurns: value })} />

            <label className="grid gap-2 text-sm text-[var(--text-secondary)]">
              <span>User Prompt</span>
              <textarea
                value={retrieval.userPrompt}
                onChange={(event) => patchRetrieval({ userPrompt: event.target.value })}
                rows={6}
                className="control-textarea"
                placeholder="可选：给 QA 模型的额外指令"
              />
            </label>

            <label className="subtle-surface flex items-center justify-between px-4 py-3 text-sm text-[var(--text-secondary)]">
              <span>Rerank</span>
              <button
                type="button"
                onClick={() => patchRetrieval({ rerank: !retrieval.rerank })}
                className={`inline-flex h-7 w-14 items-center rounded-full border p-1 transition ${retrieval.rerank ? 'border-emerald-200 bg-emerald-100 dark:border-emerald-900 dark:bg-emerald-950/40' : 'border-[var(--line)] bg-[var(--bg-elevated)]'}`}
                aria-pressed={retrieval.rerank}
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

function ChatBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user'
  const isError = message.status === 'error'
  return (
    <article className={`chat-bubble ${isUser ? 'is-user' : ''} ${isError ? 'is-error' : ''}`}>
      <div className="mb-2 flex items-center gap-2 font-semibold text-[var(--text-primary)]">
        {isUser ? <MessageSquareText className="h-4 w-4" /> : <BotMessageSquare className="h-4 w-4" />}
        <span>{isUser ? 'You' : isError ? 'QA API' : 'Assistant'}</span>
        {message.status === 'pending' ? <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--text-dim)]" /> : null}
        {isError ? <AlertCircle className="h-3.5 w-3.5 text-red-500" /> : null}
      </div>
      <div className="markdown-body text-sm leading-6 text-[var(--text-secondary)]">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
      </div>
      {message.response?.citations?.length ? (
        <p className="mt-3 text-xs text-[var(--text-dim)]">{message.response.citations.length} citations</p>
      ) : null}
    </article>
  )
}

function NumberField({
  label,
  min,
  max,
  value,
  onChange,
}: {
  label: string
  min: number
  max: number
  value: number
  onChange: (value: number) => void
}) {
  return (
    <label className="grid gap-2 text-sm text-[var(--text-secondary)]">
      <span>{label}</span>
      <input
        value={value}
        type="number"
        min={min}
        max={max}
        onChange={(event) => onChange(clampNumber(Number(event.target.value), min, max))}
        className="control-input"
      />
    </label>
  )
}

function InlineNotice({ text, tone = 'neutral' }: { text: string; tone?: 'neutral' | 'error' }) {
  return (
    <div className={`inline-notice ${tone === 'error' ? 'is-error' : ''}`}>
      {tone === 'error' ? <AlertCircle className="h-4 w-4 shrink-0" /> : <Loader2 className="h-4 w-4 shrink-0 animate-spin" />}
      <span>{text}</span>
    </div>
  )
}

function clampNumber(value: number, min: number, max: number) {
  if (!Number.isFinite(value)) {
    return min
  }
  return Math.min(max, Math.max(min, Math.round(value)))
}

function buildApiErrorMessage(status: number | undefined, data: unknown) {
  const detail = typeof data === 'object' && data !== null && 'detail' in data ? String((data as { detail?: unknown }).detail) : ''
  return `QA 请求失败${status ? ` (${status})` : ''}${detail ? `：${detail}` : ''}`
}
