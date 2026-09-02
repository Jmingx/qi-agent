import { useEffect, useRef, useState } from 'react'
import { ConnectionStatus, WsClient } from './ws'
import { ToolCard } from './components/ToolCard'
import { CommandPalette } from './components/CommandPalette'
import { SubTaskCard } from './components/SubTaskCard'
import { LoginPage } from './components/LoginPage'
import { SearchPanel } from './components/SearchPanel'

type Role = 'user' | 'assistant' | 'system'
type ThemeMode = 'light' | 'dark' | 'system'
type MessageVariant = 'default' | 'error' | 'info'

type TextEntry = {
  id: number
  kind: 'message'
  role: Role
  content: string
  time?: string
  variant?: MessageVariant
}

type ToolResultEntry = {
  ok: boolean
  summary: string
  durationMs: number
}

type ToolEntry = {
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

type StreamEntry = TextEntry | ToolEntry | SubTaskEntry

type SessionItem = {
  id: string
  title?: string
  updated_at?: number
}

type HistoryMessage = {
  role: string
  content: string
  time?: string
}

type HistoryPage = {
  total: number
  messages: HistoryMessage[]
}

type SessionCreateResponse = {
  session_id: string
}

type SessionResumeResponse = {
  session_id: string
}

type SessionListResponse = {
  sessions: SessionItem[]
}

type SessionSearchResult = {
  session_id: string
  title?: string
  role?: string
  content: string
  time?: string
}

type SessionSearchResponse = {
  results: SessionSearchResult[]
}

type ContextUsageResponse = {
  prompt_tokens: number
  completion_tokens: number
  total_tokens?: number
  est_ratio?: number | boolean
  context_limit: number
}

type ErrorLike = {
  message?: string
}

type ToolCallPayload = {
  session_id?: string
  name?: string
  arguments?: unknown
  status?: 'running' | 'blocked'
  reason?: string
}

type ToolResultPayload = {
  session_id?: string
  name?: string
  ok?: boolean
  summary?: string
  duration_ms?: number
}

type SubTaskStatus = 'running' | 'completed' | 'failed' | 'timed_out'

type SubTaskEntry = {
  id: number
  kind: 'subtask'
  sessionId: string
  subId: string
  goal: string
  status: SubTaskStatus
  resultText?: string
  reason?: string
  expanded?: boolean
  timedOut?: boolean
  time?: string
}

type SessionStatusResponse = {
  session_id: string
  status: string
  turn?: number
  messages?: number
  result?: unknown
  error?: unknown
}

type DelegateAsyncResponse = {
  sub_id: string
  status: 'spawned'
}

type CommandName =
  | '/new'
  | '/resume'
  | '/clear'
  | '/help'
  | '/delegate'
  | '/memory'
  | '/compact'
  | '/stop'
  | '/status'

type CommandDefinition = {
  name: CommandName
  label: string
  description: string
}

const COMMANDS: CommandDefinition[] = [
  { name: '/new', label: '/new', description: '新建会话' },
  { name: '/resume', label: '/resume', description: '打开会话列表' },
  { name: '/clear', label: '/clear', description: '清空当前上下文' },
  { name: '/help', label: '/help', description: '显示命令帮助' },
  { name: '/delegate', label: '/delegate', description: '发起子任务' },
  { name: '/memory', label: '/memory', description: '打开记忆弹窗' },
  { name: '/compact', label: '/compact', description: '压缩当前上下文' },
  { name: '/stop', label: '/stop', description: '停止当前会话' },
  { name: '/status', label: '/status', description: '查看会话状态' },
]

const SESSION_STORAGE_KEY = 'qi_session_id'
const AUTH_TOKEN_STORAGE_KEY = 'qi_web_token'
const THEME_STORAGE_KEY = 'qi_theme'
const HISTORY_PAGE_SIZE = 100
const USAGE_POLL_INTERVAL_MS = 10_000
const SUBTASK_POLL_INTERVAL_MS = 2000
const SUBTASK_TIMEOUT_MS = 60_000

function now(): string {
  return new Date().toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  })
}

function readThemeMode(): ThemeMode {
  const value = window.localStorage.getItem(THEME_STORAGE_KEY)
  if (value === 'light' || value === 'dark' || value === 'system') {
    return value
  }
  return 'system'
}

function readSessionId(): string {
  return window.localStorage.getItem(SESSION_STORAGE_KEY)?.trim() ?? ''
}

function readAuthToken(): string | null {
  const value = window.localStorage.getItem(AUTH_TOKEN_STORAGE_KEY)
  return value === null ? null : value
}

function isLoopbackHost(hostname: string): boolean {
  return hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '::1'
    || hostname.startsWith('127.')
}

function resolveTheme(mode: ThemeMode): 'light' | 'dark' {
  if (mode === 'light' || mode === 'dark') {
    return mode
  }
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function toMessage(raw: HistoryMessage): TextEntry {
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

function getErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message
  }
  if (typeof error === 'string') {
    return error
  }
  const maybeError = error as ErrorLike | undefined
  return maybeError?.message ?? '未知错误'
}

function normalizeToolArguments(value: unknown): unknown {
  return value ?? {}
}

function stringifyValue(value: unknown): string {
  if (typeof value === 'string') {
    return value
  }
  try {
    return JSON.stringify(value ?? {}, null, 2)
  } catch {
    return String(value ?? '')
  }
}

