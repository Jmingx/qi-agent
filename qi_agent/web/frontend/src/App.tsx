import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { isLoopbackHost } from './auth'
import { Header } from './components/Header'
import { InputBar } from './components/InputBar'
import { MessageList } from './components/MessageList'
import { Sidebar } from './components/Sidebar'
import { COMMANDS } from './commands'
import { ZH_CN } from './i18n/zh-CN'
import { type SessionSearchResult, type TextEntry } from './appModel'
import { logEvent } from './utils/eventLog'
import { useAuth } from './hooks/useAuth'
import { useMessages } from './hooks/useMessages'
import { useSearch } from './hooks/useSearch'
import { useSession } from './hooks/useSession'
import { useSessionActions } from './hooks/useSessionActions'
import { useSubtask } from './hooks/useSubtask'
import { useTheme } from './hooks/useTheme'
import { useUsage } from './hooks/useUsage'
import { useWsClient } from './hooks/useWsClient'

const LazyLoginPage = lazy(() =>
  import('./components/LoginPage').then((module) => ({ default: module.LoginPage })),
)

const DRAFT_STORAGE_KEY = 'qi_draft'

function buildSessionLabel(sessionId: string): string {
  return sessionId ? sessionId.slice(0, 12) : 'Web Shell'
}

export default function App() {
  const auth = useAuth()
  const theme = useTheme()
  const ws = useWsClient({
    authToken: auth.authToken,
    setAuthBusy: auth.setAuthBusy,
    setAuthError: auth.setAuthError,
  })
  const session = useSession({
    clientRef: ws.clientRef,
    connectionState: ws.connectionState,
  })
  const usage = useUsage({
    clientRef: ws.clientRef,
    connectionState: ws.connectionState,
    sessionId: session.sessionId,
  })
  const [input, setInput] = useState('')
  const [toast, setToast] = useState<string | null>(null)
  const [commandPaletteOpen, setCommandPaletteOpen] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const toastTimerRef = useRef<number | null>(null)

  const clearToastTimer = useCallback((): void => {
    if (toastTimerRef.current !== null) {
      window.clearTimeout(toastTimerRef.current)
      toastTimerRef.current = null
    }
  }, [])

  const showToast = useCallback((message: string): void => {
    setToast(message)
    clearToastTimer()
    toastTimerRef.current = window.setTimeout(() => {
      setToast(null)
      toastTimerRef.current = null
    }, 2800)
  }, [clearToastTimer])

  const handleToggleTheme = useCallback((): void => {
    logEvent('toggle_theme', { themeMode: theme.themeMode })
    theme.cycleTheme()
  }, [theme.cycleTheme, theme.themeMode])

  const messages = useMessages({
    clientRef: ws.clientRef,
    connectionState: ws.connectionState,
    sessionIdRef: session.sessionIdRef,
    setRunning: session.setRunning,
    refreshSessions: session.refreshSessions,
    onApprovalChange: session.setApproval,
  })
  const search = useSearch({
    clientRef: ws.clientRef,
    connectionState: ws.connectionState,
    authToken: auth.authToken,
  })
  const subtask = useSubtask({
    clientRef: ws.clientRef,
    connectionState: ws.connectionState,
    connectionStateRef: ws.connectionStateRef,
    sessionIdRef: session.sessionIdRef,
    entriesRef: messages.entriesRef,
    appendSubTaskEntry: messages.appendSubTaskEntry,
    updateSubTaskEntry: messages.updateSubTaskEntry,
    appendSystemMessage: messages.appendSystemMessage,
    showToast,
  })
  const actions = useSessionActions({
    authToken: auth.authToken,
    clientRef: ws.clientRef,
    connectionState: ws.connectionState,
    connectionStateRef: ws.connectionStateRef,
    session,
    messages,
    usage,
    subtask,
    input,
    setInput,
    setCommandPaletteOpen,
    inputRef,
    showToast,
    toggleTheme: handleToggleTheme,
  })

  const resetWorkspaceState = useCallback((): void => {
    setInput('')
    setToast(null)
    setCommandPaletteOpen(false)
    window.localStorage.removeItem(DRAFT_STORAGE_KEY)
    session.setSessionId('')
    session.setSessions([])
    session.setSidebarOpen(false)
    session.setRunning(false)
    session.setLoadingSession(false)
    session.setApproval(null)
    session.setMemoryOpen(false)
    session.setMemoryText('')
    usage.setUsage(null)
    search.setSearchQuery('')
    search.setSearchOpen(true)
    messages.clearEntries()
    messages.setHighlightedMessageId(null)
    messages.currentTurnRef.current = 0
    messages.currentTurnAssistantSeenRef.current = false
    messages.turnErrorNotifiedRef.current = false
    messages.requestScrollToMessage(null)
    session.bootstrappedRef.current = false
    session.bootstrapInFlightRef.current = false
    session.loadingSessionRef.current = false
  }, [messages, search, session, usage])

  const handleLoginSubmit = useCallback((nextToken: string): void => {
    resetWorkspaceState()
    auth.handleLogin(nextToken)
  }, [auth, resetWorkspaceState])

  const handleInputChange = useCallback((value: string): void => {
    setInput(value)
    if (value.trimStart().startsWith('/')) {
      setCommandPaletteOpen(true)
    }
  }, [])

  const handleSearchSelect = useCallback((result: SessionSearchResult): void => {
    void actions.switchSession(result.session_id, {
      query: result.content,
      content: result.content,
    })
  }, [actions])

  const handleCopyMessage = useCallback(async (text: string): Promise<void> => {
    try {
      await navigator.clipboard.writeText(text)
      showToast(ZH_CN.messageBubble.copySuccess)
    } catch {
      showToast(ZH_CN.messageBubble.copyFailed)
    }
  }, [showToast])

  const handleEditMessage = useCallback((entry: TextEntry): void => {
    setInput(entry.content)
    setCommandPaletteOpen(entry.content.trimStart().startsWith('/'))
    inputRef.current?.focus()
  }, [])

  const handleRetryMessage = useCallback((messageId: number, text: string): void => {
    void actions.retryMessage(messageId, text)
  }, [actions])

  useEffect(() => {
    return () => {
      clearToastTimer()
    }
  }, [clearToastTimer])

  useEffect(() => {
    const draft = window.localStorage.getItem(DRAFT_STORAGE_KEY)
    if (ws.connectionState === 'connected') {
      if (!input && draft) {
        setInput(draft)
        setCommandPaletteOpen(draft.trimStart().startsWith('/'))
      }
      window.localStorage.removeItem(DRAFT_STORAGE_KEY)
      return
    }

    if (input) {
      window.localStorage.setItem(DRAFT_STORAGE_KEY, input)
    } else {
      window.localStorage.removeItem(DRAFT_STORAGE_KEY)
    }
  }, [input, ws.connectionState])

  const connected = ws.connectionState === 'connected'
  const placeholder = session.running
    ? 'AI 正在回复...'
    : ws.connectionState === 'reconnecting'
      ? '连接恢复中，稍后可继续发送'
      : connected
        ? '输入消息，Enter 发送，Ctrl/Cmd+Enter 也可发送'
        : '当前断开，等待自动重连'

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

  if (auth.authToken === null) {
    return (
      <Suspense fallback={<div className="login-shell"><main className="login-card">加载中...</main></div>}>
        <LazyLoginPage
          error={auth.authError}
          loading={auth.authBusy}
          localHint={isLoopbackHost(window.location.hostname)}
          onSubmit={handleLoginSubmit}
        />
      </Suspense>
    )
  }

  return (
    <div className="app">
      {toast && <div className="toast">{toast}</div>}

      <Sidebar
        open={session.sidebarOpen}
        sessionId={session.sessionId}
        sessions={session.sessions}
        searchOpen={search.searchOpen}
        searchQuery={search.searchQuery}
        searchLoading={search.searchLoading}
        searchResults={search.searchResults}
        onClose={() => session.setSidebarOpen(false)}
        onNewSession={() => void actions.newSession()}
        onSwitchSession={(sessionId) => void actions.switchSession(sessionId)}
        onDeleteSession={(sessionId) => void actions.deleteSession(sessionId)}
        onSearchToggle={() => search.setSearchOpen((current) => !current)}
        onSearchQueryChange={search.setSearchQuery}
        onSearchSelect={handleSearchSelect}
      />

      <Header
        sessionId={session.sessionId}
        sessionLabel={buildSessionLabel(session.sessionId)}
        connectionState={ws.connectionState}
        running={session.running}
        usageLabel={usage.usageLabel}
        usageClass={usage.usageClass}
        themeLabel={theme.themeLabel}
        themeIcon={theme.themeIcon}
        onOpenSidebar={() => {
          session.setSidebarOpen(true)
          void session.refreshSessions()
        }}
        onToggleTheme={handleToggleTheme}
        onStop={() => void actions.stop()}
        onClear={() => void actions.clearCurrentSession()}
        onCompact={() => void actions.compact()}
        onOpenMemory={() => void actions.openMemory()}
      />

      <MessageList
        entries={messages.entries}
        highlightedMessageId={messages.highlightedMessageId}
        loading={session.loadingSession}
        scrollTarget={messages.pendingScrollTarget}
        onScrollTargetHandled={() => messages.requestScrollToMessage(null)}
        onHighlightMessage={messages.highlightMessage}
        onCopyMessage={handleCopyMessage}
        onEditMessage={handleEditMessage}
        onRetryMessage={handleRetryMessage}
        onToggleSubTaskExpanded={(subId) => {
          messages.updateSubTaskEntry(subId, (current) => ({
            ...current,
            expanded: !current.expanded,
          }))
        }}
      />

      {session.approval && (
        <div className="approval">
          <div className="approval-box">
            <div className="approval-header">
              <span className="approval-icon">⚠</span>
              <h3>工具审批请求</h3>
            </div>
            <div className="approval-tool">{String(session.approval.name || '未知工具')}</div>
            <pre className="approval-code">
              {typeof session.approval.command === 'string' && session.approval.command
                ? session.approval.command
                : JSON.stringify(session.approval.arguments || {}, null, 2)}
            </pre>
            <p className="approval-hint">允许执行这个操作，或者拒绝。</p>
            <div className="approval-btns">
              <button className="btn-deny" onClick={() => void actions.respondApproval('deny')}>拒绝</button>
              <button className="btn-allow" onClick={() => void actions.respondApproval('approve')}>允许</button>
            </div>
          </div>
        </div>
      )}

      {session.memoryOpen && (
        <div className="approval">
          <div className="approval-box">
            <div className="approval-header">
              <span className="approval-icon">🧠</span>
              <h3>跨会话记忆</h3>
            </div>
            <pre className="approval-code memory-view">{session.memoryText}</pre>
            <div className="approval-btns">
              <button className="btn-deny" onClick={() => session.setMemoryOpen(false)}>关闭</button>
            </div>
          </div>
        </div>
      )}

      <InputBar
        input={input}
        disabled={session.running}
        placeholder={placeholder}
        commandPaletteVisible={commandPaletteVisible}
        commandPaletteCommands={commandPaletteCommands}
        commandPaletteQuery={commandPaletteQuery}
        onCommandButtonClick={() => {
          setCommandPaletteOpen(true)
          inputRef.current?.focus()
        }}
        onChange={handleInputChange}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            setCommandPaletteOpen(false)
            return
          }
          if (event.key === 'Enter' && (event.ctrlKey || event.metaKey || !event.shiftKey)) {
            event.preventDefault()
            void actions.send()
          }
        }}
        onSend={() => void actions.send()}
        onSelectCommand={actions.handleCommandSelect}
        onCloseCommandPalette={() => setCommandPaletteOpen(false)}
      />
    </div>
  )
}
