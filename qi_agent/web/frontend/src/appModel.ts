export type Role = 'user' | 'assistant' | 'system'
export type MessageVariant = 'default' | 'error' | 'info'

export const WS_PROTOCOL_VERSION = 1

export type TextEntry = {
  id: number
  kind: 'message'
  role: Role
  content: string
  traceId?: string | null
  time?: string
  variant?: MessageVariant
}

export type ToolResultEntry = {
  ok: boolean
  summary: string
  durationMs: number
  /** 可展开的输出预览（后端 output_preview，≤2000 字符） */
  outputPreview?: string
  outputBytes?: number
  truncated?: boolean
}

/** 一轮的 token 消耗（后端在 turn/end 里给出增量）。 */
export type TurnUsage = {
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
  /** true = 模型没返回 usage，前端按估算展示（加 "~" 前缀） */
  estimated?: boolean
}

export type ToolApprovalState = 'pending' | 'allowed' | 'denied' | 'timeout'

/** 审批的内联记录（M2-a）：挂在触发它的工具行上——弹框收起后仍可回看。 */
export type ToolApprovalEntry = {
  approvalId: string
  /** 决策档位（SEC_APPROVAL_*），前端映射成人话标签 */
  code: string
  question: string
  command: string
  /** 请求工具名（卡片上显示"谁在请求"） */
  name: string
  options: Array<{ value: string; label: string }>
  state: ToolApprovalState
  choice?: string
  decidedAt?: number
  waitedMs?: number
  /** 网关等待上限（待决卡片倒计时用它，与内核一致） */
  timeoutMs?: number
}

export type ToolEntry = {
  id: number
  kind: 'tool'
  sessionId: string
  toolCallId: string
  name: string
  toolArguments: unknown
  status: 'running' | 'blocked'
  reason?: string
  result?: ToolResultEntry
  progress: ToolProgressEntry[]
  traceId?: string | null
  time?: string
  /** M2-a：审批记录（请求时建立，决策后落地；无审批的工具行为空） */
  approval?: ToolApprovalEntry
}

export type ToolProgressEntry = {
  time: string
  text: string
}

/** 一个服务端 agent 回合的唯一 UI 容器，禁止再靠相邻消息猜测归属。 */
export type AssistantTurnEntry = {
  id: number
  kind: 'assistant-turn'
  sessionId: string
  turn: number
  body: string
  bodyState: 'streaming' | 'completed' | 'error'
  tools: ToolEntry[]
  traceId?: string | null
  error?: string
  time?: string
  /** 回合耗时（turn/end.elapsed_ms） */
  elapsedMs?: number
  /** 本轮 token 消耗（turn/end.usage） */
  usage?: TurnUsage
  /** 本轮 LLM 调用次数（turn/end.llm_calls） */
  llmCalls?: number
}

export type SubTaskStatus =
  | 'running'
  | 'completed'
  | 'failed'
  | 'need_more_info'
  | 'stopped'
  | 'timed_out'

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

export type StreamEntry = TextEntry | ToolEntry | AssistantTurnEntry | SubTaskEntry

export type SessionItem = {
  id: string
  title?: string
  updated_at?: number
  /** 会话轮数（空壳会话判断依据之一） */
  turn?: number
  /** 持久化消息数（0 = 空壳会话） */
  message_count?: number
  workspace_id?: string | null
  workspace_label?: string | null
  workspace_available?: boolean
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
  workspace_id?: string | null
  workspace_label?: string | null
  workspace_available?: boolean
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
  /** 当前上下文窗口占用（消息 + 工具 schema 估算） */
  context_tokens?: number
  context_estimated?: boolean
  /** 会话累计消耗（真实 usage，无则为 0） */
  session_tokens?: number
  session_estimated?: boolean
  session_completion_tokens?: number
  /** 占用构成：系统提示 / 工具 schema / 历史 / 工具输出 / 压缩摘要 / 当前输入 */
  breakdown?: Record<string, number>
  percent?: number
  warn_at?: number
  compact_at?: number
  // ── 旧字段（兼容） ──
  prompt_tokens: number
  completion_tokens: number
  total_tokens?: number
  est_ratio?: number | boolean
  context_limit: number
}

export type TurnEndPayload = {
  session_id?: string
  turn?: number
  reason?: string
  error?: string
  trace_id?: string
  elapsed_ms?: number
  tool_calls?: number
  tool_errors?: number
  blocked_count?: number
  llm_calls?: number
  usage?: TurnUsage
}

export type ErrorLike = {
  message?: string
}

export type ToolCallPayload = {
  session_id?: string
  turn?: number
  tool_call_id?: string
  name?: string
  arguments?: unknown
  status?: 'running' | 'blocked'
  reason?: string
}

export type ToolResultPayload = {
  session_id?: string
  turn?: number
  tool_call_id?: string
  name?: string
  ok?: boolean
  summary?: string
  duration_ms?: number
  output_preview?: string
  output_bytes?: number
  truncated?: boolean
  trace_id?: string
}

export type ToolProgressPayload = {
  session_id?: string
  turn?: number
  tool_call_id?: string
  text?: string
}

export type SessionStatusResponse = {
  session_id: string
  status: string
  turn?: number
  messages?: number
  result?: unknown
  error?: unknown
}

export type SessionTraceResponse = {
  trace_id: string | null
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
