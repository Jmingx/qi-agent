import { memo, useEffect, useState } from "react"

type SubTaskStatus =
  | "running"
  | "completed"
  | "failed"
  | "need_more_info"
  | "stopped"
  | "timed_out"

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
    return "已超时"
  }

  switch (status) {
    case "completed":
      return "已完成"
    case "failed":
      return "已失败"
    case "need_more_info":
      return "需补充信息"
    case "stopped":
      return "已停止"
    default:
      return "运行中"
  }
}

function getStatusClass(status: SubTaskStatus, timedOut: boolean): string {
  if (timedOut) {
    return "timed-out"
  }

  switch (status) {
    case "completed":
      return "completed"
    case "failed":
      return "failed"
    case "need_more_info":
      return "need-more-info"
    case "stopped":
      return "stopped"
    default:
      return "running"
  }
}

function formatElapsedSeconds(startedAtMs: number, nowMs: number): number {
  return Math.max(0, Math.floor((nowMs - startedAtMs) / 1000))
}

function getFallbackText(status: SubTaskStatus, timedOut: boolean): string {
  if (timedOut) {
    return "60 秒无变化，轮询已停止"
  }

  switch (status) {
    case "running":
      return "等待子任务状态更新..."
    case "failed":
      return "子任务执行失败"
    case "need_more_info":
      return "需要补充信息"
    case "stopped":
      return "子任务已停止"
    case "completed":
      return "没有返回结果"
    default:
      return "等待子任务状态更新..."
  }
}

function buildVisibleText(
  status: SubTaskStatus,
  timedOut: boolean,
  expanded: boolean,
  resultText?: string,
  reason?: string,
): string {
  const outputText = resultText?.trim() || reason?.trim() || ""

  if (status === "completed") {
    if (!resultText?.trim()) {
      return getFallbackText(status, timedOut)
    }
    if (resultText.length > 800 && !expanded) {
      return resultText.slice(0, 300)
    }
    return resultText
  }

  if (outputText) {
    return outputText
  }

  return getFallbackText(status, timedOut)
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
  const hasLongResult = Boolean(resultText && resultText.length > 800)
  const visibleText = buildVisibleText(status, timedOut, expanded, resultText, reason)
  const recentProgress = progress.slice(-5)
  const hasOverflowProgress = progress.length > recentProgress.length
  const elapsedSeconds = status === "running" && !timedOut
    ? formatElapsedSeconds(startedAtMs, clockMs)
    : formatElapsedSeconds(startedAtMs, startedAtMs)

  useEffect(() => {
    if (status !== "running" || timedOut) {
      return undefined
    }

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
          {status === "running" && !timedOut && (
            <span className="subtask-card-elapsed">已运行 {elapsedSeconds} 秒</span>
          )}
        </div>
      </div>
      <div className="subtask-card-meta">子任务 ID：{subId.slice(0, 12)}</div>
      <pre className="subtask-card-body">{visibleText}</pre>
      {progress.length > 0 && status === "running" && (
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
      {status !== "running" && progress.length > 0 && (
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
      {status === "completed" && hasLongResult && (
        <button type="button" className="subtask-card-toggle" onClick={onToggleExpanded}>
          {expanded ? "收起" : "展开"}
        </button>
      )}
      {status === "failed" && reason && <div className="subtask-card-error">{reason}</div>}
      {status === "need_more_info" && reason && <div className="subtask-card-hint">{reason}</div>}
      {status === "stopped" && reason && <div className="subtask-card-hint">{reason}</div>}
      {timedOut && <div className="subtask-card-hint">60 秒无变化，轮询已停止</div>}
    </article>
  )
}

export const SubTaskCard = memo(SubTaskCardBase)
