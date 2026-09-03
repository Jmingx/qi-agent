import { useCallback, useEffect, useRef, useState } from 'react'
import type { Dispatch, MutableRefObject, SetStateAction } from 'react'
import { type ConnectionStatus, type WsClient } from '../ws'
import { readSessionId, SESSION_STORAGE_KEY, type SessionItem } from '../appModel'

type UseSessionArgs = {
  clientRef: MutableRefObject<WsClient | null>
  connectionState: ConnectionStatus
}

type UseSessionResult = {
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

export function useSession({
  clientRef,
  connectionState,
}: UseSessionArgs): UseSessionResult {
  const [sessionId, setSessionIdState] = useState(() => readSessionId())
  const sessionIdRef = useRef(sessionId)
  const [sessions, setSessions] = useState<SessionItem[]>([])
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [running, setRunning] = useState(false)
  const [loadingSession, setLoadingSession] = useState(false)
  const [approval, setApproval] = useState<Record<string, unknown> | null>(null)
  const [memoryOpen, setMemoryOpen] = useState(false)
  const [memoryText, setMemoryText] = useState('')
  const bootstrappedRef = useRef(false)
  const bootstrapInFlightRef = useRef(false)
  const bootstrapPromiseRef = useRef<Promise<string | null> | null>(null)
  const loadingSessionRef = useRef(false)

  useEffect(() => {
    sessionIdRef.current = sessionId
    if (sessionId) {
      window.localStorage.setItem(SESSION_STORAGE_KEY, sessionId)
    } else {
      window.localStorage.removeItem(SESSION_STORAGE_KEY)
    }
  }, [sessionId])

  const setSessionId = useCallback((value: string | ((current: string) => string)) => {
    setSessionIdState((current) => {
      const next = typeof value === 'function' ? value(current) : value
      // 这里同步更新 ref，避免刚创建/恢复会话时 ref 还停留在旧值，发送兜底误判成“没有会话”。
      sessionIdRef.current = next
      return next
    })
  }, [])

  const refreshSessions = useCallback(async (): Promise<void> => {
    const client = clientRef.current
    if (!client || connectionState !== 'connected') {
      return
    }
    try {
      const response = await client.call<{ sessions: SessionItem[] }>('session/list')
      setSessions(response.sessions ?? [])
    } catch (error) {
      console.error('[qi-agent] refreshSessions failed', error)
    }
  }, [clientRef, connectionState])

  return {
    sessionId,
    setSessionId,
    sessionIdRef,
    sessions,
    setSessions,
    sidebarOpen,
    setSidebarOpen,
    running,
    setRunning,
    loadingSession,
    setLoadingSession,
    approval,
    setApproval,
    memoryOpen,
    setMemoryOpen,
    memoryText,
    setMemoryText,
    bootstrappedRef,
    bootstrapInFlightRef,
    bootstrapPromiseRef,
    loadingSessionRef,
    refreshSessions,
  }
}
