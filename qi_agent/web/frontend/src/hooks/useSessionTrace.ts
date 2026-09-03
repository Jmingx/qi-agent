import { useCallback, useEffect, useRef, useState } from 'react'
import type { Dispatch, MutableRefObject, SetStateAction } from 'react'
import { type ConnectionStatus, type WsClient } from '../ws'
import { type SessionTraceResponse } from '../appModel'
import { readJaegerUrl } from '../jaegerUrl'

type UseSessionTraceArgs = {
  clientRef: MutableRefObject<WsClient | null>
  connectionState: ConnectionStatus
  sessionId: string
}

type UseSessionTraceResult = {
  traceId: string | null
  setTraceId: Dispatch<SetStateAction<string | null>>
  jaegerUrl: string
  clearTrace: () => void
  refreshTrace: (targetSessionId?: string) => Promise<string | null>
}

export function useSessionTrace({
  clientRef,
  connectionState,
  sessionId,
}: UseSessionTraceArgs): UseSessionTraceResult {
  const [traceId, setTraceId] = useState<string | null>(null)
  const [jaegerUrl] = useState<string>(() => readJaegerUrl())
  const lastSessionIdRef = useRef('')
  const requestSeqRef = useRef(0)

  const clearTrace = useCallback((): void => {
    setTraceId(null)
  }, [])

  const refreshTrace = useCallback(async (targetSessionId: string = sessionId): Promise<string | null> => {
    if (!targetSessionId || connectionState !== 'connected') {
      return null
    }

    const client = clientRef.current
    if (!client) {
      return null
    }

    const requestSeq = ++requestSeqRef.current
    try {
      const response = await client.call<SessionTraceResponse>('session/trace', {
        session_id: targetSessionId,
      })
      if (requestSeqRef.current === requestSeq) {
        setTraceId(response.trace_id?.trim() || null)
      }
      return response.trace_id?.trim() || null
    } catch (error) {
      if (requestSeqRef.current === requestSeq) {
        setTraceId(null)
      }
      console.error('[qi-agent] refreshTrace failed', error)
      return null
    }
  }, [clientRef, connectionState, sessionId])

  useEffect(() => {
    if (!sessionId) {
      lastSessionIdRef.current = ''
      setTraceId(null)
      return
    }

    if (lastSessionIdRef.current !== sessionId) {
      // 会话切换/新建时先清空，避免用户短暂看到上一个会话的 trace 链接。
      lastSessionIdRef.current = sessionId
      setTraceId(null)
    }

    if (connectionState !== 'connected') {
      return
    }

    void refreshTrace(sessionId)
  }, [refreshTrace, sessionId])

  return {
    traceId,
    setTraceId,
    jaegerUrl,
    clearTrace,
    refreshTrace,
  }
}
