import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { MessageBubble } from './MessageBubble'
import { StepRow } from './StepRow'
import { ApprovalCard } from './ApprovalCard'
import { TurnSummary } from './TurnSummary'
import { SubTaskCard } from './SubTaskCard'
import { MarkdownView } from './MarkdownView'
import { Icon } from './ui/Icon'
import {
  normalizeSearchText,
  type AssistantTurnEntry,
  type PendingScrollTarget,
  type StreamEntry,
  type SubTaskEntry,
  type TextEntry,
  type ToolEntry,
} from '../appModel'
import { formatDayLabel } from '../utils/format'

type MessageListProps = {
  entries: StreamEntry[]
  highlightedMessageId: number | null
  loading: boolean
  jaegerUrl: string
  scrollTarget: PendingScrollTarget
  onScrollTargetHandled: () => void
  onHighlightMessage: (messageId: number) => void
  onCopyMessage: (text: string) => void
  onEditMessage: (entry: TextEntry) => void
  onRetryMessage: (messageId: number, text: string) => void
  onToggleSubTaskExpanded: (subId: string) => void
  onUseSample: (prompt: string) => void
  /** 审批决策（M2-a：卡片就在对话流里，按 approval_id 回执） */
  onRespondApproval: (approvalId: string, choice: string) => void | Promise<void>
}

const STICK_THRESHOLD_PX = 80
const SAMPLES = [
  '解释这个仓库的目录结构，指出每个包的职责',
  '跑一遍后端测试并总结失败项与原因',
  '看下当前上下文占用，告诉我哪一部分最占 token',
]

function findTargetEntry(entries: StreamEntry[], target: PendingScrollTarget): TextEntry | null {
  if (!target) {
    return null
  }
  const normalizedContent = normalizeSearchText(target.content)
  const normalizedQuery = normalizeSearchText(target.query || target.content)
  for (const entry of entries) {
    if (entry.kind !== 'message' || entry.role !== 'user') {
      continue
    }
    const normalizedEntry = normalizeSearchText(entry.content)
    if (normalizedEntry.includes(normalizedContent) || normalizedEntry.includes(normalizedQuery)) {
      return entry
    }
  }
  return null
}

/**
 * 消息列表（UI v3 §7）：
 * - 移除自制虚拟列表（高度估算 → 滚动跳变），改由 CSS `content-visibility` 承担离屏成本；
 * - 滚动改为 stick-to-bottom：只有用户停在底部才跟随，上滑即解锁并给「新消息」胶囊。
 */
