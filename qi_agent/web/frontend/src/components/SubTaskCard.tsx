type SubTaskStatus = 'running' | 'completed' | 'failed' | 'timed_out'

type SubTaskCardProps = {
  goal: string
  subId: string
  status: SubTaskStatus
  resultText?: string
  reason?: string
  expanded: boolean
  timedOut: boolean
  onToggleExpanded: () => void
}

function getStatusLabel(status: SubTaskStatus, timedOut: boolean): string {
  if (timedOut) {
    return '已停止轮询'
  }
  if (status === 'completed') {
    return '已完成'
  }
  if (status === 'failed') {
    return '已失败'
  }
  return '运行中'
}

function getStatusClass(status: SubTaskStatus, timedOut: boolean): string {
  if (timedOut) {
    return 'timed-out'
  }
  if (status === 'completed') {
    return 'completed'
  }
  if (status === 'failed') {
    return 'failed'
  }
  return 'running'
}

export function SubTaskCard({
  goal,
  subId,
  status,
  resultText,
  reason,
  expanded,
  timedOut,
  onToggleExpanded,
}: SubTaskCardProps) {
  const resolvedStatus = getStatusLabel(status, timedOut)
  const statusClass = getStatusClass(status, timedOut)
  const outputText = resultText?.trim() || reason?.trim() || ''
  const hasLongResult = Boolean(resultText && resultText.length > 300)
  const previewText = resultText ? resultText.slice(0, 300) : ''
  const visibleText = status === 'completed'
    ? expanded && resultText
      ? resultText
      : previewText || outputText || '没有返回结果'
    : outputText || '等待子任务状态更新...'

  return (
    <article className={`subtask-card ${statusClass}`}>
      <div className="subtask-card-head">
        <div className="subtask-card-title">🚀 子任务 {goal}</div>
        <span className="subtask-card-status">{resolvedStatus}</span>
      </div>
      <div className="subtask-card-meta">子任务 ID：{subId.slice(0, 12)}</div>
      <pre className="subtask-card-body">{visibleText}</pre>
      {status === 'completed' && hasLongResult && (
        <button type="button" className="subtask-card-toggle" onClick={onToggleExpanded}>
          {expanded ? '收起' : '展开'}
        </button>
      )}
      {status === 'failed' && reason && <div className="subtask-card-error">{reason}</div>}
      {timedOut && <div className="subtask-card-hint">60 秒无变化，轮询已停止</div>}
    </article>
  )
}
