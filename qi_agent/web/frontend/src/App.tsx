import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react'
import { isLoopbackHost } from './auth'
import { ContextPanel } from './components/ContextPanel'
import { Header } from './components/Header'
import { InputBar } from './components/InputBar'
import { MessageList } from './components/MessageList'
import { Sidebar } from './components/Sidebar'
import { TracePanel } from './components/TracePanel'
import { Icon } from './components/ui/Icon'
import { COMMANDS } from './commands'
import { ZH_CN } from './i18n/zh-CN'
import { type SessionItem, type SessionSearchResult, type TextEntry } from './appModel'
import { readOpikUrl } from './opikUrl'
import { logEvent } from './utils/eventLog'
import { useAuth } from './hooks/useAuth'
import { useMediaQuery } from './hooks/useMediaQuery'
import { useMessages } from './hooks/useMessages'
import { useSearch } from './hooks/useSearch'
import { useSession } from './hooks/useSession'
import { useSessionActions } from './hooks/useSessionActions'
import { useSessionTrace } from './hooks/useSessionTrace'
import { useSubtask } from './hooks/useSubtask'
import { useTheme } from './hooks/useTheme'
import { useUsage } from './hooks/useUsage'
import { useWsClient } from './hooks/useWsClient'

const LazyLoginPage = lazy(() =>
  import('./components/LoginPage').then((module) => ({ default: module.LoginPage })),
)

const DRAFT_STORAGE_KEY = 'qi_draft'

type PanelTab = 'context' | 'trace'

type ConfirmState = {
  title: string
  body: string
  confirmLabel: string
  danger?: boolean
  onConfirm: () => void
} | null