export function MessageList({
  entries,
  highlightedMessageId,
  loading,
  jaegerUrl,
  scrollTarget,
  onScrollTargetHandled,
  onHighlightMessage,
  onCopyMessage,
  onEditMessage,
  onRetryMessage,
  onToggleSubTaskExpanded,
  onUseSample,
  onRespondApproval,
}: MessageListProps) {
  const containerRef = useRef<HTMLElement | null>(null)
  const stickRef = useRef(true)
  const frameRef = useRef<number | null>(null)
  const lastCountRef = useRef(0)
  const nodeRefs = useRef(new Map<number, HTMLDivElement | null>())
  const [pendingCount, setPendingCount] = useState(0)
  const [showJump, setShowJump] = useState(false)

  const distanceToBottom = (): number => {
    const container = containerRef.current
    if (!container) {
      return 0
    }
    return container.scrollHeight - container.scrollTop - container.clientHeight
  }

  const scrollToBottom = (smooth = false): void => {
    const container = containerRef.current
    if (!container) {
      return
    }
    stickRef.current = true
    setPendingCount(0)
    setShowJump(false)
    container.scrollTo({ top: container.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
  }

  const handleScroll = (): void => {
    if (frameRef.current !== null) {
      return
    }
    frameRef.current = window.requestAnimationFrame(() => {
      frameRef.current = null
      const distance = distanceToBottom()
      const stuck = distance < STICK_THRESHOLD_PX
      stickRef.current = stuck
      if (stuck) {
        setPendingCount(0)
        setShowJump(false)
      } else {
        setShowJump(true)
      }
    })
  }

  // 新内容到达：停在底部才跟随；否则累计「新消息」数
  useLayoutEffect(() => {
    if (loading) {
      return
    }
    const previousCount = lastCountRef.current
    lastCountRef.current = entries.length
    if (stickRef.current) {
      scrollToBottom()
      return
    }
    if (entries.length > previousCount) {
      setPendingCount((current) => current + (entries.length - previousCount))
    }
  }, [entries, loading])

  // 搜索结果跳转：直接按 DOM 节点居中（去虚拟化后无需高度估算）
  useEffect(() => {
    if (!scrollTarget) {
      return
    }
    const target = findTargetEntry(entries, scrollTarget)
    if (!target) {
      onScrollTargetHandled()
      return
    }
    const node = nodeRefs.current.get(target.id)
    if (node) {
      node.scrollIntoView({ block: 'center' })
      onHighlightMessage(target.id)
    }
    onScrollTargetHandled()
  }, [entries, onHighlightMessage, onScrollTargetHandled, scrollTarget])

  useEffect(() => () => {
    if (frameRef.current !== null) {
      window.cancelAnimationFrame(frameRef.current)
    }
  }, [])

  const registerNode = (id: number) => (node: HTMLDivElement | null): void => {
    if (node) {
      nodeRefs.current.set(id, node)
    } else {
      nodeRefs.current.delete(id)
    }
  }

  const items = useMemo(() => entries.map((entry, index) => {
    const previous = index > 0 ? entries[index - 1] : null
    const currentDay = formatDayLabel(entry.kind === 'message' || entry.kind === 'tool' || entry.kind === 'assistant-turn' ? entry.time : undefined)
    const previousDay = previous && (previous.kind === 'message' || previous.kind === 'tool' || previous.kind === 'assistant-turn')
      ? formatDayLabel(previous.time)
      : null
    return {
      entry,
      daySeparator: currentDay && currentDay !== previousDay ? currentDay : null,
    }
  }), [entries])

  const renderEntry = (entry: StreamEntry): JSX.Element | null => {
    if (entry.kind === 'assistant-turn') {
      return (
        <div key={entry.id} ref={registerNode(entry.id)} className={`msg msg--assistant${highlightedMessageId === entry.id ? ' highlighted' : ''}`}>
          <TurnContainer entry={entry} jaegerUrl={jaegerUrl} onCopyMessage={onCopyMessage} onRespondApproval={onRespondApproval} />
        </div>
      )
    }
    if (entry.kind === 'tool') {
      const synthetic: AssistantTurnEntry = {
        id: entry.id,
        kind: 'assistant-turn',
        sessionId: entry.sessionId,
        turn: 0,
        body: '',
        bodyState: 'completed',
        tools: [entry],
      }
      return (
        <div key={entry.id} ref={registerNode(entry.id)} className="msg msg--assistant">
          <TurnContainer entry={synthetic} jaegerUrl={jaegerUrl} onCopyMessage={onCopyMessage} />
        </div>
      )
    }
    if (entry.kind === 'subtask') {
      return (
        <div key={entry.id} ref={registerNode(entry.id)} className="msg msg--assistant">
          <SubTaskCardWrapper entry={entry} onToggle={onToggleSubTaskExpanded} />
        </div>
      )
    }
    return (
      <div key={entry.id} ref={registerNode(entry.id)}>
        <MessageBubble
          entry={entry}
          highlighted={highlightedMessageId === entry.id}
          jaegerUrl={jaegerUrl}
          onCopy={onCopyMessage}
          onEdit={onEditMessage}
          onRetry={(message) => onRetryMessage(message.id, message.content)}
        />
      </div>
    )
  }

  return (
    <main ref={containerRef} className="messages" onScroll={handleScroll}>
      <div className="messages-inner">
        {loading && (
          <div className="skeleton" aria-label="正在恢复会话">
            <span className="skeleton-line w-60" />
            <span className="skeleton-line w-90" />
            <span className="skeleton-line w-40" />
          </div>
        )}

        {!loading && entries.length === 0 && (
          <div className="empty-state">
            <div className="empty-logo">qi</div>
            <h2>qi-agent Web Shell</h2>
            <p>直接提问，或从左侧恢复历史会话。断线会自动重连并补齐历史。</p>
            <div className="empty-samples">
              {SAMPLES.map((sample) => (
                <button
                  key={sample}
                  type="button"
                  className="empty-sample"
                  onClick={() => onUseSample(sample)}
                >
                  {sample}
                </button>
              ))}
            </div>
          </div>
        )}

        {!loading && items.map((item) => (
          <div key={`wrap-${item.entry.id}`}>
            {item.daySeparator && <div className="day-sep">{item.daySeparator}</div>}
            {renderEntry(item.entry)}
          </div>
        ))}
      </div>

      {showJump && (
        <button type="button" className="jump-latest" onClick={() => scrollToBottom(true)}>
          <Icon name="arrow-down" size={14} />
          {pendingCount > 1 ? `${pendingCount} 条新消息` : '回到底部'}
        </button>
      )}
    </main>
  )
}

/** 回合容器：摘要行（步骤统计）+ 展开的步骤时间线 + markdown 正文。 */
function TurnContainer({
  entry,
  jaegerUrl,
  onCopyMessage,
  onRespondApproval,
}: {
  entry: AssistantTurnEntry
  jaegerUrl: string
  onCopyMessage: (text: string) => void
  onRespondApproval: (approvalId: string, choice: string) => void | Promise<void>
}) {
  const running = entry.bodyState === 'streaming'
  return (
    <div className="turn">
      <TurnSummary
        tools={entry.tools}
        elapsedMs={entry.elapsedMs}
        usage={entry.usage}
        llmCalls={entry.llmCalls}
        running={running}
      >
        {entry.tools.map((tool: ToolEntry) => (
          <Fragment key={tool.id}>
            {/* 审批卡片长在触发它的工具行上方：决策后原地变记录，回看对话时还在（2026-09-14） */}
            {tool.approval && (
              <ApprovalCard
                approval={tool.approval}
                onRespond={(choice) => onRespondApproval(tool.approval?.approvalId ?? '', choice)}
              />
            )}
            <StepRow tool={tool} />
          </Fragment>
        ))}
      </TurnSummary>

      {entry.error && <div className="turn-error">运行出错：{entry.error}</div>}

      {entry.body && (
        <div className="turn-body">
          <MarkdownView content={entry.body} streaming={running} />
        </div>
      )}

      <div className="msg-actions">
        {entry.body && (
          <button
            type="button"
            className="icon-action"
            onClick={() => onCopyMessage(entry.body)}
            title="复制回复"
            aria-label="复制回复"
          >
            <Icon name="copy" size={13} /> 复制
          </button>
        )}
        {entry.traceId && (
          <button
            type="button"
            className="icon-action"
            title="在 Jaeger 中查看本次调用链"
            aria-label="在 Jaeger 中查看本次调用链"
            onClick={() => window.open(`${jaegerUrl}/trace/${entry.traceId}`, '_blank', 'noopener,noreferrer')}
          >
            <Icon name="link" size={13} /> 调用链
          </button>
        )}
        {entry.time && <span className="msg-time">{entry.time}</span>}
      </div>
    </div>
  )
}

function SubTaskCardWrapper({
  entry,
  onToggle,
}: {
  entry: SubTaskEntry
  onToggle: (subId: string) => void
}) {
  return (
    <SubTaskCard
      goal={entry.goal}
      subId={entry.subId}
      status={entry.status}
      progress={entry.progress}
      resultText={entry.resultText}
      reason={entry.reason}
      expanded={Boolean(entry.expanded)}
      timedOut={Boolean(entry.timedOut)}
      startedAtMs={entry.startedAtMs}
      onToggleExpanded={() => onToggle(entry.subId)}
    />
  )
}
