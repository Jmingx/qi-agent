type ToolResultState = {
  ok: boolean
  summary: string
  durationMs: number
}

type ToolCardProps = {
  name: string
  toolArguments: unknown
  status: 'running' | 'blocked'
  reason?: string
  result?: ToolResultState
}

function stringifyArguments(value: unknown): string {
  try {
    return JSON.stringify(value ?? {}, null, 2)
  } catch {
    return String(value ?? '')
  }
}

export function ToolCard({ name, toolArguments, status, reason, result }: ToolCardProps) {
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
    <article className={`tool-card ${statusClass}`}>
      <div className="tool-card-head">
        <div className="tool-card-name">🔧 {name}</div>
        <span className="tool-card-status">{statusLabel}</span>
      </div>
      <details className="tool-card-details">
        <summary>参数 JSON</summary>
        <pre className="tool-card-json">{stringifyArguments(toolArguments)}</pre>
      </details>
      <div className="tool-card-footer">{footerText}</div>
    </article>
  )
}
