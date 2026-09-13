import { useCallback, useState } from 'react'
import type { MutableRefObject } from 'react'
import { type ConnectionStatus, type WsClient } from '../ws'
import { formatTokenCount, type ContextUsageResponse } from '../appModel'

type UseUsageArgs = {
  clientRef: MutableRefObject<WsClient | null>
  connectionState: ConnectionStatus
  sessionId: string
}

export type UsageLevel = 'ok' | 'warn' | 'danger'

type UseUsageResult = {
  usage: ContextUsageResponse | null
  setUsage: (value: ContextUsageResponse | null) => void
  refreshUsage: (targetSessionId?: string) => Promise<void>
  /** 当前上下文窗口占用（语义见 gateway._context_usage：占用 ≠ 累计） */
  contextTokens: number
  /** 会话累计消耗 */
  sessionTokens: number
  sessionEstimated: boolean
  percent: number
  compactAt: number
  usageLabel: string
  usageLevel: UsageLevel
  breakdown: Record<string, number>
}

const DEFAULT_COMPACT_AT = 0.7
const DEFAULT_WARN_AT = 0.8

/**
 * 上下文用量：口径修复后的读取层（UI v3 §9）。
 * - contextTokens = 当前窗口占用（消息 + 工具 schema 估算）
 * - sessionTokens = 会话累计消耗（真实 usage，缺失时前端不显示假数）
 */
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

  const contextLimit = usage?.context_limit ?? 64_000
  const contextTokens = usage
    ? (usage.context_tokens ?? usage.total_tokens ?? usage.prompt_tokens)
    : 0
  const sessionTokens = usage?.session_tokens ?? 0
  const sessionEstimated = Boolean(usage?.session_estimated ?? true)
  const percent = usage
    ? (usage.percent ?? Math.min(100, Math.round((contextTokens / contextLimit) * 100)))
    : 0
  const compactAt = usage?.compact_at ?? Math.round(contextLimit * DEFAULT_COMPACT_AT)
  const warnAt = usage?.warn_at ?? Math.round(contextLimit * DEFAULT_WARN_AT)
  const usageLevel: UsageLevel = contextTokens >= warnAt ? 'danger' : contextTokens >= compactAt ? 'warn' : 'ok'
  const usageLabel = usage
    ? `~${formatTokenCount(contextTokens)} / ${formatTokenCount(contextLimit)} (${percent}%)`
    : '— / —'

  return {
    usage,
    setUsage,
    refreshUsage,
    contextTokens,
    sessionTokens,
    sessionEstimated,
    percent,
    compactAt,
    usageLabel,
    usageLevel,
    breakdown: usage?.breakdown ?? {},
  }
}
