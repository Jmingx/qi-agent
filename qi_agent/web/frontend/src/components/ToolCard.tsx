import { memo } from 'react'

type ToolResultState = {
  ok: boolean
  summary: string
  durationMs: number
}

type ToolCardProps = {
  name: string
  status: 'running' | 'blocked'
  reason?: string
  result?: ToolResultState
  compact?: boolean
}

function ToolCardBase({ name, status, reason, result, compact }: ToolCardProps) {
  const footerText = result
    ? result.ok
      ? `✓ 完成 · ${result.summary || '无摘要'} · ${result.durationMs}ms`
      : `⛔ 拦截：${result.summary || reason || '未知原因'}`
    : status === 'blocked'
      ? `⛔ 拦截：${reason || '未知原因'}`
      : '运行中'

  const statusClass = result
    ? result.ok
      ? 'success'
      : 'blocked'
    : status === 'blocked'
      ? 'blocked'
      : 'running'

  const statusLabel = result
    ? result.ok
      ? '已完成'
      : '已拦截'
    : status === 'blocked'
      ? '已拦截'
      : '运行中'

  return (
    <article className={`tool-card ${statusClass}${compact ? ' compact' : ''}`}>
      <div className="tool-card-head">
        <div className="tool-card-name">🔧 {name}</div>
        <span className="tool-card-status">{statusLabel}</span>
      </div>
      <div className="tool-card-footer">{footerText}</div>
    </article>
  )
}

export const ToolCard = memo(ToolCardBase)
