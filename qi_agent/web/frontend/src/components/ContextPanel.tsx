import { type ContextUsageResponse } from '../appModel'
import { type TurnStat } from '../hooks/useMessages'
import { formatDuration, formatTokens, percent } from '../utils/format'
import { Icon } from './ui/Icon'

type ContextPanelProps = {
  usage: ContextUsageResponse | null
  contextTokens: number
  sessionTokens: number
  sessionEstimated: boolean
  percentUsed: number
  compactAt: number
  contextLimit: number
  turnStats: TurnStat[]
  compacting: boolean
  onCompact: () => void
  onRefresh: () => void
}

const SEGMENTS: Array<{ key: string; label: string; cls: string; hint: string }> = [
  { key: 'system', label: '系统提示', cls: 'is-system', hint: '主 system prompt + 环境信息等注入' },
  { key: 'tools', label: '工具 schema', cls: 'is-tools', hint: '工具定义（模型每轮都能看到）' },
  { key: 'history', label: '对话历史', cls: 'is-history', hint: '往轮 user/assistant 消息' },
  { key: 'tool_output', label: '工具输出', cls: 'is-summary', hint: '工具返回内容（role=tool）' },
  { key: 'summary', label: '压缩摘要', cls: 'is-memory', hint: '上下文压缩后保留的摘要' },
  { key: 'input', label: '当前输入', cls: 'is-input', hint: '本轮用户消息' },
]

/**
 * 上下文面板（UI v3 §9）：回答「窗口被谁吃掉了、还剩多少、这一轮花了多少」。
 * 口径：占用 = 当前消息 + 工具 schema 估算（标 ~）；累计 = 会话真实 usage 求和。
 */
export function ContextPanel({
  usage,
  contextTokens,
  sessionTokens,
  sessionEstimated,
  percentUsed,
  compactAt,
  contextLimit,
  turnStats,
  compacting,
  onCompact,
  onRefresh,
}: ContextPanelProps) {
  const breakdown = usage?.breakdown ?? {}
  const total = Object.values(breakdown).reduce((sum, value) => sum + value, 0) || contextTokens
  const totalToolCalls = turnStats.reduce((sum, stat) => sum + stat.toolCalls, 0)
  const needCompact = contextTokens >= compactAt

  return (
    <div>
      {needCompact && (
        <div className="context-warning">
          <Icon name="alert" size={13} /> 上下文已达 {percent(contextTokens, contextLimit)}%，
          建议压缩（保留摘要 + 最近消息）。
          <button type="button" className="btn" style={{ marginTop: 6 }} onClick={onCompact} disabled={compacting}>
            {compacting ? '压缩中…' : '立即压缩上下文'}
          </button>
        </div>
      )}

      <section className="context-section">
        <div className="context-title">
          <span>上下文占用</span>
          <span className="context-num">
            ~{contextTokens.toLocaleString()} / {contextLimit.toLocaleString()} · {percentUsed}%
          </span>
        </div>
        <div className="context-bar" role="img" aria-label={`上下文占用 ${percentUsed}%`}>
          {SEGMENTS.map((segment) => {
            const value = breakdown[segment.key] ?? 0
            if (value <= 0 || total <= 0) {
              return null
            }
            return (
              <span
                key={segment.key}
                className={`context-seg ${segment.cls}`}
                style={{ width: `${(value / total) * 100}%` }}
                title={`${segment.label} ~${value.toLocaleString()} tokens`}
              />
            )
          })}
        </div>
        <div className="context-thresholds">
          压缩线 {Math.round((compactAt / contextLimit) * 100)}% · 警告线 80%
        </div>

        <div className="context-legend">
          {SEGMENTS.map((segment) => {
            const value = breakdown[segment.key] ?? 0
            return (
              <div className="context-legend-row" key={segment.key} title={segment.hint}>
                <span className={`context-swatch ${segment.cls}`} />
                <span className="context-legend-name">{segment.label}</span>
                <span className="context-legend-value">
                  ~{formatTokens(value)} · {total > 0 ? Math.round((value / total) * 100) : 0}%
                </span>
              </div>
            )
          })}
        </div>
        <div className="context-note">
          占用按「当前消息 + 工具 schema」估算（char/4，模型未返回 usage 时）。累计消耗取会话真实
          usage 求和；标注 ~ 的值是估算，不适合用于计费对账。
        </div>
      </section>

      <section className="context-section">
        <div className="context-title">
          <span>会话统计</span>
          <button type="button" className="icon-action" onClick={onRefresh} title="刷新">
            <Icon name="regenerate" size={13} /> 刷新
          </button>
        </div>
        <div className="metric-grid">
          <div className="metric">
            <div className="metric-label">会话累计 tokens</div>
            <div className="metric-value">
              {sessionTokens > 0 ? `${sessionEstimated ? '~' : ''}${sessionTokens.toLocaleString()}` : '暂无真实数据'}
            </div>
          </div>
          <div className="metric">
            <div className="metric-label">本会话轮数 / 工具调用</div>
            <div className="metric-value">
              {turnStats.length} / {totalToolCalls}
            </div>
          </div>
        </div>
      </section>

      <section className="context-section">
        <div className="context-title">
          <span>每轮明细</span>
          <span className="context-num">{turnStats.length} 轮</span>
        </div>
        {turnStats.length === 0 ? (
          <div className="context-note">本会话还没有已完成的回合（数据来自 turn/end 通知）。</div>
        ) : (
          <table className="turn-table">
            <thead>
              <tr>
                <th>轮</th>
                <th>耗时</th>
                <th>工具</th>
                <th>tokens</th>
              </tr>
            </thead>
            <tbody>
              {turnStats.slice().reverse().map((stat) => (
                <tr key={stat.turn}>
                  <td>#{stat.turn}</td>
                  <td>{formatDuration(stat.elapsedMs)}</td>
                  <td>{stat.toolCalls}</td>
                  <td>
                    {stat.estimated ? '~' : ''}
                    {formatTokens(stat.totalTokens)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
