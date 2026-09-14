import { memo, useEffect, useRef, useState, type ReactNode } from 'react'
import { type ToolEntry, type TurnUsage } from '../appModel'
import { Icon, toolIcon } from './ui/Icon'
import { formatDuration, formatTokens } from '../utils/format'

type TurnSummaryProps = {
  tools: ToolEntry[]
  elapsedMs?: number
  usage?: TurnUsage
  llmCalls?: number
  running: boolean
  children: ReactNode
}

const AUTO_COLLAPSE_DELAY_MS = 2000

/**
 * 助手回合摘要行（UI v3 §5.3 修正 1/2）。
 * 一行回答「几步、哪些工具、成没成、多久、多少 token」——不展开也能看懂 agent 干了什么；
 * 完成后**延迟 2s 再收起**（旧实现完成瞬间立即折叠，用户回头什么都看不到）。
 */
function TurnSummaryBase({ tools, elapsedMs, usage, llmCalls, running, children }: TurnSummaryProps) {
  const [expanded, setExpanded] = useState(tools.length > 0)
  const userToggledRef = useRef(false)
  // 待决审批：卡片（含按钮）在对话流里，收起就等于用户点不到
  const hasPendingApproval = tools.some((tool) => tool.approval?.state === 'pending')

  useEffect(() => {
    if (tools.length > 0 || hasPendingApproval) {
      setExpanded(true)
    }
  }, [hasPendingApproval, tools.length])

  useEffect(() => {
    if (running || userToggledRef.current || tools.length === 0) {
      return undefined
    }
    // 有待决审批时不自动收起：收起后按钮就滚没了（审批卡片在对话流里）
    if (hasPendingApproval) {
      return undefined
    }
    const timer = window.setTimeout(() => {
      if (!userToggledRef.current) {
        setExpanded(false)
      }
    }, AUTO_COLLAPSE_DELAY_MS)
    return () => window.clearTimeout(timer)
  }, [hasPendingApproval, running, tools.length])

  if (tools.length === 0) {
    return <div className="turn">{children}</div>
  }

  const failed = tools.filter((tool) => tool.result && !tool.result.ok).length
  const blocked = tools.filter((tool) => tool.status === 'blocked').length
  // 审批计数（M2-a）：折叠状态下也要一眼看到「这一步被审批过、结局是什么」，
  // 否则记录只存在于展开后的行里 —— 等于用户看不到（2026-09-14 UX 反馈）。
  const approvals = tools.map((tool) => tool.approval).filter((item) => item !== undefined)
  const approvalParts = [
    { state: 'pending' as const, label: '待审批', cls: 'chip--warn' },
    { state: 'allowed' as const, label: '已允许', cls: 'chip--ok' },
    { state: 'denied' as const, label: '已拒绝', cls: 'chip--danger' },
    { state: 'timeout' as const, label: '超时拒绝', cls: 'chip--danger' },
  ]
    .map((kind) => ({ ...kind, count: approvals.filter((item) => item.state === kind.state).length }))
    .filter((kind) => kind.count > 0)
  const tokenLabel = usage && (usage.total_tokens ?? 0) > 0
    ? `${usage.estimated ? '~' : ''}${formatTokens(usage.total_tokens)} tok`
    : ''
  const statParts = [
    createdAtLabel(tools),
    elapsedMs !== undefined ? formatDuration(elapsedMs) : '',
    tokenLabel,
    llmCalls && llmCalls > 1 ? `${llmCalls} 次 LLM` : '',
  ].filter(Boolean)

  const summary = running
    ? `执行中… ${tools.length} 步`
    : `已完成 ${tools.length} 步`

  return (
    <div className="turn">
      <button
        type="button"
        className="turn-summary"
        aria-expanded={expanded}
        onClick={() => {
          userToggledRef.current = true
          setExpanded((current) => !current)
        }}
      >
        <Icon name="chevron-right" size={12} className="summary-chevron" />
        <span className="summary-step">{summary}</span>
        <span className="summary-icons">
          {tools.slice(0, 6).map((tool) => (
            <span
              key={tool.id}
              className={tool.result && !tool.result.ok ? 'is-failed' : undefined}
              title={tool.name}
            >
              <Icon name={toolIcon(tool.name)} size={13} />
            </span>
          ))}
        </span>
        {(failed > 0 || blocked > 0) && (
          <span className="chip chip--danger">
            <Icon name="alert" size={11} /> {failed + blocked} 失败/拦截
          </span>
        )}
        {approvalParts.map((kind) => (
          <span key={kind.state} className={`chip ${kind.cls}`} title="展开可见审批记录（档位/命令/决策时间）">
            <Icon name="alert" size={11} /> {kind.label}
            {kind.count > 1 ? ` ${kind.count}` : ''}
          </span>
        ))}
        {statParts.length > 0 && (
          <>
            <span className="summary-dot">·</span>
            <span className="summary-stat">{statParts.join(' · ')}</span>
          </>
        )}
      </button>
      {expanded && <div className="steps">{children}</div>}
    </div>
  )
}

function createdAtLabel(tools: ToolEntry[]): string {
  const first = tools[0]?.time
  return first ? first : ''
}

export const TurnSummary = memo(TurnSummaryBase)
