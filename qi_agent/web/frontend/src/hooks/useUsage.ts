import { useCallback, useState } from 'react'
import type { MutableRefObject } from 'react'
import { type ConnectionStatus, type WsClient } from '../ws'
import { formatTokenCount, type ContextUsageResponse } from '../appModel'

type UseUsageArgs = {
  clientRef: MutableRefObject<WsClient | null>
  connectionState: ConnectionStatus
  sessionId: string
}

type UseUsageResult = {
  usage: ContextUsageResponse | null
  setUsage: (value: ContextUsageResponse | null) => void
  refreshUsage: (targetSessionId?: string) => Promise<void>
  usageTotalTokens: number
  usagePercent: number
  usageLabel: string
  usageClass: 'ok' | 'warn'
}

export function useUsage({
  clientRef,
  connectionState,
  sessionId,
}: UseUsageArgs): UseUsageResult {
  const [usage, setUsage] = useState<ContextUsageResponse | null>(null)

  const refreshUsage = useCallback(async (targetSessionId?: string) => {
    const client = clientRef.current
    const activeSessionId = targetSessionId ?? sessionId
    if (!client || !activeSessionId || connectionState !== 'connected') {
      setUsage(null)
      return
    }
    try {
      const response = await client.call<ContextUsageResponse>('context/usage', {
        session_id: activeSessionId,
      })
      setUsage(response)
    } catch (error) {
      console.error('[qi-agent] refreshUsage failed', error)
    }
  }, [clientRef, connectionState, sessionId])

  const usageTotalTokens = usage
    ? usage.total_tokens ?? (usage.prompt_tokens + usage.completion_tokens)
    : 0
  const usagePercent = usage && usage.context_limit > 0
    ? Math.min(100, Math.round((usageTotalTokens / usage.context_limit) * 100))
    : 0
  const usageLabel = usage
    ? `${Boolean(usage.est_ratio ?? false) ? '~' : ''}${formatTokenCount(usageTotalTokens)} / ${formatTokenCount(usage.context_limit)} tokens (${usagePercent}%)`
    : '— / — tokens'
  const usageClass = usagePercent >= 80 ? 'warn' : 'ok'

  return {
    usage,
    setUsage,
    refreshUsage,
    usageTotalTokens,
    usagePercent,
    usageLabel,
    usageClass,
  }
}
