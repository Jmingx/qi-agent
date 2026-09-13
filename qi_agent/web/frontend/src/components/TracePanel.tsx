import { useMemo, useState } from 'react'
import { type StreamEntry } from '../appModel'
import { formatDuration, formatTokens } from '../utils/format'
import { Icon, toolIcon } from './ui/Icon'

type TracePanelProps = {
  entries: StreamEntry[]
  jaegerUrl: string
  onOpenJaeger: (traceId: string) => void
}

type Filter = 'all' | 'failed' | 'tools' | 'subtask'

const FILTERS: Array<{ key: Filter; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'failed', label: '仅失败' },
  { key: 'tools', label: '仅工具' },
  { key: 'subtask', label: '仅子任务' },
]

/**
 * 调用轨迹面板（UI v3 §8 L3）。
 * 定位：产品视角的「做了哪些事」——人话标签 + 参数摘要 + 耗时 + 状态 + token；
 * 精确 span 时序仍交给 Jaeger（点深挖跳转），不在这里重造瀑布图。
 */
export function TracePanel({ entries, jaegerUrl, onOpenJaeger }: TracePanelProps) {
  const [filter, setFilter] = useState<Filter>('all')

  const turns = useMemo(() => entries.filter((entry) => entry.kind === 'assistant-turn'), [entries])
  const subtasks = useMemo(() => entries.filter((entry) => entry.kind === 'subtask'), [entries])
  const totalToolCalls = turns.reduce(
    (sum, turn) => sum + (turn.kind === 'assistant-turn' ? turn.tools.length : 0),
    0,
  )
  const totalFailures = turns.reduce((sum, turn) => {
    if (turn.kind !== 'assistant-turn') {
      return sum
    }
    return sum + turn.tools.filter((tool) => (tool.result && !tool.result.ok) || tool.status === 'blocked').length
  }, 0)

  const visibleTurns = turns.filter((turn) => {
    if (turn.kind !== 'assistant-turn') {
      return false
    }
    if (filter === 'tools') {
      return turn.tools.length > 0
    }
    if (filter === 'failed') {
      return turn.error !== undefined
        || turn.tools.some((tool) => (tool.result && !tool.result.ok) || tool.status === 'blocked')
    }
    if (filter === 'subtask') {
      return false
    }
    return true
  })

  const showSubtasks = filter === 'all' || filter === 'subtask'

  if (turns.length === 0 && subtasks.length === 0) {
    return <div className="trace-empty">本会话还没有调用记录。发一条消息后，这里会显示每一轮做了什么。</div>
  }

  return (
    <div>
      <div className="metric-grid" style={{ marginBottom: 12 }}>
        <div className="metric">
          <div className="metric-label">回合 / 工具调用</div>
          <div className="metric-value">{turns.length} / {totalToolCalls}</div>
        </div>
        <div className="metric">
          <div className="metric-label">失败或被拦截</div>
          <div className="metric-value">{totalFailures}</div>
        </div>
      </div>

      <div className="trace-filter">
        {FILTERS.map((item) => (
          <button
            key={item.key}
            type="button"
            className={`filter-chip${filter === item.key ? ' is-active' : ''}`}
            onClick={() => setFilter(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>

      {visibleTurns.map((turn) => {
        if (turn.kind !== 'assistant-turn') {
          return null
        }
        const tokens = turn.usage?.total_tokens ?? 0
        return (
          <div className="trace-turn" key={turn.id}>
            <div className="trace-turn-head">
              <span className="trace-turn-title">#{turn.turn || '—'}</span>
              <span className="chip">
                {turn.tools.length} 步
              </span>
              {turn.error && <span className="chip chip--danger"><Icon name="alert" size={11} /> 出错</span>}
              <span className="trace-turn-meta">
                {[
                  turn.elapsedMs !== undefined ? formatDuration(turn.elapsedMs) : '',
                  tokens > 0 ? `${turn.usage?.estimated ? '~' : ''}${formatTokens(tokens)} tok` : '',
                  turn.time ?? '',
                ].filter(Boolean).join(' · ')}
              </span>
              {turn.traceId && (
                <button
                  type="button"
                  className="icon-action"
                  title="在 Jaeger 中查看 span 瀑布"
                  onClick={() => onOpenJaeger(turn.traceId as string)}
                >
                  <Icon name="link" size={12} />
                </button>
              )}
            </div>

            {turn.tools.length > 0 && (
              <div className="steps" style={{ marginTop: 6 }}>
                {turn.tools.map((tool) => {
                  const failed = (tool.result && !tool.result.ok) || tool.status === 'blocked'
                  return (
                    <div className={`step-head${failed ? '' : ''}`} key={tool.id}>
                      <span className={`step-icon${failed ? ' is-failed' : ''}`}>
                        <Icon name={toolIcon(tool.name)} size={13} />
                      </span>
                      <span className="step-name">{tool.name}</span>
                      {tool.result ? (
                        <span className="step-stat">{formatDuration(tool.result.durationMs)}</span>
                      ) : (
                        <span className="chip chip--running">运行中</span>
                      )}
                      {failed && <span className="chip chip--danger">失败/拦截</span>}
                    </div>
                  )
                })}
              </div>
            )}

            {turn.body && (
              <div className="context-note" style={{ marginTop: 4 }}>
                {turn.body.replace(/\s+/g, ' ').slice(0, 90)}
                {turn.body.length > 90 ? '…' : ''}
              </div>
            )}
          </div>
        )
      })}

      {showSubtasks && subtasks.map((entry) => {
        if (entry.kind !== 'subtask') {
          return null
        }
        return (
          <div className="trace-turn" key={entry.id}>
            <div className="trace-turn-head">
              <Icon name="git-branch" size={13} />
              <span className="trace-turn-title">子任务</span>
              <span className="chip">{entry.status}</span>
              <span className="trace-turn-meta">{entry.time ?? ''}</span>
            </div>
            <div className="context-note" style={{ marginTop: 4 }}>{entry.goal}</div>
          </div>
        )
      })}

      {visibleTurns.length === 0 && !showSubtasks && (
        <div className="trace-empty">当前筛选没有记录。</div>
      )}

      {jaegerUrl && (
        <div className="context-note" style={{ marginTop: 12 }}>
          需要精确的 span 时序时，点每轮的链图标跳 Jaeger；评测数据看 Opik（⋯ 菜单里）。
        </div>
      )}
    </div>
  )
}
