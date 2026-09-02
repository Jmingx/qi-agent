export type Role = 'user' | 'assistant' | 'system'
export type MessageVariant = 'default' | 'error' | 'info'

export const WS_PROTOCOL_VERSION = 1

export type TextEntry = {
  id: number
  kind: 'message'
  role: Role
  content: string
  time?: string
  variant?: MessageVariant
}

export type ToolResultEntry = {
  ok: boolean
  summary: string
  durationMs: number
}

export type ToolEntry = {
  id: number
  kind: 'tool'
  sessionId: string
  name: string
  toolArguments: unknown
  status: 'running' | 'blocked'
  reason?: string
  result?: ToolResultEntry
  time?: string
}

export type SubTaskStatus = 'running' | 'completed' | 'failed' | 'timed_out'

export type SubTaskProgressEntry = {
  time: string
  text: string
}

export type SubTaskEntry = {
  id: number
  kind: 'subtask'
  sessionId: string
  subId: string
  goal: string
  status: SubTaskStatus
  progress: SubTaskProgressEntry[]
  resultText?: string
  reason?: string
  expanded: boolean
  timedOut: boolean
  startedAtMs: number
  time?: string
}

export type StreamEntry = TextEntry | ToolEntry | SubTaskEntry

export type SessionItem = {
  id: string
  title?: string
  updated_at?: number
}

export type HistoryMessage = {
  role: string
  content: string
  time?: string
}

export type HistoryPage = {
  total: number
  messages: HistoryMessage[]
}

export type SessionCreateResponse = {
  session_id: string
}

export type SessionResumeResponse = {
  session_id: string
}

export type SessionListResponse = {
  sessions: SessionItem[]
}

export type SessionSearchResult = {
  session_id: string
  title?: string
  role?: string
  content: string
  time?: string
}

export type SessionSearchResponse = {
  results: SessionSearchResult[]
}

export type ContextUsageResponse = {
  prompt_tokens: number
  completion_tokens: number
  total_tokens?: number
  est_ratio?: number | boolean
  context_limit: number
}

export type ErrorLike = {
  message?: string
}

export type ToolCallPayload = {
  session_id?: string
  name?: string
  arguments?: unknown
  status?: 'running' | 'blocked'
  reason?: string
}

export type ToolResultPayload = {
  session_id?: string
  name?: string
  ok?: boolean
  summary?: string
  duration_ms?: number
}

export type SessionStatusResponse = {
  session_id: string
  status: string
  turn?: number
  messages?: number
  result?: unknown
  error?: unknown
}

export type DelegateAsyncResponse = {
  sub_id: string
  status: 'spawned'
}

export type PendingScrollTarget = {
  sessionId: string
  query: string
  content: string
} | null

export const SESSION_STORAGE_KEY = 'qi_session_id'
export const HISTORY_PAGE_SIZE = 100
export const USAGE_POLL_INTERVAL_MS = 10_000
export const SUBTASK_POLL_INTERVAL_MS = 2000
export const SUBTASK_TIMEOUT_MS = 60_000

export function now(): string {
  return new Date().toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function readSessionId(): string {
  return window.localStorage.getItem(SESSION_STORAGE_KEY)?.trim() ?? ''
}

export function toMessage(raw: HistoryMessage): TextEntry {
  const role: Role = raw.role === 'assistant' || raw.role === 'user' || raw.role === 'system'
    ? raw.role
    : 'system'
  return {
    id: 0,
    kind: 'message',
    role,
    content: String(raw.content ?? ''),
    time: raw.time,
  }
}

export function getErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message
  }
  if (typeof error === 'string') {
    return error
  }
  const maybeError = error as ErrorLike | undefined
  return maybeError?.message ?? '未知错误'
}

export function normalizeToolArguments(value: unknown): unknown {
  return value ?? {}
}

export function stringifyValue(value: unknown): string {
  if (typeof value === 'string') {
    return value
  }
  try {
    return JSON.stringify(value ?? {}, null, 2)
  } catch {
    return String(value ?? '')
  }
}

export function commandFromInput(text: string): { name: string; args: string } | null {
  const trimmed = text.trim()
  if (!trimmed.startsWith('/')) {
    return null
  }

  const withoutSlash = trimmed.slice(1)
  if (!withoutSlash.trim()) {
    return null
  }
  const spaceIndex = withoutSlash.search(/\s/)
  if (spaceIndex === -1) {
    return { name: withoutSlash.toLowerCase(), args: '' }
  }

  return {
    name: withoutSlash.slice(0, spaceIndex).toLowerCase(),
    args: withoutSlash.slice(spaceIndex).trim(),
  }
}

export function normalizeSearchText(value: string): string {
  return value.replace(/\s+/g, ' ').trim().toLowerCase()
}

export function formatTokenCount(tokens: number): string {
  if (tokens >= 1000) {
    const value = tokens / 1000
    const formatted = value >= 100 ? value.toFixed(0) : value.toFixed(1)
    return `${formatted.replace(/\.0$/, '')}k`
  }
  return String(tokens)
}
