import { memo, useEffect, useState } from 'react'

type SubTaskStatus = 'running' | 'completed' | 'failed' | 'timed_out'

type SubTaskProgressEntry = {
  time: string
  text: string
}

type SubTaskCardProps = {
  goal: string
  subId: string
  status: SubTaskStatus
  progress: SubTaskProgressEntry[]
  resultText?: string
  reason?: string
  expanded: boolean
  timedOut: boolean
  startedAtMs: number
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
  return '⏳ 运行中'
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

function formatElapsedSeconds(startedAtMs: number, nowMs: number): number {
  return Math.max(0, Math.floor((nowMs - startedAtMs) / 1000))
}

function SubTaskCardBase({
  goal,
  subId,
  status,
  progress,
  resultText,
  reason,
  expanded,
  timedOut,
  startedAtMs,
  onToggleExpanded,
}: SubTaskCardProps) {
  const [clockMs, setClockMs] = useState(() => Date.now())
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
  const recentProgress = progress.slice(-5)
  const hasOverflowProgress = progress.length > recentProgress.length
  const elapsedSeconds = status === 'running' && !timedOut
    ? formatElapsedSeconds(startedAtMs, clockMs)
    : formatElapsedSeconds(startedAtMs, startedAtMs)

  useEffect(() => {
    if (status !== 'running' || timedOut) {
      return undefined
    }
    // 运行中每秒刷新一次，只是为了让“已运行 n 秒”保持真实，不做更重的轮询。
    const timerId = window.setInterval(() => {
      setClockMs(Date.now())
    }, 1000)
    return () => {
      window.clearInterval(timerId)
    }
  }, [status, timedOut])

  return (
    <article className={`subtask-card ${statusClass}`}>
      <div className="subtask-card-head">
        <div className="subtask-card-title">🧭 子任务：{goal}</div>
        <div className="subtask-card-status-wrap">
          <span className="subtask-card-status">{resolvedStatus}</span>
          {status === 'running' && !timedOut && (
            <span className="subtask-card-elapsed">已运行 {elapsedSeconds} 秒</span>
          )}
        </div>
      </div>
      <div className="subtask-card-meta">子任务 ID：{subId.slice(0, 12)}</div>
      <pre className="subtask-card-body">{visibleText}</pre>
      {progress.length > 0 && status === 'running' && (
        <div className="subtask-card-progress">
          {recentProgress.map((item) => (
            <div key={`${item.time}-${item.text}`} className="subtask-card-progress-line">
              <span className="subtask-card-progress-time">{item.time}</span>
              <span className="subtask-card-progress-text">{item.text}</span>
            </div>
          ))}
          {hasOverflowProgress && <div className="subtask-card-progress-more">…</div>}
        </div>
      )}
      {status !== 'running' && progress.length > 0 && (
        <details className="subtask-card-progress-fold">
          <summary>最近进度（{progress.length}）</summary>
          <div className="subtask-card-progress">
            {progress.slice(-5).map((item) => (
              <div key={`${item.time}-${item.text}`} className="subtask-card-progress-line">
                <span className="subtask-card-progress-time">{item.time}</span>
                <span className="subtask-card-progress-text">{item.text}</span>
              </div>
            ))}
            {progress.length > 5 && <div className="subtask-card-progress-more">…</div>}
          </div>
        </details>
      )}
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

export const SubTaskCard = memo(SubTaskCardBase)
