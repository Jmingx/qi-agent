import { useCallback, useEffect } from 'react'
import type { Dispatch, MutableRefObject, SetStateAction } from 'react'
import { type ConnectionStatus, type WsClient } from '../ws'
import {
  commandFromInput,
  getErrorMessage,
  now,
  readSessionId,
  toMessage,
  type ContextUsageResponse,
  type HistoryPage,
  type SessionCreateResponse,
  type SessionItem,
  type SessionResumeResponse,
  type SessionSearchResult,
  type SessionStatusResponse,
  type StreamEntry,
} from '../appModel'
import { COMMANDS, getCommandDefinition, type CommandName } from '../commands'
import { logEvent } from '../utils/eventLog'

type SessionState = {
  sessionId: string
  setSessionId: Dispatch<SetStateAction<string>>
  sessionIdRef: MutableRefObject<string>
  sessions: SessionItem[]
  setSessions: Dispatch<SetStateAction<SessionItem[]>>
  sidebarOpen: boolean
  setSidebarOpen: Dispatch<SetStateAction<boolean>>
  running: boolean
  setRunning: Dispatch<SetStateAction<boolean>>
  loadingSession: boolean
  setLoadingSession: Dispatch<SetStateAction<boolean>>
  approval: Record<string, unknown> | null
  setApproval: Dispatch<SetStateAction<Record<string, unknown> | null>>
  memoryOpen: boolean
  setMemoryOpen: Dispatch<SetStateAction<boolean>>
  memoryText: string
  setMemoryText: Dispatch<SetStateAction<string>>
  bootstrappedRef: MutableRefObject<boolean>
  bootstrapInFlightRef: MutableRefObject<boolean>
  bootstrapPromiseRef: MutableRefObject<Promise<string | null> | null>
  loadingSessionRef: MutableRefObject<boolean>
  refreshSessions: () => Promise<void>
}

type MessageState = {
  entriesRef: MutableRefObject<StreamEntry[]>
  replaceEntries: (next: StreamEntry[]) => void
  clearEntries: () => void
  appendMessage: (role: 'user' | 'assistant' | 'system', content: string, variant?: 'default' | 'error' | 'info') => number
  appendSystemMessage: (content: string, variant?: 'default' | 'error' | 'info') => number
  requestScrollToMessage: (target: { sessionId: string; query: string; content: string } | null) => void
  currentTurnRef: MutableRefObject<number>
  currentTurnAssistantSeenRef: MutableRefObject<boolean>
  turnErrorNotifiedRef: MutableRefObject<boolean>
  beginTurn: () => void
  updateSubTaskEntry: (subId: string, updater: (entry: { expanded: boolean }) => { expanded: boolean }) => void
  updateEntriesById: (entryId: number, updater: (entry: StreamEntry) => StreamEntry) => void
}

type UsageState = {
  setUsage: Dispatch<SetStateAction<ContextUsageResponse | null>>
  refreshUsage: (targetSessionId?: string) => Promise<void>
}

type SubtaskState = {
  startDelegate: (goal: string) => Promise<void>
}

type UseSessionActionsArgs = {
  authToken: string | null
  clientRef: MutableRefObject<WsClient | null>
  connectionState: ConnectionStatus
  connectionStateRef: MutableRefObject<ConnectionStatus>
  refreshTrace: (targetSessionId?: string) => Promise<string | null>
  setTraceId: Dispatch<SetStateAction<string | null>>
  session: SessionState
  messages: MessageState
  usage: UsageState
  subtask: SubtaskState
  input: string
  setInput: Dispatch<SetStateAction<string>>
  setCommandPaletteOpen: Dispatch<SetStateAction<boolean>>
  inputRef: MutableRefObject<HTMLInputElement | null>
  showToast: (message: string) => void
  toggleTheme: () => void
}

type UseSessionActionsResult = {
  bootstrapSession: () => Promise<string | null>
  newSession: () => Promise<void>
  switchSession: (targetSessionId: string, focusSearch?: { query: string; content: string }) => Promise<void>
  deleteSession: (targetSessionId: string) => Promise<void>
  clearCurrentSession: () => Promise<void>
  stop: () => Promise<void>
  openMemory: () => Promise<void>
  compact: () => Promise<void>
  respondApproval: (decision: 'approve' | 'deny') => Promise<void>
  showCurrentSessionStatus: () => Promise<void>
  executeCommand: (commandText: string) => Promise<boolean>
  send: () => Promise<void>
  retryMessage: (messageId: number, text: string) => Promise<void>
  handleCommandSelect: (command: CommandName) => Promise<void>
  handleSearchSelect: (result: SessionSearchResult) => void
}