export default function App() {
  const auth = useAuth()
  const theme = useTheme()
  const [opikUrl] = useState<string>(() => readOpikUrl())
  const ws = useWsClient({
    authToken: auth.authToken,
    setAuthBusy: auth.setAuthBusy,
    setAuthError: auth.setAuthError,
  })
  const session = useSession({
    clientRef: ws.clientRef,
    connectionState: ws.connectionState,
  })
  const {
    traceId,
    jaegerUrl,
    clearTrace,
    refreshTrace,
    setTraceId,
  } = useSessionTrace({
    clientRef: ws.clientRef,
    connectionState: ws.connectionState,
    sessionId: session.sessionId,
  })
  const usage = useUsage({
    clientRef: ws.clientRef,
    connectionState: ws.connectionState,
    sessionId: session.sessionId,
  })
  const [input, setInput] = useState('')
  const [toast, setToast] = useState<string | null>(null)
  const [commandPaletteOpen, setCommandPaletteOpen] = useState(false)
  const [panelOpen, setPanelOpen] = useState(false)
  const [panelTab, setPanelTab] = useState<PanelTab>('context')
  const [confirmState, setConfirmState] = useState<ConfirmState>(null)
  const [compacting, setCompacting] = useState(false)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const toastTimerRef = useRef<number | null>(null)

  const railIsDrawer = useMediaQuery('(max-width: 1023px)')
  const panelIsOverlay = useMediaQuery('(max-width: 1279px)')

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
    refreshTrace,
    setTraceId,
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
    setPanelOpen(false)
    setConfirmState(null)
    window.localStorage.removeItem(DRAFT_STORAGE_KEY)
    clearTrace()
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
    messages.turnErrorNotifiedRef.current = false
    messages.requestScrollToMessage(null)
    session.bootstrappedRef.current = false
    session.bootstrapInFlightRef.current = false
    session.bootstrapPromiseRef.current = null
    session.loadingSessionRef.current = false
  }, [clearTrace, messages, search, session, usage])

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

  const openContextPanel = useCallback((): void => {
    setPanelTab('context')
    setPanelOpen(true)
    void usage.refreshUsage()
  }, [usage])

  const openTracePanel = useCallback((): void => {
    setPanelTab('trace')
    setPanelOpen(true)
  }, [])

  const openJaeger = useCallback((): void => {
    if (!traceId) {
      showToast('当前会话还没有 trace（需启用 OTel 遥测）')
      return
    }
    window.open(`${jaegerUrl}/trace/${traceId}`, '_blank', 'noopener,noreferrer')
  }, [jaegerUrl, showToast, traceId])

  const handleCompact = useCallback((): void => {
    setCompacting(true)
    void actions.compact().finally(() => {
      setCompacting(false)
      void usage.refreshUsage()
    })
  }, [actions, usage])

  const handleRequestClear = useCallback((): void => {
    setConfirmState({
      title: '清空当前会话？',
      body: '将清除当前会话的上下文（消息历史），此操作不可撤销。',
      confirmLabel: '清空',
      danger: true,
      onConfirm: () => {
        setConfirmState(null)
        void actions.clearCurrentSession()
      },
    })
  }, [actions])

  const handleRequestDelete = useCallback((item: SessionItem): void => {
    const title = item.title?.trim() || '新会话'
    setConfirmState({
      title: `删除会话「${title}」？`,
      body: '会话与消息将从本地存储中删除，不可撤销。',
      confirmLabel: '删除',
      danger: true,
      onConfirm: () => {
        setConfirmState(null)
        void actions.deleteSession(item.id)
      },
    })
  }, [actions])

  const handleCleanupEmpty = useCallback((items: SessionItem[]): void => {
    setConfirmState({
      title: `清理 ${items.length} 个空会话？`,
      body: '这些会话没有任何消息（打开页面时会自动创建）。删除后不可撤销。',
      confirmLabel: '清理',
      danger: true,
      onConfirm: () => {
        setConfirmState(null)
        items.reduce<Promise<void>>(
          (chain, item) => chain.then(() => actions.deleteSession(item.id)),
          Promise.resolve(),
        ).finally(() => void session.refreshSessions())
      },
    })
  }, [actions, session])

  const handleKeyDown = useCallback((event: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (event.key === 'Escape') {
      setCommandPaletteOpen(false)
      return
    }
    if (event.key !== 'Enter') {
      return
    }
    // 中文输入法选词回车不是"发送"——不挡住会发出半截输入
    if (event.nativeEvent.isComposing) {
      return
    }
    if (event.shiftKey && !event.ctrlKey && !event.metaKey) {
      return
    }
    event.preventDefault()
    void actions.send()
  }, [actions])

  // 全局快捷键：Ctrl/⌘+K 命令面板（Escape 关闭浮层）
  useEffect(() => {
    const onKeyDown = (event: globalThis.KeyboardEvent): void => {
      const meta = event.ctrlKey || event.metaKey
      if (meta && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setCommandPaletteOpen((current) => !current)
        inputRef.current?.focus()
        return
      }
      if (event.key === 'Escape') {
        setCommandPaletteOpen(false)
        if (panelIsOverlay) {
          setPanelOpen(false)
        }
        setConfirmState(null)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [panelIsOverlay])

  useEffect(() => {
    return () => {
      clearToastTimer()
    }
  }, [clearToastTimer])

  useEffect(() => {
    if (auth.authToken === null) {
      clearTrace()
    }
  }, [auth.authToken, clearTrace])

  // 连接就绪后加载会话列表（桌面端会话栏常驻，不能只等用户点开关才刷新）
  useEffect(() => {
    if (ws.connectionState !== 'connected') {
      return
    }
    void session.refreshSessions()
  }, [session.refreshSessions, ws.connectionState])

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
  const reconnectExhausted = ws.connectionState === 'disconnected' && ws.hasReconnectExceeded
  const placeholder = session.running
    ? '运行中：输入内容后回车 = 引导（下一轮生效）'
    : ws.connectionState === 'reconnecting'
      ? '连接恢复中，稍后可继续发送'
      : connected
        ? '输入消息，Enter 发送，Shift+Enter 换行'
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

  const sessionTitle = session.sessions.find((item) => item.id === session.sessionId)?.title?.trim()
    || (session.sessionId ? '未命名会话' : '新会话')

  if (auth.authToken === null) {
    return (
      <Suspense fallback={<div className="login-shell"><main className="login-card">加载中…</main></div>}>
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

      <div className="app-body">
        <Sidebar
          isDrawer={railIsDrawer && session.sidebarOpen}
          sessionId={session.sessionId}
          sessions={session.sessions}
          searchOpen={search.searchOpen}
          searchQuery={search.searchQuery}
          searchLoading={search.searchLoading}
          searchResults={search.searchResults}
          onClose={() => session.setSidebarOpen(false)}
          onNewSession={() => void actions.newSession()}
          onSwitchSession={(sessionId) => {
            session.setSidebarOpen(false)
            void actions.switchSession(sessionId)
          }}
          onRequestDelete={handleRequestDelete}
          onCleanupEmpty={handleCleanupEmpty}
          onSearchToggle={() => search.setSearchOpen((current) => !current)}
          onSearchQueryChange={search.setSearchQuery}
          onSearchSelect={handleSearchSelect}
        />

        <section className="chat-column">
          <Header
            sessionTitle={sessionTitle}
            sessionId={session.sessionId}
            connectionState={ws.connectionState}
            running={session.running}
            contextTokens={usage.contextTokens}
            contextLimit={usage.usage?.context_limit ?? 64_000}
            percent={usage.percent}
            usageLevel={usage.usageLevel}
            themeLabel={theme.themeLabel}
            themeIcon={theme.themeIcon}
            traceId={traceId}
            onToggleRail={() => {
              session.setSidebarOpen(true)
              void session.refreshSessions()
            }}
            onOpenContext={openContextPanel}
            onOpenTrace={openTracePanel}
            onToggleTheme={handleToggleTheme}
            onStop={() => void actions.stop()}
            onClear={handleRequestClear}
            onCompact={handleCompact}
            onOpenMemory={() => void actions.openMemory()}
            onOpenJaeger={openJaeger}
            onOpenOpik={() => window.open(opikUrl, '_blank', 'noopener,noreferrer')}
          />

          {reconnectExhausted && (
            <div className="connection-banner" role="status" aria-live="polite">
              <span>连接已断开，自动重连已停止。</span>
              <button type="button" className="btn" onClick={() => void ws.manualReconnect()}>
                点击重试
              </button>
            </div>
          )}

          <MessageList
            entries={messages.entries}
            highlightedMessageId={messages.highlightedMessageId}
            loading={session.loadingSession}
            jaegerUrl={jaegerUrl}
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
            onUseSample={(prompt) => {
              setInput(prompt)
              inputRef.current?.focus()
            }}
          />

          {session.approval && (
            <div className="dialog" role="dialog" aria-modal="true">
              <div className="dialog-card">
                <div className="dialog-head">
                  <span className="dialog-icon"><Icon name="alert" size={16} /></span>
                  <h3 className="dialog-title">工具审批请求</h3>
                </div>
                <div className="dialog-tool">
                  工具：<code>{String(session.approval.name || '未知工具')}</code>
                </div>
                <pre className="dialog-code">
                  {typeof session.approval.command === 'string' && session.approval.command
                    ? session.approval.command
                    : JSON.stringify(session.approval.arguments || {}, null, 2)}
                </pre>
                <p className="dialog-hint">
                  同意后该操作以当前权限执行；拒绝会让 agent 收到[审批拒绝]并调整策略。
                </p>
                <div className="dialog-actions">
                  <button className="btn" onClick={() => void actions.respondApproval('deny')}>拒绝</button>
                  <button className="btn btn-primary" onClick={() => void actions.respondApproval('approve')}>允许执行</button>
                </div>
              </div>
            </div>
          )}

          {session.memoryOpen && (
            <div className="dialog" role="dialog" aria-modal="true">
              <div className="dialog-card">
                <div className="dialog-head">
                  <span className="dialog-icon"><Icon name="memory" size={16} /></span>
                  <h3 className="dialog-title">跨会话记忆</h3>
                </div>
                <pre className="dialog-code">{session.memoryText || '（记忆为空）'}</pre>
                <div className="dialog-actions">
                  <button className="btn" onClick={() => session.setMemoryOpen(false)}>关闭</button>
                </div>
              </div>
            </div>
          )}

          {confirmState && (
            <div className="dialog" role="dialog" aria-modal="true">
              <div className="dialog-card">
                <div className="dialog-head">
                  <span className={`dialog-icon${confirmState.danger ? ' is-danger' : ''}`}>
                    <Icon name="alert" size={16} />
                  </span>
                  <h3 className="dialog-title">{confirmState.title}</h3>
                </div>
                <p className="dialog-hint">{confirmState.body}</p>
                <div className="dialog-actions">
                  <button className="btn" onClick={() => setConfirmState(null)}>取消</button>
                  <button
                    className={`btn ${confirmState.danger ? 'btn-danger' : 'btn-primary'}`}
                    onClick={confirmState.onConfirm}
                  >
                    {confirmState.confirmLabel}
                  </button>
                </div>
              </div>
            </div>
          )}

          <InputBar
            input={input}
            disabled={!connected}
            running={session.running}
            placeholder={placeholder}
            commandPaletteVisible={commandPaletteVisible}
            commandPaletteCommands={commandPaletteCommands}
            commandPaletteQuery={commandPaletteQuery}
            inputRef={inputRef}
            onCommandButtonClick={() => {
              setCommandPaletteOpen(true)
              inputRef.current?.focus()
            }}
            onChange={handleInputChange}
            onKeyDown={handleKeyDown}
            onSend={() => void actions.send()}
            onStop={() => void actions.stop()}
            onSelectCommand={actions.handleCommandSelect}
            onCloseCommandPalette={() => setCommandPaletteOpen(false)}
          />
        </section>

        {panelOpen && (
          <aside className={`side-panel${panelIsOverlay ? ' is-overlay' : ''}`} aria-label="上下文与轨迹">
            <div className="panel-head">
              <div className="panel-tabs" role="tablist">
                <button
                  type="button"
                  role="tab"
                  aria-selected={panelTab === 'context'}
                  className={`panel-tab${panelTab === 'context' ? ' is-active' : ''}`}
                  onClick={() => setPanelTab('context')}
                >
                  上下文
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={panelTab === 'trace'}
                  className={`panel-tab${panelTab === 'trace' ? ' is-active' : ''}`}
                  onClick={() => setPanelTab('trace')}
                >
                  调用轨迹
                </button>
              </div>
              <button
                type="button"
                className="icon-btn"
                onClick={() => setPanelOpen(false)}
                title="关闭面板"
                aria-label="关闭面板"
              >
                <Icon name="close" size={16} />
              </button>
            </div>
            <div className="panel-body">
              {panelTab === 'context' ? (
                <ContextPanel
                  usage={usage.usage}
                  contextTokens={usage.contextTokens}
                  sessionTokens={usage.sessionTokens}
                  sessionEstimated={usage.sessionEstimated}
                  percentUsed={usage.percent}
                  compactAt={usage.compactAt}
                  contextLimit={usage.usage?.context_limit ?? 64_000}
                  turnStats={messages.turnStats}
                  compacting={compacting}
                  onCompact={handleCompact}
                  onRefresh={() => void usage.refreshUsage()}
                />
              ) : (
                <TracePanel
                  entries={messages.entries}
                  jaegerUrl={jaegerUrl}
                  onOpenJaeger={(id) => window.open(`${jaegerUrl}/trace/${id}`, '_blank', 'noopener,noreferrer')}
                />
              )}
            </div>
          </aside>
        )}
      </div>
    </div>
  )
}