function commandFromInput(text: string): { name: string; args: string } | null {
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

function normalizeSearchText(value: string): string {
  return value.replace(/\s+/g, ' ').trim().toLowerCase()
}

function formatTokenCount(tokens: number): string {
  if (tokens >= 1000) {
    const value = tokens / 1000
    const formatted = value >= 100 ? value.toFixed(0) : value.toFixed(1)
    return `${formatted.replace(/\.0$/, '')}k`
  }
  return String(tokens)
}

export default function App() {
  const [authToken, setAuthToken] = useState<string | null>(() => readAuthToken())
  const [authBusy, setAuthBusy] = useState(false)
  const [authError, setAuthError] = useState<string | null>(null)
  const [entries, setEntries] = useState<StreamEntry[]>([])
  const [input, setInput] = useState('')
  const [sessionId, setSessionId] = useState(() => readSessionId())
  const [sessions, setSessions] = useState<SessionItem[]>([])
  const [connectionState, setConnectionState] = useState<ConnectionStatus>('disconnected')
  const [running, setRunning] = useState(false)
  const [approval, setApproval] = useState<Record<string, unknown> | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [memoryOpen, setMemoryOpen] = useState(false)
  const [memoryText, setMemoryText] = useState('')
  const [themeMode, setThemeMode] = useState<ThemeMode>(() => readThemeMode())
  const [toast, setToast] = useState<string | null>(null)
  const [commandPaletteOpen, setCommandPaletteOpen] = useState(false)
  const [usage, setUsage] = useState<ContextUsageResponse | null>(null)
  const [searchOpen, setSearchOpen] = useState(true)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<SessionSearchResult[]>([])
  const [searchLoading, setSearchLoading] = useState(false)
  const [highlightedMessageId, setHighlightedMessageId] = useState<number | null>(null)

  const clientRef = useRef<WsClient | null>(null)
  const entriesRef = useRef<StreamEntry[]>([])
  const sessionIdRef = useRef(sessionId)
  const connectionStateRef = useRef<ConnectionStatus>('disconnected')
  const bootstrappedRef = useRef(false)
  const bootstrapInFlightRef = useRef(false)
  const loadingSessionRef = useRef(false)
  const streamingRef = useRef<{ id: number; turn: number; full: string } | null>(null)
  const currentTurnRef = useRef(0)
  const currentTurnAssistantSeenRef = useRef(false)
  const nextIdRef = useRef(1)
  const inputRef = useRef<HTMLInputElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const toastTimerRef = useRef<number | null>(null)
  const highlightTimerRef = useRef<number | null>(null)
  const turnErrorNotifiedRef = useRef(false)
  const subTaskPollersRef = useRef(new Map<string, number>())
  const subTaskMetaRef = useRef(
    new Map<string, { lastFingerprint: string; lastChangeAt: number; entryId: number }>(),
  )
  const messageNodeRefs = useRef(new Map<number, HTMLDivElement | null>())
  const pendingScrollTargetRef = useRef<{ sessionId: string; query: string; content: string } | null>(
    null,
  )
  const searchRequestSeqRef = useRef(0)

  useEffect(() => {
    entriesRef.current = entries
  }, [entries])

  useEffect(() => {
    sessionIdRef.current = sessionId
    if (sessionId) {
      window.localStorage.setItem(SESSION_STORAGE_KEY, sessionId)
    } else {
      window.localStorage.removeItem(SESSION_STORAGE_KEY)
    }
  }, [sessionId])

  useEffect(() => {
    connectionStateRef.current = connectionState
    if (connectionState !== 'connected') {
      // 断线后先把正在进行中的发送状态收回来，避免界面继续展示“可发送”的错觉。
      setRunning(false)
      streamingRef.current = null
    }
  }, [connectionState])

  useEffect(() => {
    const root = document.documentElement
    const media = window.matchMedia('(prefers-color-scheme: dark)')

    const applyTheme = (): void => {
      const resolved = resolveTheme(themeMode)
      root.dataset.theme = resolved
      root.style.colorScheme = resolved
      window.localStorage.setItem(THEME_STORAGE_KEY, themeMode)
    }

    applyTheme()
    if (themeMode === 'system') {
      const onChange = (): void => applyTheme()
      media.addEventListener('change', onChange)
      return () => media.removeEventListener('change', onChange)
    }
    return undefined
  }, [themeMode])

  useEffect(() => {
    if (authToken === null) {
      return undefined
    }

    const client = new WsClient('/ws', () => authToken)
    clientRef.current = client
    setAuthBusy(true)
    setAuthError(null)

    client.onStatus((status) => {
      setConnectionState(status)
      if (status === 'connected') {
        // 修复（2026-09-02）：StrictMode 双执行 + 无 cleanup 会并存两个
        // WsClient——旧实例 onopen 时 clientRef.current 已指向新实例
        // （ws 还在 CONNECTING）→ call reject → unhandled rejection。
        // ① cleanup dispose 旧实例 ② 每个 void 调用补 .catch 兜底。
        void bootstrapSession().catch(() => {})
        void refreshSessions().catch(() => {})
        void refreshUsage().catch(() => {})
        if (bootstrappedRef.current && sessionIdRef.current && entriesRef.current.length === 0) {
          void restoreSession(sessionIdRef.current, { allowFallbackToCreate: true }).catch(() => {})
        }
      }
    })

    client.onNotify('item/agentMessage/delta', (params) => {
      const delta = String((params.text as string | undefined) ?? (params.delta as string | undefined) ?? '')
      if (!delta) {
        return
      }
      const turn = Number(params.turn ?? 0)
      if (turn !== currentTurnRef.current) {
        return
      }

      currentTurnAssistantSeenRef.current = true

      const existing = streamingRef.current
      if (existing && existing.turn === turn) {
        existing.full += delta
        const nextEntries = entriesRef.current.map((entry) => (
          entry.kind === 'message' && entry.id === existing.id
            ? { ...entry, content: existing.full }
            : entry
        ))
        entriesRef.current = nextEntries
        setEntries(nextEntries)
        return
      }

      const id = nextId()
      streamingRef.current = { id, turn, full: delta }
      const nextEntries: StreamEntry[] = [
        ...entriesRef.current,
        { id, kind: 'message', role: 'assistant', content: delta, time: now() },
      ]
      entriesRef.current = nextEntries
      setEntries(nextEntries)
    })

    client.onNotify('item/toolCall', (params) => {
      const payload = params as ToolCallPayload
      const targetSessionId = String(payload.session_id ?? '')
      const currentSessionId = sessionIdRef.current
      if (!targetSessionId || targetSessionId !== currentSessionId) {
        return
      }

      const nextEntries: StreamEntry[] = [
        ...entriesRef.current,
        {
          id: nextId(),
          kind: 'tool',
          sessionId: targetSessionId,
          name: String(payload.name ?? '未知工具'),
          toolArguments: normalizeToolArguments(payload.arguments),
          status: payload.status === 'blocked' ? 'blocked' : 'running',
          reason: payload.reason ? String(payload.reason) : undefined,
          time: now(),
        },
      ]
      entriesRef.current = nextEntries
      setEntries(nextEntries)
    })

    client.onNotify('item/toolResult', (params) => {
      const payload = params as ToolResultPayload
      const targetSessionId = String(payload.session_id ?? '')
      const currentSessionId = sessionIdRef.current
      if (!targetSessionId || targetSessionId !== currentSessionId) {
        return
      }

      const toolName = String(payload.name ?? '未知工具')
      const result: ToolResultEntry = {
        ok: Boolean(payload.ok),
        summary: String(payload.summary ?? ''),
        durationMs: Number(payload.duration_ms ?? 0),
      }

      const nextEntries = entriesRef.current.slice()
      for (let index = nextEntries.length - 1; index >= 0; index -= 1) {
        const entry = nextEntries[index]
        if (entry.kind === 'tool' && entry.sessionId === targetSessionId && entry.name === toolName && !entry.result) {
          nextEntries[index] = {
            ...entry,
            result,
          }
          entriesRef.current = nextEntries
          setEntries(nextEntries)
          return
        }
      }

      // 如果结果通知先丢了调用通知，也尽量把信息补进消息流，避免前端静默丢卡。
      const fallbackEntries: StreamEntry[] = [
        ...entriesRef.current,
        {
          id: nextId(),
          kind: 'tool',
          sessionId: targetSessionId,
          name: toolName,
          toolArguments: {},
          status: 'running',
          result,
          time: now(),
        },
      ]
      entriesRef.current = fallbackEntries
      setEntries(fallbackEntries)
    })

    client.onNotify('turn/end', (params) => {
      setRunning(false)
      streamingRef.current = null
      const error = String((params.error as string | undefined) ?? '')
      turnErrorNotifiedRef.current = Boolean(error)
      if (error) {
        appendSystemMessage(`内核错误：${error}`, 'error')
      }
      void refreshSessions()
    })

    client.onNotify('serverRequest/approval', (params) => {
      setApproval(params)
    })

    void client.connect()
      .then(() => {
        setAuthBusy(false)
        // 修复（2026-09-02）：bootstrapSession 的 reject 从这里冒泡曾导致
        // unhandled rejection（restoreSession 的 ref 检查误判 + 无 catch）
        void bootstrapSession().catch(() => {})
      })
      .catch((error) => {
        const message = getErrorMessage(error)
        setAuthBusy(false)
        setConnectionState('disconnected')
        setUsage(null)
        setSearchResults([])
        setSearchLoading(false)
        setHighlightedMessageId(null)
        setAuthError(message)
        window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY)
        setAuthToken(null)
      })

    return () => {
      client.dispose()
      clientRef.current = null
      if (toastTimerRef.current !== null) {
        window.clearTimeout(toastTimerRef.current)
        toastTimerRef.current = null
      }
      for (const timerId of subTaskPollersRef.current.values()) {
        window.clearTimeout(timerId)
      }
      subTaskPollersRef.current.clear()
      subTaskMetaRef.current.clear()
      if (highlightTimerRef.current !== null) {
        window.clearTimeout(highlightTimerRef.current)
        highlightTimerRef.current = null
      }
    }
  }, [authToken])

  useEffect(() => {
    const pendingTarget = pendingScrollTargetRef.current
    if (pendingTarget) {
      const normalizedQuery = normalizeSearchText(pendingTarget.query || pendingTarget.content)
      const normalizedContent = normalizeSearchText(pendingTarget.content)
      const matchedEntry = entriesRef.current.find((entry) => {
        if (entry.kind !== 'message') {
          return false
        }
        const normalizedEntry = normalizeSearchText(entry.content)
        return normalizedEntry.includes(normalizedContent) || normalizedEntry.includes(normalizedQuery)
      })
      if (matchedEntry) {
        const node = messageNodeRefs.current.get(matchedEntry.id)
        if (node) {
          node.scrollIntoView({ behavior: 'smooth', block: 'center' })
          setHighlightedMessageId(matchedEntry.id)
          if (highlightTimerRef.current !== null) {
            window.clearTimeout(highlightTimerRef.current)
          }
          highlightTimerRef.current = window.setTimeout(() => {
            setHighlightedMessageId((current) => (
              current === matchedEntry.id ? null : current
            ))
            highlightTimerRef.current = null
          }, 1800)
          pendingScrollTargetRef.current = null
          return
        }
      }
    }

    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [entries])

  useEffect(() => {
    const client = clientRef.current
    const query = searchQuery.trim()
    if (!query) {
      setSearchResults([])
      setSearchLoading(false)
      return undefined
    }
    if (!client || connectionStateRef.current !== 'connected' || authToken === null) {
      setSearchLoading(false)
      return undefined
    }

    let cancelled = false
    const timerId = window.setTimeout(() => {
      setSearchLoading(true)
      void client.call<SessionSearchResponse>('session/search', { query })
        .then((response) => {
          if (!cancelled) {
            setSearchResults(response.results ?? [])
          }
        })
        .catch(() => {
          if (!cancelled) {
            setSearchResults([])
          }
        })
        .finally(() => {
          if (!cancelled) {
            setSearchLoading(false)
          }
        })
    }, 300)

    return () => {
      cancelled = true
      window.clearTimeout(timerId)
    }
  }, [searchQuery, authToken, connectionState])

  function nextId(): number {
    nextIdRef.current += 1
    return nextIdRef.current
  }

  function setStreamEntries(next: StreamEntry[]): void {
    entriesRef.current = next
    setEntries(next)
  }

  function appendMessage(role: Role, content: string, variant: MessageVariant = 'default'): void {
    const next: StreamEntry[] = [
      ...entriesRef.current,
      { id: nextId(), kind: 'message', role, content, time: now(), variant },
    ]
    setStreamEntries(next)
  }

  function appendSystemMessage(content: string, variant: MessageVariant = 'default'): void {
    appendMessage('system', content, variant)
  }

  function showToast(message: string): void {
    setToast(message)
    if (toastTimerRef.current !== null) {
      window.clearTimeout(toastTimerRef.current)
    }
    toastTimerRef.current = window.setTimeout(() => {
      setToast(null)
      toastTimerRef.current = null
    }, 2800)
  }

  function closeCommandPalette(): void {
    setCommandPaletteOpen(false)
  }

  function updateEntriesById(entryId: number, updater: (entry: StreamEntry) => StreamEntry): void {
    const nextEntries = entriesRef.current.map((entry) => (
      entry.id === entryId ? updater(entry) : entry
    ))
    setStreamEntries(nextEntries)
  }

  function updateSubTaskEntry(subId: string, updater: (entry: SubTaskEntry) => SubTaskEntry): void {
    const nextEntries = entriesRef.current.map((entry) => (
      entry.kind === 'subtask' && entry.subId === subId ? updater(entry) : entry
    ))
    setStreamEntries(nextEntries)
  }

  function clearSubTaskPoll(subId: string): void {
    const timerId = subTaskPollersRef.current.get(subId)
    if (timerId !== undefined) {
      window.clearTimeout(timerId)
      subTaskPollersRef.current.delete(subId)
    }
    subTaskMetaRef.current.delete(subId)
  }

  function scheduleSubTaskPoll(subId: string): void {
    clearSubTaskPoll(subId)
    const timerId = window.setTimeout(() => {
      void pollSubTask(subId)
    }, SUBTASK_POLL_INTERVAL_MS)
    subTaskPollersRef.current.set(subId, timerId)
  }

  async function pollSubTask(subId: string): Promise<void> {
    // 诊断日志（2026-09-02：排查"子任务轮询未发出"——每分支打点）
    console.debug('[subtask] poll', subId)
    const client = clientRef.current
    const meta = subTaskMetaRef.current.get(subId)
    if (!meta) {
      console.debug('[subtask] meta missing, skip', subId)
      return
    }

    if (!client || connectionStateRef.current !== 'connected') {
      console.debug('[subtask] conn not ready', subId, connectionStateRef.current)
      if (Date.now() - meta.lastChangeAt >= SUBTASK_TIMEOUT_MS) {
        updateSubTaskEntry(subId, (entry) => ({
          ...entry,
          status: 'timed_out',
          timedOut: true,
          reason: entry.reason ?? '子任务轮询超时',
        }))
        showToast('子任务 60 秒无变化，已停止轮询')
        clearSubTaskPoll(subId)
        return
      }
      scheduleSubTaskPoll(subId)
      return
    }

    try {
      console.debug('[subtask] calling session/status', subId)
      const response = await client.call<SessionStatusResponse>('session/status', { session_id: subId })
      console.debug('[subtask] status resp', subId, response.status)
      const status = String(response.status ?? '').toLowerCase()
      const fingerprint = stringifyValue({
        status,
        result: response.result,
        error: response.error,
      })
      const nowMs = Date.now()
      const changed = fingerprint !== meta.lastFingerprint
      const nextMeta = changed
        ? { ...meta, lastFingerprint: fingerprint, lastChangeAt: nowMs }
        : meta
      subTaskMetaRef.current.set(subId, nextMeta)

      if (status === 'completed' || status === 'failed') {
        updateSubTaskEntry(subId, (entry) => ({
          ...entry,
          status: status === 'completed' ? 'completed' : 'failed',
          resultText: status === 'completed' ? stringifyValue(response.result) : entry.resultText,
          reason: status === 'failed'
            ? stringifyValue(response.error ?? response.result ?? '子任务执行失败')
            : entry.reason,
          timedOut: false,
        }))
        clearSubTaskPoll(subId)
        return
      }

      if (nowMs - nextMeta.lastChangeAt >= SUBTASK_TIMEOUT_MS) {
        updateSubTaskEntry(subId, (entry) => ({
          ...entry,
          status: 'timed_out',
          timedOut: true,
          reason: entry.reason ?? '子任务 60 秒无变化，已停止轮询',
        }))
        showToast('子任务 60 秒无变化，已停止轮询')
        appendSystemMessage(`子任务 ${subId.slice(0, 8)} 60 秒无变化，已停止轮询`, 'info')
        clearSubTaskPoll(subId)
        return
      }
    } catch (error) {
      console.debug('[subtask] poll error', subId, getErrorMessage(error))
      const nextMeta = subTaskMetaRef.current.get(subId)
      if (!nextMeta) {
        return
      }
      if (Date.now() - nextMeta.lastChangeAt >= SUBTASK_TIMEOUT_MS) {
        updateSubTaskEntry(subId, (entry) => ({
          ...entry,
          status: 'timed_out',
          timedOut: true,
          reason: entry.reason ?? getErrorMessage(error),
        }))
        showToast('子任务 60 秒无变化，已停止轮询')
        clearSubTaskPoll(subId)
        return
      }
    }

    scheduleSubTaskPoll(subId)
  }

  function appendSubTask(goal: string, subId: string): void {
    const entryId = nextId()
    const nextEntries: StreamEntry[] = [
      ...entriesRef.current,
      {
        id: entryId,
        kind: 'subtask',
        sessionId: sessionIdRef.current,
        subId,
        goal,
        status: 'running',
        expanded: false,
        time: now(),
      },
    ]
    setStreamEntries(nextEntries)
    subTaskMetaRef.current.set(subId, {
      lastFingerprint: '',
      lastChangeAt: Date.now(),
      entryId,
    })
    scheduleSubTaskPoll(subId)
  }

  function openCommandInput(): void {
    setCommandPaletteOpen(true)
    inputRef.current?.focus()
  }

  function showCommandHelp(): void {
    appendSystemMessage(
      [
        '可用命令：',
        '/new 新建会话',
        '/resume 打开会话列表',
        '/clear 清空当前上下文',
        '/help 显示命令帮助',
        '/delegate 发起子任务，格式：/delegate 目标',
        '/memory 打开记忆弹窗',
        '/compact 压缩当前上下文',
        '/stop 停止当前会话',
        '/status 显示会话状态',
      ].join('\n'),
      'info',
    )
  }

  async function showCurrentSessionStatus(): Promise<void> {
    if (!sessionId || !ensureConnected('查看会话状态')) {
      return
    }
    const client = clientRef.current
    if (!client) {
      return
    }
    try {
      const response = await client.call<SessionStatusResponse>('session/status', {
        session_id: sessionId,
      })
      appendSystemMessage(
        [
          `会话状态：${response.status || 'unknown'}`,
          `会话 ID：${response.session_id || sessionId}`,
          `消息数：${entriesRef.current.length}`,
          response.result ? `结果：${stringifyValue(response.result).slice(0, 300)}` : '结果：无',
          response.error ? `错误：${stringifyValue(response.error).slice(0, 300)}` : '错误：无',
        ].join('\n'),
        'info',
      )
    } catch (error) {
      const message = getErrorMessage(error)
      showToast(`查看状态失败：${message}`)
      appendSystemMessage(`查看状态失败：${message}`, 'error')
    }
  }

  function shouldTreatAsCommand(text: string): boolean {
    return text.trimStart().startsWith('/')
  }

  async function startDelegate(goal: string): Promise<void> {
    if (!sessionId || !ensureConnected('发起子任务')) {
      return
    }
    const client = clientRef.current
    if (!client) {
      return
    }
    const cleanedGoal = goal.trim()
    if (!cleanedGoal) {
      showToast('请输入子任务目标')
      setInput('/delegate ')
      openCommandInput()
      return
    }

    try {
      const response = await client.call<DelegateAsyncResponse>('session/delegate_async', {
        session_id: sessionId,
        goal: cleanedGoal,
      })
      appendSubTask(cleanedGoal, response.sub_id)
      showToast(`子任务已派发：${cleanedGoal}`)
    } catch (error) {
      const message = getErrorMessage(error)
      showToast(`子任务派发失败：${message}`)
      appendSystemMessage(`子任务派发失败：${message}`, 'error')
    }
  }

  async function executeCommand(commandText: string): Promise<boolean> {
    const parsed = commandFromInput(commandText)
    if (!parsed) {
      return false
    }

    const command = `/${parsed.name}` as CommandName
    const args = parsed.args

    if (command === '/delegate') {
      await startDelegate(args)
      return true
    }

    switch (command) {
      case '/new':
        await newSession()
        return true
      case '/resume':
        setSidebarOpen(true)
        void refreshSessions()
        return true
      case '/clear':
        await clearCurrentSession()
        return true
      case '/help':
        showCommandHelp()
        return true
      case '/memory':
        await openMemory()
        return true
      case '/compact':
        await compact()
        return true
      case '/stop':
        await stop()
        return true
      case '/status':
        await showCurrentSessionStatus()
        return true
      default:
        showToast(`未知命令：${commandText}`)
        appendSystemMessage(`未知命令：${commandText}`, 'error')
        return true
    }
  }

  async function handleCommandSelect(command: CommandName): Promise<void> {
    closeCommandPalette()
    if (command === '/delegate') {
      setInput('/delegate ')
      inputRef.current?.focus()
      return
    }
    await executeCommand(command)
    inputRef.current?.focus()
  }

  function ensureConnected(actionName: string): boolean {
    if (connectionStateRef.current === 'connected') {
      return true
    }
    showToast(`${actionName} 需要先连接 WebSocket`)
    return false
  }

  async function refreshSessions(): Promise<void> {
    const client = clientRef.current
    if (!client || connectionStateRef.current !== 'connected') {
      return
    }
    try {
      const response = await client.call<SessionListResponse>('session/list')
      setSessions(response.sessions ?? [])
    } catch {
      // 侧边栏是辅助能力，刷新失败不应该打断主聊天流程。
    }
  }

  async function refreshUsage(targetSessionId?: string): Promise<void> {
    const client = clientRef.current
    const activeSessionId = targetSessionId ?? sessionIdRef.current
    if (!client || connectionStateRef.current !== 'connected' || !activeSessionId) {
      setUsage(null)
      return
    }
    try {
      const response = await client.call<ContextUsageResponse>('context/usage', {
        session_id: activeSessionId,
      })
      setUsage(response)
    } catch {
      // usage 只是顶部展示，不应影响主对话流程。
    }
  }

  function resetWorkspaceState(): void {
    setEntries([])
    setSessions([])
    setInput('')
    setRunning(false)
    setApproval(null)
    setSidebarOpen(false)
    setMemoryOpen(false)
    setMemoryText('')
    setCommandPaletteOpen(false)
    setUsage(null)
    setSearchQuery('')
    setSearchResults([])
    setSearchLoading(false)
    setHighlightedMessageId(null)
    setAuthBusy(false)
    setConnectionState('disconnected')
    bootstrappedRef.current = false
    bootstrapInFlightRef.current = false
    loadingSessionRef.current = false
    streamingRef.current = null
    currentTurnRef.current = 0
    currentTurnAssistantSeenRef.current = false
    turnErrorNotifiedRef.current = false
    pendingScrollTargetRef.current = null
  }

  function handleLogin(nextToken: string): void {
    const trimmed = nextToken.trim()
    resetWorkspaceState()
    setAuthError(null)
    setAuthBusy(true)
    if (trimmed) {
      window.localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, trimmed)
    } else {
      window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY)
    }
    setAuthToken(trimmed)
  }

  async function loadHistory(session: string): Promise<StreamEntry[]> {
    const client = clientRef.current
    if (!client) {
      throw new Error('WebSocket 未连接')
    }

    const collected: StreamEntry[] = []
    let offset = 0
    let total = Number.POSITIVE_INFINITY

    while (offset < total) {
      const page = await client.call<HistoryPage>('context/history', {
        session_id: session,
        offset,
        limit: HISTORY_PAGE_SIZE,
      })
      total = Number(page.total ?? 0)
      const chunk = (page.messages ?? []).map((message) => ({
        ...toMessage(message),
        id: nextId(),
      }))
      collected.push(...chunk)
      if (chunk.length === 0) {
        break
      }
      offset += chunk.length
    }

    return collected
  }

  async function createFreshSession(goal = 'web 会话'): Promise<void> {
    const client = clientRef.current
    if (!client || connectionStateRef.current !== 'connected') {
      throw new Error('WebSocket 未连接')
    }
    const response = await client.call<SessionCreateResponse>('session/create', { goal })
    setSessionId(response.session_id)
    setRunning(false)
    streamingRef.current = null
    currentTurnAssistantSeenRef.current = false
    turnErrorNotifiedRef.current = false
    setApproval(null)
    setStreamEntries([])
    setSidebarOpen(false)
    setUsage(null)
    pendingScrollTargetRef.current = null
    bootstrappedRef.current = true
    await refreshSessions()
    await refreshUsage(response.session_id)
  }

  async function restoreSession(
    targetSessionId: string,
    options: {
      allowFallbackToCreate: boolean
      focusSearch?: { query: string; content: string }
    },
  ): Promise<void> {
    const client = clientRef.current
    if (!client) {
      throw new Error('客户端未初始化')
    }
    // 修复（2026-09-02）：不再用 connectionStateRef 检查（connect().then
    // 时序下 ref 尚未被 useEffect 同步——误判未连接 → 恢复会话必失败）。
    // ws 就绪与否交给 client.call 自己判断（this.ws.readyState）。

    if (loadingSessionRef.current) {
      return
    }

    loadingSessionRef.current = true
    try {
      const loadingNote = targetSessionId
        ? `正在恢复会话 ${targetSessionId.slice(0, 8)}...`
        : '正在恢复会话...'
      setStreamEntries([
        { id: nextId(), kind: 'message', role: 'system', content: loadingNote, time: now(), variant: 'info' },
      ])

      await client.call<SessionResumeResponse>('session/resume', { session_id: targetSessionId })
      const history = await loadHistory(targetSessionId)
      setSessionId(targetSessionId)
      setApproval(null)
      setRunning(false)
      streamingRef.current = null
      currentTurnAssistantSeenRef.current = false
      turnErrorNotifiedRef.current = false
      setUsage(null)
      setStreamEntries(history)
      setSidebarOpen(false)
      pendingScrollTargetRef.current = options.focusSearch
        ? { sessionId: targetSessionId, query: options.focusSearch.query, content: options.focusSearch.content }
        : null
      await refreshSessions()
      await refreshUsage(targetSessionId)
      bootstrappedRef.current = true
    } catch (error) {
      // 修复（2026-09-02）：不再依赖 connectionStateRef（时序滞后误判）——
      // 只要允许 fallback 就尝试自动新建（会话不存在/连接瞬断都走这里，
      // 总比卡在无会话状态好；真正断网时 call 会 reject，由调用方 .catch 兜底）
      if (!options.allowFallbackToCreate) {
        throw error
      }
      const message = getErrorMessage(error)
      showToast(`恢复会话失败，已自动新建：${message}`)
      await createFreshSession('web 会话')
      appendSystemMessage(`恢复会话失败，已自动新建：${message}`, 'error')
      bootstrappedRef.current = true
    } finally {
      loadingSessionRef.current = false
    }
  }

  async function bootstrapSession(): Promise<void> {
    if (bootstrappedRef.current || bootstrapInFlightRef.current) {
      return
    }
    bootstrapInFlightRef.current = true
    try {
      const storedSessionId = readSessionId()
      if (storedSessionId) {
        await restoreSession(storedSessionId, { allowFallbackToCreate: true })
      } else {
        await createFreshSession('web 会话')
        bootstrappedRef.current = true
      }
      await refreshSessions()
    } catch {
      // 兜底（2026-09-02）：bootstrap 失败不抛（避免 unhandled rejection），
      // 用户后续手动操作（发消息/新会话）会重新走会话创建路径
    } finally {
      bootstrapInFlightRef.current = false
    }
  }

  async function send(): Promise<void> {
    const text = input.trim()
    if (!text) {
      return
    }
    if (text === '/') {
      return
    }
    if (shouldTreatAsCommand(text)) {
      setInput('')
      closeCommandPalette()
      await executeCommand(text)
      return
    }
    if (!sessionId) {
      return
    }
    if (connectionStateRef.current !== 'connected') {
      showToast('当前断开，等重连成功后再发送')
      return
    }
    const client = clientRef.current
    if (!client) {
      showToast('当前连接不可用')
      return
    }
    if (running) {
      return
    }

    setInput('')
    streamingRef.current = null
    currentTurnRef.current += 1
    currentTurnAssistantSeenRef.current = false
    turnErrorNotifiedRef.current = false

    const nextEntries: StreamEntry[] = [
      ...entriesRef.current,
      { id: nextId(), kind: 'message', role: 'user', content: text, time: now() },
    ]
    setStreamEntries(nextEntries)
    setRunning(true)

    try {
      const response = await client.call<{ reply: string }>('message/send', {
        session_id: sessionId,
        text,
      })
      if (response.reply) {
        window.setTimeout(() => {
          if (!currentTurnAssistantSeenRef.current) {
            appendMessage('assistant', response.reply)
            currentTurnAssistantSeenRef.current = true
          }
        }, 250)
      }
    } catch (error) {
      const message = getErrorMessage(error)
      showToast(`消息发送失败：${message}`)
      if (!turnErrorNotifiedRef.current) {
        appendSystemMessage(`RPC 调用失败：${message}`, 'error')
      }
    } finally {
      setRunning(false)
      turnErrorNotifiedRef.current = false
      void refreshUsage()
    }
  }

  async function switchSession(
    targetSessionId: string,
    focusSearch?: { query: string; content: string },
  ): Promise<void> {
    if (!targetSessionId || running) {
      return
    }
    if (!ensureConnected('切换会话')) {
      return
    }
    setSidebarOpen(false)
    try {
      await restoreSession(targetSessionId, {
        allowFallbackToCreate: true,
        focusSearch,
      })
    } catch (error) {
      const message = getErrorMessage(error)
      showToast(`切换会话失败：${message}`)
      appendSystemMessage(`切换会话失败：${message}`, 'error')
    }
  }

  async function newSession(): Promise<void> {
    if (!ensureConnected('新建会话')) {
      return
    }
    setSidebarOpen(false)
    try {
      await createFreshSession('web 会话')
      showToast('已新建会话')
    } catch (error) {
      const message = getErrorMessage(error)
      showToast(`新建会话失败：${message}`)
      appendSystemMessage(`新建会话失败：${message}`, 'error')
    }
  }

  async function deleteSession(targetSessionId: string): Promise<void> {
    if (!targetSessionId || !ensureConnected('删除会话')) {
      return
    }
    const label = sessions.find((item) => item.id === targetSessionId)?.title || targetSessionId
    if (!window.confirm(`确定删除会话「${label}」吗？此操作无法撤销。`)) {
      return
    }
    const client = clientRef.current
    if (!client) {
      return
    }
    try {
      await client.call('session/delete', { session_id: targetSessionId })
      const deletingCurrent = targetSessionId === sessionIdRef.current
      await refreshSessions()
      if (!deletingCurrent) {
        await refreshUsage(sessionIdRef.current)
      }
      if (deletingCurrent) {
        setApproval(null)
        setRunning(false)
        streamingRef.current = null
        currentTurnAssistantSeenRef.current = false
        turnErrorNotifiedRef.current = false
        setStreamEntries([])
        setUsage(null)
        await createFreshSession('web 会话')
      }
      showToast('会话已删除')
    } catch (error) {
      const message = getErrorMessage(error)
      showToast(`删除会话失败：${message}`)
      appendSystemMessage(`删除会话失败：${message}`, 'error')
    }
  }

  async function clearCurrentSession(): Promise<void> {
    if (!sessionId || !ensureConnected('清空当前会话')) {
      return
    }
    if (!window.confirm('确定清空当前会话吗？这会删除当前上下文里的消息。')) {
      return
    }
    const client = clientRef.current
    if (!client) {
      return
    }
    try {
      await client.call('context/clear', { session_id: sessionId })
      setApproval(null)
      setRunning(false)
      streamingRef.current = null
      currentTurnAssistantSeenRef.current = false
      turnErrorNotifiedRef.current = false
      currentTurnRef.current += 1
      setStreamEntries([])
      setUsage(null)
      showToast('当前会话已清空')
      await refreshSessions()
      await refreshUsage(sessionId)
    } catch (error) {
      const message = getErrorMessage(error)
      showToast(`清空会话失败：${message}`)
      appendSystemMessage(`清空会话失败：${message}`, 'error')
    }
  }

  async function stop(): Promise<void> {
    if (!sessionId || !ensureConnected('停止运行')) {
      return
    }
    const client = clientRef.current
    if (!client) {
      return
    }
    try {
      await client.call('session/stop', { session_id: sessionId })
      appendSystemMessage('已发送停止请求')
      setRunning(false)
    } catch (error) {
      const message = getErrorMessage(error)
      showToast(`停止失败：${message}`)
      appendSystemMessage(`停止失败：${message}`, 'error')
    }
  }

  async function openMemory(): Promise<void> {
    if (!ensureConnected('打开记忆面板')) {
      return
    }
    const client = clientRef.current
    if (!client) {
      return
    }
    try {
      const response = await client.call<{ memory: string }>('memory/get')
      setMemoryText(response.memory || '(空)')
    } catch (error) {
      const message = getErrorMessage(error)
      setMemoryText(`读取失败：${message}`)
      showToast(`记忆读取失败：${message}`)
      appendSystemMessage(`记忆读取失败：${message}`, 'error')
    } finally {
      setMemoryOpen(true)
    }
  }

  async function compact(): Promise<void> {
    if (!sessionId || !ensureConnected('压缩上下文')) {
      return
    }
    const client = clientRef.current
    if (!client) {
      return
    }
    try {
      const response = await client.call<{ ok: boolean; summary?: string; before?: number }>(
        'context/compact',
        { session_id: sessionId },
      )
      appendSystemMessage(
        `压缩完成（${response.before ?? 0} 条消息）：${response.summary || ''}`,
      )
    } catch (error) {
      const message = getErrorMessage(error)
      showToast(`压缩失败：${message}`)
      appendSystemMessage(`压缩失败：${message}`, 'error')
    }
  }

  async function respondApproval(decision: 'approve' | 'deny'): Promise<void> {
    if (!approval || !sessionId || !ensureConnected('处理审批')) {
      return
    }
    const client = clientRef.current
    if (!client) {
      return
    }
    try {
      await client.call('approval/respond', {
        session_id: sessionId,
        approval_id: approval.approval_id,
        decision,
      })
      setApproval(null)
    } catch (error) {
      const message = getErrorMessage(error)
      showToast(`审批响应失败：${message}`)
      appendSystemMessage(`审批响应失败：${message}`, 'error')
    }
  }

  async function copyMessage(text: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(text)
      showToast('已复制消息')
    } catch (error) {
      const message = getErrorMessage(error)
      showToast(`复制失败：${message}`)
    }
  }

  function cycleTheme(): void {
    setThemeMode((current) => {
      if (current === 'system') {
        return 'light'
      }
      if (current === 'light') {
        return 'dark'
      }
      return 'system'
    })
  }

  const connected = connectionState === 'connected'
  const canSend = connected && Boolean(sessionId) && !running && input.trim().length > 0 && input.trim() !== '/'
  const placeholder = running
    ? 'AI 正在回复...'
    : connectionState === 'reconnecting'
      ? '连接恢复中，稍后可继续发送'
      : connected
        ? '输入消息，Enter 发送'
        : '当前断开，等待自动重连'

  const statusText = connectionState === 'connected'
    ? '已连接'
    : connectionState === 'reconnecting'
      ? '重连中'
      : '已断开'

  const statusClass = connectionState === 'connected'
    ? 'ok'
    : connectionState === 'reconnecting'
      ? 'warn'
      : 'bad'

  const themeLabel = themeMode === 'system'
    ? '跟随系统'
    : themeMode === 'light'
      ? '浅色'
      : '深色'

  const themeIcon = themeMode === 'system'
    ? '◐'
    : themeMode === 'light'
      ? '☼'
      : '☾'

  const usageTotalTokens = usage
    ? usage.total_tokens ?? (usage.prompt_tokens + usage.completion_tokens)
    : 0
  const usagePercent = usage && usage.context_limit > 0
    ? Math.min(100, Math.round((usageTotalTokens / usage.context_limit) * 100))
    : 0
  const usageEstimated = Boolean(usage)
  const usageLabel = usage
    ? `${usageEstimated ? '~' : ''}${formatTokenCount(usageTotalTokens)} / ${formatTokenCount(usage.context_limit)} tokens (${usagePercent}%)`
    : '— / 64k tokens'
  const usageClass = usagePercent >= 80 ? 'warn' : 'ok'

  if (authToken === null) {
    return (
      <LoginPage
        error={authError}
        loading={authBusy}
        localHint={isLoopbackHost(window.location.hostname)}
        onSubmit={handleLogin}
      />
    )
  }

  const normalizedInput = input.trimStart()
  const commandPaletteQuery = normalizedInput.startsWith('/')
    ? normalizedInput.slice(1).trim().toLowerCase()
    : ''
  const commandPaletteVisible = commandPaletteOpen || normalizedInput.startsWith('/')
  const commandPaletteCommands = COMMANDS.filter((command) => {
    if (!commandPaletteQuery) {
      return true
    }
    const haystack = `${command.name} ${command.label} ${command.description}`.toLowerCase()
    return haystack.includes(commandPaletteQuery)
  })

  return (
    <div className="app">
      {toast && <div className="toast">{toast}</div>}

      {sidebarOpen && (
        <div className="sidebar-overlay" onClick={() => setSidebarOpen(false)}>
          <aside className="sidebar" onClick={(event) => event.stopPropagation()}>
            <div className="sidebar-header">
              <div>
                <h3>会话</h3>
                <p>本地保留最近会话 id，重开页面不会丢。</p>
              </div>
              <button className="icon-btn" onClick={() => void newSession()} title="新建会话">+</button>
            </div>
            <SearchPanel
              open={searchOpen}
              query={searchQuery}
              loading={searchLoading}
              results={searchResults}
              onToggle={() => setSearchOpen((current) => !current)}
              onQueryChange={setSearchQuery}
              onSelect={(result) => {
                void switchSession(result.session_id, {
                  query: searchQuery,
                  content: result.content,
                })
              }}
            />
            <div className="session-list">
              <div className={`session-item-row ${sessionId === '' ? 'active' : ''}`}>
                <button
                  type="button"
                  className={`session-item ${sessionId === '' ? 'active' : ''}`}
                  onClick={() => void newSession()}
                >
                  <span className="sess-icon">＋</span>
                  <span className="sess-title">新建会话</span>
                </button>
                <span className="session-item-spacer" />
              </div>
              {sessions.map((item) => (
                <div key={item.id} className={`session-item-row ${item.id === sessionId ? 'active' : ''}`}>
                  <button
                    type="button"
                    className={`session-item ${item.id === sessionId ? 'active' : ''}`}
                    onClick={() => void switchSession(item.id)}
                  >
                    <span className="sess-icon">💬</span>
                    <span className="sess-title">{item.title || item.id.slice(0, 12)}</span>
                  </button>
                  <button
                    type="button"
                    className="session-delete-btn"
                    onClick={() => void deleteSession(item.id)}
                    title="删除会话"
                    aria-label={`删除会话 ${item.title || item.id}`}
                  >
                    🗑
                  </button>
                </div>
              ))}
            </div>
          </aside>
        </div>
      )}

      <header className="header">
        <button className="icon-btn menu-btn" onClick={() => { setSidebarOpen(true); void refreshSessions() }}>☰</button>
        <div className="brand">
          <div className="logo">qi</div>
          <div className="brand-text">
            <span className="brand-name">qi-agent</span>
            <span className="brand-sub">{sessionId ? sessionId.slice(0, 12) : 'Web Shell'}</span>
          </div>
        </div>
        <div className="header-right">
          <button
            type="button"
            className="theme-btn"
            onClick={cycleTheme}
            title="切换浅色 / 深色 / 跟随系统"
          >
            <span className="theme-btn-icon">{themeIcon}</span>
            <span className="theme-btn-text">{themeLabel}</span>
          </button>
          <span className={`badge ${usageClass} usage-badge`} title="当前会话上下文占用">
            <span className="dot" />
            {usageLabel}
          </span>
          {running && <span className="badge running-badge"><span className="spinner" />运行中</span>}
          <button className="icon-btn" onClick={() => void stop()} disabled={!running} title="停止">⏹</button>
          <button className="icon-btn" onClick={() => void clearCurrentSession()} disabled={!sessionId} title="清空当前会话">🧹</button>
          <button className="icon-btn" onClick={() => void compact()} disabled={!sessionId || running} title="压缩上下文">🗜</button>
          <button className="icon-btn" onClick={() => void openMemory()} title="记忆">🧠</button>
          <span className={`badge ${statusClass}`}>
            <span className="dot" />
            {statusText}
          </span>
        </div>
      </header>

      <main className="messages">
        {entries.length === 0 && (
          <div className="empty">
            <div className="empty-logo">qi</div>
            <h2>qi-agent Web Shell</h2>
            <p>输入消息开始对话，或者从左侧恢复之前的会话。断线时会自动重连，重连后会继续补回历史。</p>
            <div className="empty-hints">
              <span>/new 新建会话</span>
              <span>/history 恢复消息</span>
              <span>/theme 切换主题</span>
            </div>
          </div>
        )}

        {entries.map((entry, index) => {
          if (entry.kind === 'tool') {
            return (
              <div key={entry.id ?? index} className="row tool">
                <div className="avatar tool">🔧</div>
                <div className="bubble-wrap tool-wrap">
                  <ToolCard
                    name={entry.name}
                    toolArguments={entry.toolArguments}
                    status={entry.status}
                    reason={entry.reason}
                    result={entry.result}
                  />
                </div>
              </div>
            )
          }

          if (entry.kind === 'subtask') {
            return (
              <div key={entry.id ?? index} className="row subtask">
                <div className="avatar subtask">🚀</div>
                <div className="bubble-wrap tool-wrap">
                  <SubTaskCard
                    goal={entry.goal}
                    subId={entry.subId}
                    status={entry.status}
                    resultText={entry.resultText}
                    reason={entry.reason}
                    expanded={Boolean(entry.expanded)}
                    timedOut={Boolean(entry.timedOut)}
                    onToggleExpanded={() => {
                      updateSubTaskEntry(entry.subId, (current) => ({
                        ...current,
                        expanded: !current.expanded,
                      }))
                    }}
                  />
                </div>
              </div>
            )
          }

          return (
            <div
              key={entry.id ?? index}
              ref={(node) => {
                if (node) {
                  messageNodeRefs.current.set(entry.id, node)
                } else {
                  messageNodeRefs.current.delete(entry.id)
                }
              }}
              className={`row ${entry.role} ${highlightedMessageId === entry.id ? 'highlighted' : ''}`}
            >
              {entry.role === 'assistant' && <div className="avatar ai">qi</div>}
              {entry.role === 'user' && <div className="avatar user">我</div>}
              <div className="bubble-wrap">
                {entry.role !== 'system' && (
                  <div className="bubble">
                    <button
                      type="button"
                      className="copy-btn"
                      onClick={() => void copyMessage(entry.content)}
                      title="复制消息"
                    >
                      ⧉
                    </button>
                    <div className="bubble-content">{entry.content}</div>
                  </div>
                )}
                {entry.role === 'system' && (
                  <div className={`sys-note ${entry.variant === 'error' ? 'error' : ''}`}>
                    {entry.content}
                  </div>
                )}
                {entry.role !== 'system' && entry.time && <div className="time">{entry.time}</div>}
              </div>
            </div>
          )
        })}
        <div ref={messagesEndRef} />
      </main>

      {approval && (
        <div className="approval">
          <div className="approval-box">
            <div className="approval-header">
              <span className="approval-icon">⚠</span>
              <h3>工具审批请求</h3>
            </div>
            <div className="approval-tool">{String(approval.name || '未知工具')}</div>
            <pre className="approval-code">
              {typeof approval.command === 'string' && approval.command
                ? approval.command
                : JSON.stringify(approval.arguments || {}, null, 2)}
            </pre>
            <p className="approval-hint">允许执行这个操作，或者拒绝。</p>
            <div className="approval-btns">
              <button className="btn-deny" onClick={() => void respondApproval('deny')}>拒绝</button>
              <button className="btn-allow" onClick={() => void respondApproval('approve')}>允许</button>
            </div>
          </div>
        </div>
      )}

      {memoryOpen && (
        <div className="approval">
          <div className="approval-box">
            <div className="approval-header">
              <span className="approval-icon">🧠</span>
              <h3>跨会话记忆</h3>
            </div>
            <pre className="approval-code memory-view">{memoryText}</pre>
            <div className="approval-btns">
              <button className="btn-deny" onClick={() => setMemoryOpen(false)}>关闭</button>
            </div>
          </div>
        </div>
      )}

      <footer className="composer">
        {commandPaletteVisible && (
          <CommandPalette
            commands={commandPaletteCommands}
            query={commandPaletteQuery}
            onSelect={(command) => void handleCommandSelect(command)}
            onClose={() => closeCommandPalette()}
          />
        )}
        <div className="input-bar">
          <button
            type="button"
            className="command-btn"
            onClick={() => {
              setCommandPaletteOpen(true)
              inputRef.current?.focus()
            }}
            title="命令面板"
            aria-label="打开命令面板"
          >
            / 
          </button>
          <input
            ref={inputRef}
            value={input}
            onChange={(event) => {
              const nextValue = event.target.value
              setInput(nextValue)
              if (nextValue.trimStart().startsWith('/')) {
                setCommandPaletteOpen(true)
              }
            }}
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                closeCommandPalette()
                return
              }
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                void send()
              }
            }}
            placeholder={placeholder}
            disabled={running}
          />
          <button className="send-btn" onClick={() => void send()} disabled={!canSend}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M22 2L11 13" />
              <path d="M22 2L15 22L11 13L2 9L22 2Z" />
            </svg>
          </button>
        </div>
      </footer>
    </div>
  )
}