type SendOptions = {
  userMessageId?: number
  preserveInput?: boolean
}

export function useSessionActions({
  authToken,
  clientRef,
  connectionState,
  connectionStateRef,
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
  toggleTheme,
}: UseSessionActionsArgs): UseSessionActionsResult {
  const {
    setSessionId,
    setRunning,
    setLoadingSession,
    setApproval,
    setSidebarOpen,
    bootstrappedRef,
    bootstrapInFlightRef,
    bootstrapPromiseRef,
    loadingSessionRef,
    sessionIdRef,
  } = session
  const {
    replaceEntries,
    clearEntries,
    appendSystemMessage,
    requestScrollToMessage,
    entriesRef,
  } = messages
  const { setUsage } = usage
  const ensureConnected = useCallback((actionName: string): boolean => {
    if (connectionStateRef.current === 'connected') {
      return true
    }
    showToast(`${actionName} 闇€瑕佸厛杩炴帴 WebSocket`)
    return false
  }, [connectionStateRef, showToast])

  const refreshUsage = usage.refreshUsage
  const refreshSessions = session.refreshSessions

  const loadHistory = useCallback(async (targetSessionId: string): Promise<StreamEntry[]> => {
    const client = clientRef.current
    if (!client) {
      throw new Error('WebSocket not connected')
    }

    const collected: StreamEntry[] = []
    let offset = 0
    let total = Number.POSITIVE_INFINITY

    while (offset < total) {
      const page = await client.call<HistoryPage>('context/history', {
        session_id: targetSessionId,
        offset,
        limit: 100,
      })
      total = Number(page.total ?? 0)
      const chunk = (page.messages ?? []).map((message, index) => ({
        ...toMessage(message),
        id: offset + index + 1,
      }))
      collected.push(...chunk)
      if (chunk.length === 0) {
        break
      }
      offset += chunk.length
    }

    return collected
  }, [clientRef])

  const replaceSessionWithFresh = useCallback(async (goal = 'web 浼氳瘽'): Promise<string> => {
    const client = clientRef.current
    if (!client) {
      throw new Error('WebSocket not connected')
    }

    const response = await client.call<SessionCreateResponse>('session/create', { goal })
    setSessionId(response.session_id)
    setTraceId(null)
    bootstrappedRef.current = true
    setRunning(false)
    setLoadingSession(false)
    setApproval(null)
    setSidebarOpen(false)
    clearEntries()
    setUsage(null)
    requestScrollToMessage(null)
    await refreshSessions()
    await refreshUsage(response.session_id)
    return response.session_id
  }, [
    bootstrappedRef,
    clearEntries,
    clientRef,
    refreshSessions,
    refreshUsage,
    requestScrollToMessage,
    setApproval,
    setLoadingSession,
    setRunning,
    setSessionId,
    setTraceId,
    setSidebarOpen,
  ])

  const restoreSession = useCallback(async (
    targetSessionId: string,
    options: {
      allowFallbackToCreate: boolean
      focusSearch?: { query: string; content: string }
    },
  ): Promise<string | null> => {
    const client = clientRef.current
    if (!client) {
      throw new Error('Client not initialized')
    }

    if (loadingSessionRef.current) {
      return null
    }

    loadingSessionRef.current = true
    setLoadingSession(true)
    try {
      const loadingNote = targetSessionId
        ? `姝ｅ湪鎭㈠浼氳瘽 ${targetSessionId.slice(0, 8)}...`
        : '姝ｅ湪鎭㈠浼氳瘽...'
      replaceEntries([
        {
          id: 1,
          kind: 'message',
          role: 'system',
          content: loadingNote,
          time: now(),
          variant: 'info',
        },
      ])

      await client.call<SessionResumeResponse>('session/resume', { session_id: targetSessionId })
      const history = await loadHistory(targetSessionId)
      setSessionId(targetSessionId)
      setTraceId(null)
      bootstrappedRef.current = true
      setApproval(null)
      setRunning(false)
      replaceEntries(history)
      setSidebarOpen(false)
      requestScrollToMessage(options.focusSearch
        ? {
            sessionId: targetSessionId,
            query: options.focusSearch.query,
            content: options.focusSearch.content,
          }
        : null)
      void refreshSessions()
      void refreshUsage(targetSessionId)
      return targetSessionId
    } catch (error) {
      console.error('[qi-agent] restoreSession failed', error)
      if (!options.allowFallbackToCreate) {
        throw error
      }
      const message = getErrorMessage(error)
      showToast(`鎭㈠浼氳瘽澶辫触锛屽凡鑷姩鏂板缓锛?{message}`)
      const createdSessionId = await replaceSessionWithFresh('web 浼氳瘽')
      appendSystemMessage(`鎭㈠浼氳瘽澶辫触锛屽凡鑷姩鏂板缓锛?{message}`, 'error')
      return createdSessionId
    } finally {
      loadingSessionRef.current = false
      setLoadingSession(false)
    }
  }, [
    appendSystemMessage,
    bootstrappedRef,
    loadHistory,
    loadingSessionRef,
    refreshSessions,
    refreshUsage,
    replaceEntries,
    replaceSessionWithFresh,
    requestScrollToMessage,
    setApproval,
    setLoadingSession,
    setRunning,
    setSessionId,
    setTraceId,
    setSidebarOpen,
    showToast,
  ])

  const bootstrapSession = useCallback(async (): Promise<string | null> => {
    if (bootstrappedRef.current) {
      return sessionIdRef.current || null
    }
    if (bootstrapPromiseRef.current) {
      return bootstrapPromiseRef.current
    }

    bootstrapInFlightRef.current = true
    const promise = (async (): Promise<string | null> => {
      try {
        const storedSessionId = readSessionId()
        const bootstrappedSessionId = storedSessionId
          ? await restoreSession(storedSessionId, { allowFallbackToCreate: true })
          : await replaceSessionWithFresh('web 浼氳瘽')
        void refreshSessions()
        return bootstrappedSessionId
      } catch (error) {
        console.error('[qi-agent] bootstrapSession failed', error)
        return null
      } finally {
        bootstrapInFlightRef.current = false
        bootstrapPromiseRef.current = null
      }
    })()
    bootstrapPromiseRef.current = promise
    return promise
  }, [
    bootstrapInFlightRef,
    bootstrapPromiseRef,
    bootstrappedRef,
    refreshSessions,
    replaceSessionWithFresh,
    restoreSession,
    sessionIdRef,
  ])

  const ensureSession = useCallback(async (): Promise<string> => {
    if (session.sessionId) {
      return session.sessionId
    }

    // 先等待现成的 bootstrap，避免发送链路自己再开一条创建请求。
    const bootstrappedSessionId = await bootstrapSession()
    if (bootstrappedSessionId) {
      return bootstrappedSessionId
    }

    // bootstrap 失败或被历史恢复逻辑卡住时，发送路径兜底新建一个干净会话。
    if (connectionStateRef.current !== 'connected') {
      throw new Error('WebSocket not connected')
    }

    return replaceSessionWithFresh('web 浼氳瘽')
  }, [bootstrapSession, connectionStateRef, replaceSessionWithFresh, session.sessionId])

  const newSession = useCallback(async (): Promise<void> => {
    if (!ensureConnected('鏂板缓浼氳瘽')) {
      return
    }
    setSidebarOpen(false)
    try {
      await replaceSessionWithFresh('web 浼氳瘽')
      showToast('New session created')
    } catch (error) {
      const message = getErrorMessage(error)
      showToast(`鏂板缓浼氳瘽澶辫触锛?{message}`)
      appendSystemMessage(`鏂板缓浼氳瘽澶辫触锛?{message}`, 'error')
    }
  }, [ensureConnected, messages, replaceSessionWithFresh, session, showToast])

  const switchSession = useCallback(async (
    targetSessionId: string,
    focusSearch?: { query: string; content: string },
  ): Promise<void> => {
    if (!targetSessionId || session.running) {
      return
    }
    if (!ensureConnected('鍒囨崲浼氳瘽')) {
      return
    }
    logEvent('switch_session', {
      from: sessionIdRef.current,
      to: targetSessionId,
    })
    setSidebarOpen(false)
    try {
      await restoreSession(targetSessionId, {
        allowFallbackToCreate: true,
        focusSearch,
      })
    } catch (error) {
      const message = getErrorMessage(error)
      showToast(`鍒囨崲浼氳瘽澶辫触锛?{message}`)
      appendSystemMessage(`鍒囨崲浼氳瘽澶辫触锛?{message}`, 'error')
    }
  }, [ensureConnected, messages, restoreSession, session, showToast])

  const deleteSession = useCallback(async (targetSessionId: string): Promise<void> => {
    if (!targetSessionId || !ensureConnected('鍒犻櫎浼氳瘽')) {
      return
    }
    const label = session.sessions.find((item) => item.id === targetSessionId)?.title || targetSessionId
    if (!window.confirm(`Delete session "${label}"? This cannot be undone.`)) {
      return
    }
    logEvent('delete_session', {
      current: targetSessionId === sessionIdRef.current,
      sessionId: targetSessionId,
    })
    const client = clientRef.current
    if (!client) {
      return
    }
    try {
      await client.call('session/delete', { session_id: targetSessionId })
      const deletingCurrent = targetSessionId === sessionIdRef.current
      if (deletingCurrent) {
        // 当前会话被删除后，链接必须立刻失效；等新会话创建完再重新拉取。
        setTraceId(null)
      }
      await refreshSessions()
      if (!deletingCurrent) {
        await refreshUsage(sessionIdRef.current)
      }
      if (deletingCurrent) {
        setApproval(null)
        setRunning(false)
        messages.clearEntries()
        usage.setUsage(null)
        await replaceSessionWithFresh('web 浼氳瘽')
      }
      showToast('Session deleted')
    } catch (error) {
      const message = getErrorMessage(error)
      showToast(`鍒犻櫎浼氳瘽澶辫触锛?{message}`)
      appendSystemMessage(`鍒犻櫎浼氳瘽澶辫触锛?{message}`, 'error')
    }
  }, [clientRef, ensureConnected, messages, refreshSessions, refreshUsage, replaceSessionWithFresh, session, setTraceId, showToast, usage])

  const clearCurrentSession = useCallback(async (): Promise<void> => {
    if (!session.sessionId || !ensureConnected('娓呯┖褰撳墠浼氳瘽')) {
      return
    }
    if (!window.confirm('Clear the current session? This removes the current context.')) {
      return
    }
    const client = clientRef.current
    if (!client) {
      return
    }
    try {
      await client.call('context/clear', { session_id: session.sessionId })
      setApproval(null)
      setRunning(false)
      setTraceId(null)
      messages.currentTurnRef.current += 1
      messages.clearEntries()
      usage.setUsage(null)
      showToast('Current session cleared')
      await refreshSessions()
      await refreshUsage(session.sessionId)
    } catch (error) {
      const message = getErrorMessage(error)
      showToast(`娓呯┖浼氳瘽澶辫触锛?{message}`)
      appendSystemMessage(`娓呯┖浼氳瘽澶辫触锛?{message}`, 'error')
    }
  }, [clientRef, ensureConnected, messages, refreshSessions, refreshUsage, session, setTraceId, showToast, usage])

  const stop = useCallback(async (): Promise<void> => {
    if (!session.sessionId || !ensureConnected('鍋滄杩愯')) {
      return
    }
    const client = clientRef.current
    if (!client) {
      return
    }
    try {
      await client.call('session/stop', { session_id: session.sessionId })
      appendSystemMessage('Stop request sent')
      setRunning(false)
    } catch (error) {
      const message = getErrorMessage(error)
      showToast(`鍋滄澶辫触锛?{message}`)
      appendSystemMessage(`鍋滄澶辫触锛?{message}`, 'error')
    }
  }, [clientRef, ensureConnected, messages, session, showToast])

  const openMemory = useCallback(async (): Promise<void> => {
    if (!ensureConnected('鎵撳紑璁板繂闈㈡澘')) {
      return
    }
    const client = clientRef.current
    if (!client) {
      return
    }
    try {
      const response = await client.call<{ memory: string }>('memory/get')
      session.setMemoryText(response.memory || '(绌?')
    } catch (error) {
      const message = getErrorMessage(error)
      session.setMemoryText(`璇诲彇澶辫触锛?{message}`)
      showToast(`璁板繂璇诲彇澶辫触锛?{message}`)
      appendSystemMessage(`璁板繂璇诲彇澶辫触锛?{message}`, 'error')
    } finally {
      session.setMemoryOpen(true)
    }
  }, [clientRef, ensureConnected, messages, session, showToast])

  const compact = useCallback(async (): Promise<void> => {
    if (!session.sessionId || !ensureConnected('Compress context')) {
      return
    }
    const client = clientRef.current
    if (!client) {
      return
    }
    try {
      const response = await client.call<{ ok: boolean; summary?: string; before?: number }>(
        'context/compact',
        { session_id: session.sessionId },
      )
      appendSystemMessage(
        `鍘嬬缉瀹屾垚锛?{response.before ?? 0} 鏉℃秷鎭級锛?{response.summary || ''}`,
      )
    } catch (error) {
      const message = getErrorMessage(error)
      showToast(`鍘嬬缉澶辫触锛?{message}`)
      appendSystemMessage(`鍘嬬缉澶辫触锛?{message}`, 'error')
    }
  }, [clientRef, ensureConnected, messages, session, showToast])

  const respondApproval = useCallback(async (decision: 'approve' | 'deny'): Promise<void> => {
    if (!session.approval || !session.sessionId || !ensureConnected('澶勭悊瀹℃壒')) {
      return
    }
    const client = clientRef.current
    if (!client) {
      return
    }
    try {
      await client.call('approval/respond', {
        session_id: session.sessionId,
        approval_id: session.approval.approval_id,
        decision,
      })
      setApproval(null)
    } catch (error) {
      const message = getErrorMessage(error)
      showToast(`瀹℃壒鍝嶅簲澶辫触锛?{message}`)
      appendSystemMessage(`瀹℃壒鍝嶅簲澶辫触锛?{message}`, 'error')
    }
  }, [clientRef, ensureConnected, messages, session, showToast])

  const showCommandHelp = useCallback((): void => {
    appendSystemMessage(
      ['Available commands:', ...COMMANDS.map((command) => (
        command.requiresInput
          ? `${command.name} ${command.name} goal`
          : `${command.name} ${command.description}`
      ))].join('\n'),
      'info',
    )
  }, [appendSystemMessage])

  const showCurrentSessionStatus = useCallback(async (): Promise<void> => {
    if (!session.sessionId || !ensureConnected('Show session status')) {
      return
    }
    const client = clientRef.current
    if (!client) {
      return
    }
    try {
      const response = await client.call<SessionStatusResponse>('session/status', {
        session_id: session.sessionId,
      })
      appendSystemMessage(
        [
          `浼氳瘽鐘舵€侊細${response.status || 'unknown'}`,
          `浼氳瘽 ID锛?{response.session_id || session.sessionId}`,
          `娑堟伅鏁帮細${entriesRef.current.length}`,
          response.result ? `缁撴灉锛?{JSON.stringify(response.result).slice(0, 300)}` : '缁撴灉锛氭棤',
          response.error ? `閿欒锛?{JSON.stringify(response.error).slice(0, 300)}` : '閿欒锛氭棤',
        ].join('\n'),
        'info',
      )
    } catch (error) {
      const message = getErrorMessage(error)
      showToast(`鏌ョ湅鐘舵€佸け璐ワ細${message}`)
      appendSystemMessage(`鏌ョ湅鐘舵€佸け璐ワ細${message}`, 'error')
    }
  }, [clientRef, ensureConnected, messages, session, showToast])

  const executeCommand = useCallback(async (commandText: string): Promise<boolean> => {
    const parsed = commandFromInput(commandText)
    if (!parsed) {
      return false
    }

    const command = `/${parsed.name}` as CommandName
    const definition = getCommandDefinition(command)
    if (!definition) {
      showToast(`鏈煡鍛戒护锛?{commandText}`)
      appendSystemMessage(`鏈煡鍛戒护锛?{commandText}`, 'error')
      return true
    }
    await definition.run({
      clearCurrentSession,
      compact,
      newSession,
      openMemory,
      openSessionList: async (): Promise<void> => {
        setSidebarOpen(true)
        await refreshSessions()
      },
      showCommandHelp,
      showCurrentSessionStatus,
      startDelegate: async (goal: string): Promise<void> => {
        logEvent('delegate', {
          goalLength: goal.length,
          sessionId: sessionIdRef.current,
        })
        await subtask.startDelegate(goal)
      },
      stop,
      toggleTheme,
    }, parsed.args)
    return true
  }, [
    clearCurrentSession,
    compact,
    messages,
    newSession,
    openMemory,
    refreshSessions,
    session,
    showCommandHelp,
    showCurrentSessionStatus,
    sessionIdRef,
    showToast,
    stop,
    subtask,
    toggleTheme,
    setSidebarOpen,
  ])

  const dispatchMessage = useCallback(async (
    text: string,
    options: SendOptions = {},
  ): Promise<void> => {
    const content = text.trim()
    if (!content) {
      return
    }
    if (connectionStateRef.current !== 'connected') {
      showToast('Disconnected. Wait for reconnect.')
      return
    }
    const sessionId = await ensureSession()
    const client = clientRef.current
    if (!client) {
      showToast('Connection unavailable')
      return
    }
    if (session.running) {
      return
    }

    logEvent('send', {
      isRetry: options.userMessageId !== undefined,
      length: content.length,
      sessionId,
    })

    if (options.userMessageId !== undefined) {
      messages.updateEntriesById(options.userMessageId, (entry) => (
        entry.kind === 'message'
          ? { ...entry, variant: 'default' }
          : entry
      ))
    } else if (!options.preserveInput) {
      setInput('')
      setCommandPaletteOpen(false)
    }

    messages.beginTurn()
    const userMessageId = options.userMessageId ?? messages.appendMessage('user', content)
    messages.trackCurrentTurnEntryId(userMessageId)
    setRunning(true)

    try {
      const response = await client.call<{ reply: string }>('message/send', {
        session_id: sessionId,
        text: content,
      })
      if (response.reply) {
        window.setTimeout(() => {
          if (!messages.currentTurnAssistantSeenRef.current) {
            messages.appendMessage('assistant', response.reply)
            messages.currentTurnAssistantSeenRef.current = true
          }
        }, 250)
      }
    } catch (error) {
      const message = getErrorMessage(error)
      showToast(`娑堟伅鍙戦€佸け璐ワ細${message}`)
      messages.updateEntriesById(userMessageId, (entry) => (
        entry.kind === 'message'
          ? { ...entry, variant: 'error' }
          : entry
      ))
      if (!messages.turnErrorNotifiedRef.current) {
        appendSystemMessage(`RPC 璋冪敤澶辫触锛?{message}`, 'error')
      }
    } finally {
      setRunning(false)
      messages.turnErrorNotifiedRef.current = false
      // 用 ref 而非闭包 sessionId（lazy 会话创建后 state 更新有延迟窗口——
      // 闭包里的 sessionId 可能是旧值，导致 trace 刷新到空会话）
      void refreshTrace(session.sessionIdRef.current)
      void refreshUsage()
    }
  }, [
    clientRef,
    connectionStateRef,
    ensureSession,
    messages,
    refreshTrace,
    refreshUsage,
    setCommandPaletteOpen,
    setInput,
    showToast,
  ])

  const send = useCallback(async (): Promise<void> => {
    const text = input.trim()
    if (!text || text === '/') {
      return
    }
    if (text.startsWith('/')) {
      setInput('')
      setCommandPaletteOpen(false)
      await executeCommand(text)
      return
    }
    await dispatchMessage(text)
  }, [dispatchMessage, executeCommand, input, setCommandPaletteOpen, setInput])

  const retryMessage = useCallback(async (messageId: number, text: string): Promise<void> => {
    await dispatchMessage(text, {
      userMessageId: messageId,
      preserveInput: true,
    })
  }, [dispatchMessage])

  const handleCommandSelect = useCallback(async (command: CommandName): Promise<void> => {
    setCommandPaletteOpen(false)
    const definition = getCommandDefinition(command)
    if (definition?.requiresInput) {
      setInput(`${command} `)
      inputRef.current?.focus()
      return
    }
    await executeCommand(command)
    inputRef.current?.focus()
  }, [executeCommand, inputRef, setCommandPaletteOpen, setInput])

  const handleSearchSelect = useCallback((result: SessionSearchResult): void => {
    void switchSession(result.session_id, {
      query: result.content,
      content: result.content,
    })
  }, [switchSession])

  useEffect(() => {
    if (authToken === null || connectionState !== 'connected') {
      return undefined
    }

    void bootstrapSession().catch(() => {})
    void refreshSessions().catch(() => {})
    void refreshUsage().catch(() => {})
    if (
      bootstrappedRef.current
      && sessionIdRef.current
      && entriesRef.current.length === 0
    ) {
      void restoreSession(sessionIdRef.current, {
        allowFallbackToCreate: true,
      }).catch(() => {})
    }
    return undefined
  }, [
    authToken,
    bootstrapSession,
    bootstrappedRef,
    connectionState,
    entriesRef,
    refreshSessions,
    refreshUsage,
    restoreSession,
    sessionIdRef,
  ])

  return {
    bootstrapSession,
    newSession,
    switchSession,
    deleteSession,
    clearCurrentSession,
    stop,
    openMemory,
    compact,
    respondApproval,
    showCurrentSessionStatus,
    executeCommand,
    send,
    retryMessage,
    handleCommandSelect,
    handleSearchSelect,
  }
}


