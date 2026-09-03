import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { MessageBubble } from './MessageBubble'
import { ToolCard } from './ToolCard'
import { ToolRunHeader } from './ToolRunHeader'
import { SubTaskCard } from './SubTaskCard'
import {
  normalizeSearchText,
  type PendingScrollTarget,
  type StreamEntry,
  type ToolEntry,
  type SubTaskEntry,
  type TextEntry,
} from '../appModel'

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
}

const ROW_GAP = 16
const OVERSCAN_PX = 900
const TOOL_RUN_GAP = 8
const TOOL_RUN_PADDING_Y = 12

function estimateTextHeight(entry: TextEntry): number {
  const base = entry.role === 'system' ? 56 : 82
  const charsPerLine = entry.role === 'system' ? 52 : 66
  const lines = Math.max(1, Math.ceil(entry.content.length / charsPerLine))
  const timeHeight = entry.time ? 18 : 0
  return Math.min(420, base + (lines * 18) + timeHeight)
}

function estimateToolHeight(entry: StreamEntry): number {
  // v2 工具行已无参数 JSON 块：行高 ≈ 名称行 + 结果摘要行 + 间距（紧凑估算，ResizeObserver 实测兜底）
  const footerRows = entry.kind === 'tool' && entry.result ? 2 : 1
  return 56 + (footerRows * 16)
}

function estimateToolRunHeight(toolEntries: ToolEntry[]): number {
  if (toolEntries.length === 0) {
    return 0
  }

  const toolHeights = toolEntries.reduce((sum, entry) => sum + estimateToolHeight(entry), 0)
  const gaps = TOOL_RUN_GAP * (toolEntries.length - 1)
  // 工具活动容器只是把原有工具卡平铺到同一个视觉块里，所以估算时直接叠加单行高度。
  return toolHeights + gaps + (TOOL_RUN_PADDING_Y * 2)
}

function estimateSubTaskHeight(entry: SubTaskEntry): number {
  const progressRows = Math.min(6, entry.progress.length)
  const contentLength = (entry.resultText?.length ?? 0) + (entry.reason?.length ?? 0) + entry.goal.length
  const bodyRows = Math.max(3, Math.ceil(contentLength / 96))
  const expandedBoost = entry.expanded && entry.resultText ? Math.min(220, Math.ceil(entry.resultText.length / 24)) : 0
  return Math.min(620, 210 + (progressRows * 18) + (bodyRows * 14) + expandedBoost)
}

type RenderItem =
  | { kind: 'message'; entry: TextEntry; id: number }
  | {
    kind: 'assistant-turn'
    toolEntries: ToolEntry[]
    noteEntries: TextEntry[]
    textEntries: TextEntry[]
    id: number
  }
  | { kind: 'subtask'; entry: SubTaskEntry; id: number }

// 这里只能按“连续 tool entry 段 + 前后紧邻的 assistant 文本”切回合，因为数据流里
// 没有 turn 字段可直接依赖。一次用户提问 → 若干工具 → 最终回复，在对话流中的形态：
//   user
//   assistant 文本（中间话：模型工具轮次间的过渡输出，如“我来帮你查一下…”）
//   tool ×N（连续）
//   assistant 文本（最终回复，流式中可能晚到）
// 中间话收进回合容器顶部注记区（turn-notes），最终回复收进文本区；两者与工具段
// 一起构成一个 assistant-turn。纯文本回复（无工具）仍是独立 message。
function splitTurnItems(entries: StreamEntry[]): RenderItem[] {
  const items: RenderItem[] = []
  let index = 0

  while (index < entries.length) {
    const entry = entries[index]

    // assistant 文本且后面紧跟 tool 段 → 它是工具轮次的中间话（注记）
    if (
      entry.kind === 'message'
      && entry.role === 'assistant'
      && index + 1 < entries.length
      && entries[index + 1].kind === 'tool'
    ) {
      const noteEntries: TextEntry[] = []
      while (
        index < entries.length
        && entries[index].kind === 'message'
        && entries[index].role === 'assistant'
        && index + 1 < entries.length
        && entries[index + 1].kind === 'tool'
      ) {
        noteEntries.push(entries[index] as TextEntry)
        index += 1
      }
      // 吸收中间话后必定是 tool 段开头，走下面的统一收集逻辑
      const toolEntries: ToolEntry[] = []
      while (index < entries.length && entries[index].kind === 'tool') {
        toolEntries.push(entries[index] as ToolEntry)
        index += 1
      }
      const textEntries: TextEntry[] = []
      while (
        index < entries.length
        && entries[index].kind === 'message'
        && (entries[index] as TextEntry).role === 'assistant'
      ) {
        textEntries.push(entries[index] as TextEntry)
        index += 1
      }
      items.push({
        kind: 'assistant-turn',
        toolEntries,
        noteEntries,
        textEntries,
        id: toolEntries[0].id,
      })
      continue
    }

    if (entry.kind === 'tool') {
      // 攒连续 tool 段
      const toolEntries: ToolEntry[] = []
      while (index < entries.length && entries[index].kind === 'tool') {
        toolEntries.push(entries[index] as ToolEntry)
        index += 1
      }
      // 吸收紧随其后的连续 assistant 文本（最终回复，流式中可能晚到）
      const textEntries: TextEntry[] = []
      while (
        index < entries.length
        && entries[index].kind === 'message'
        && (entries[index] as TextEntry).role === 'assistant'
      ) {
        textEntries.push(entries[index] as TextEntry)
        index += 1
      }
      items.push({
        kind: 'assistant-turn',
        toolEntries,
        noteEntries: [],
        textEntries,
        id: toolEntries[0].id,
      })
      continue
    }

    if (entry.kind === 'message') {
      items.push({ kind: 'message', entry, id: entry.id })
      index += 1
      continue
    }

    items.push({ kind: 'subtask', entry, id: entry.id })
    index += 1
  }

  return items
}

function estimateAssistantTurnHeight(item: {
  toolEntries: ToolEntry[]
  noteEntries: TextEntry[]
  textEntries: TextEntry[]
}): number {
  // 注记摘要行 ~28px（+展开全文行）+ 折叠 header ~40px + 工具区（展开态估算，
  // 折叠后由 ResizeObserver 实测修正）+ 文本区（纯文本 pre-wrap，按字符估算行数）
  const notesHeight = item.noteEntries.length > 0 ? 30 : 0
  const headHeight = 40
  const toolsHeight = estimateToolRunHeight(item.toolEntries)
  const textHeight = item.textEntries.reduce((sum, textEntry) => {
    const rows = Math.max(1, Math.ceil(textEntry.content.length / 66))
    return sum + 18 + (rows * 20)
  }, 0)
  return Math.min(1150, notesHeight + headHeight + toolsHeight + textHeight + 24)
}

function estimateRenderItemHeight(item: RenderItem): number {
  if (item.kind === 'message') {
    return estimateTextHeight(item.entry)
  }
  if (item.kind === 'assistant-turn') {
    return estimateAssistantTurnHeight(item)
  }
  return estimateSubTaskHeight(item.entry)
}

function findFirstVisibleIndex(offsets: number[], heights: number[], scrollTop: number): number {
  let low = 0
  let high = offsets.length - 1
  let answer = 0

  while (low <= high) {
    const mid = Math.floor((low + high) / 2)
    const bottom = offsets[mid] + heights[mid]
    if (bottom >= scrollTop) {
      answer = mid
      high = mid - 1
    } else {
      low = mid + 1
    }
  }

  return answer
}

function findLastVisibleIndex(offsets: number[], scrollBottom: number): number {
  let low = 0
  let high = offsets.length - 1
  let answer = offsets.length - 1

  while (low <= high) {
    const mid = Math.floor((low + high) / 2)
    if (offsets[mid] <= scrollBottom) {
      answer = mid
      low = mid + 1
    } else {
      high = mid - 1
    }
  }

  return answer
}

function findTargetEntry(entries: StreamEntry[], target: PendingScrollTarget): TextEntry | null {
  if (!target) {
    return null
  }

  const normalizedQuery = normalizeSearchText(target.query || target.content)
  const normalizedContent = normalizeSearchText(target.content)
  for (const entry of entries) {
    if (entry.kind !== 'message') {
      continue
    }
    const normalizedEntry = normalizeSearchText(entry.content)
    if (normalizedEntry.includes(normalizedContent) || normalizedEntry.includes(normalizedQuery)) {
      return entry
    }
  }
  return null
}

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
}: MessageListProps) {
  const containerRef = useRef<HTMLElement | null>(null)
  const measuredHeightsRef = useRef(new Map<number, number>())
  const resizeObserversRef = useRef(new Map<number, ResizeObserver>())
  const skipNextAutoScrollRef = useRef(false)
  const [scrollTop, setScrollTop] = useState(0)
  const [viewportHeight, setViewportHeight] = useState(1)
  const [measureVersion, setMeasureVersion] = useState(0)
  const renderItems = useMemo(() => splitTurnItems(entries), [entries])

  const heights = useMemo(() => renderItems.map((item) => (
    measuredHeightsRef.current.get(item.id) ?? estimateRenderItemHeight(item)
  )), [measureVersion, renderItems])

  const offsets = useMemo(() => {
    const next: number[] = []
    let offset = 0
    renderItems.forEach((_, index) => {
      next[index] = offset
      offset += heights[index]
      if (index < renderItems.length - 1) {
        offset += ROW_GAP
      }
    })
    return next
  }, [heights, renderItems])

  const totalHeight = useMemo(() => {
    if (renderItems.length === 0) {
      return 0
    }
    return offsets[renderItems.length - 1] + heights[renderItems.length - 1]
  }, [heights, offsets, renderItems.length])

  const handleRowRef = useMemo(() => {
    return (entryId: number) => (node: HTMLDivElement | null): void => {
      const prevObserver = resizeObserversRef.current.get(entryId)
      if (prevObserver) {
        prevObserver.disconnect()
        resizeObserversRef.current.delete(entryId)
      }

      if (!node) {
        return
      }

      const measure = (): void => {
        const nextHeight = Math.ceil(node.getBoundingClientRect().height)
        if (nextHeight <= 0) {
          return
        }
        if (measuredHeightsRef.current.get(entryId) !== nextHeight) {
          measuredHeightsRef.current.set(entryId, nextHeight)
          setMeasureVersion((version) => version + 1)
        }
      }

      measure()
      if (typeof ResizeObserver !== 'undefined') {
        const observer = new ResizeObserver(() => {
          measure()
        })
        observer.observe(node)
        resizeObserversRef.current.set(entryId, observer)
      }
    }
  }, [])

  const handleScroll = (): void => {
    const container = containerRef.current
    if (!container) {
      return
    }
    setScrollTop(container.scrollTop)
  }

  useEffect(() => {
    const container = containerRef.current
    if (!container) {
      return undefined
    }

    const updateViewport = (): void => {
      setViewportHeight(Math.max(1, container.clientHeight))
    }

    updateViewport()

    if (typeof ResizeObserver !== 'undefined') {
      const observer = new ResizeObserver(updateViewport)
      observer.observe(container)
      return () => {
        observer.disconnect()
      }
    }

    window.addEventListener('resize', updateViewport)
    return () => {
      window.removeEventListener('resize', updateViewport)
    }
  }, [])

  useLayoutEffect(() => {
    if (skipNextAutoScrollRef.current) {
      skipNextAutoScrollRef.current = false
      return
    }
    if (loading || renderItems.length === 0 || scrollTarget) {
      return
    }
    const container = containerRef.current
    if (!container) {
      return
    }
    const nextScrollTop = Math.max(0, totalHeight - viewportHeight)
    container.scrollTop = nextScrollTop
    setScrollTop(nextScrollTop)
  }, [loading, renderItems, scrollTarget, totalHeight, viewportHeight])

  useLayoutEffect(() => {
    if (!scrollTarget || renderItems.length === 0) {
      return
    }
    const container = containerRef.current
    if (!container) {
      return
    }

    const matchedEntry = findTargetEntry(entries, scrollTarget)
    if (!matchedEntry) {
      const nextScrollTop = Math.max(0, totalHeight - viewportHeight)
      container.scrollTop = nextScrollTop
      setScrollTop(nextScrollTop)
      skipNextAutoScrollRef.current = true
      onScrollTargetHandled()
      return
    }

    const index = renderItems.findIndex((item) => item.id === matchedEntry.id)
    if (index < 0) {
      return
    }

    const offsetTop = offsets[index]
    const rowHeight = heights[index]
    const nextScrollTop = Math.max(
      0,
      Math.min(totalHeight - viewportHeight, offsetTop - Math.max(0, (viewportHeight - rowHeight) / 2)),
    )
    container.scrollTop = nextScrollTop
    setScrollTop(nextScrollTop)
    onHighlightMessage(matchedEntry.id)
    skipNextAutoScrollRef.current = true
    onScrollTargetHandled()
  }, [heights, offsets, onHighlightMessage, onScrollTargetHandled, renderItems, scrollTarget, totalHeight, viewportHeight])

  useEffect(() => {
    return () => {
      for (const observer of resizeObserversRef.current.values()) {
        observer.disconnect()
      }
      resizeObserversRef.current.clear()
    }
  }, [])

  const startIndex = useMemo(() => {
    if (renderItems.length === 0) {
      return 0
    }
    return findFirstVisibleIndex(offsets, heights, Math.max(0, scrollTop - OVERSCAN_PX))
  }, [heights, offsets, renderItems.length, scrollTop])

  const endIndex = useMemo(() => {
    if (renderItems.length === 0) {
      return 0
    }
    const index = findLastVisibleIndex(offsets, scrollTop + viewportHeight + OVERSCAN_PX)
    return Math.min(renderItems.length, Math.max(startIndex + 1, index + 1))
  }, [offsets, renderItems.length, scrollTop, startIndex, viewportHeight])

  const visibleItems = renderItems.slice(startIndex, endIndex)
  const topSpacer = renderItems.length > 0 ? offsets[startIndex] : 0
  const bottomSpacer = renderItems.length > 0
    ? Math.max(0, totalHeight - (offsets[endIndex] ?? totalHeight))
    : 0

  const renderItem = (item: RenderItem, index: number): JSX.Element | null => {
    const globalIndex = startIndex + index
    const isLastOverall = globalIndex === renderItems.length - 1
    const marginBottom = isLastOverall ? 0 : ROW_GAP

    if (item.kind === 'assistant-turn') {
      return (
        <div
          key={item.id}
          ref={handleRowRef(item.id)}
          className="row assistant-turn message-row"
          style={{ marginBottom }}
        >
          <div className="avatar ai">qi</div>
          <div className="bubble-wrap tool-wrap">
            <div className={`assistant-turn${item.textEntries.length > 0 ? ' has-text' : ''}${item.noteEntries.length > 0 ? ' has-notes' : ''}`}>
              {item.noteEntries.length > 0 && (
                <details className="turn-notes">
                  <summary>
                    <span className="turn-notes-label">过程说明</span>
                  </summary>
                  <div className="turn-notes-body">
                    {item.noteEntries.map((noteEntry) => (
                      <div key={noteEntry.id} className="turn-note">{noteEntry.content}</div>
                    ))}
                  </div>
                </details>
              )}
              <ToolRunHeader toolEntries={item.toolEntries}>
                <div className="turn-tools">
                  {item.toolEntries.map((toolEntry) => (
                    <ToolCard
                      key={toolEntry.id}
                      compact
                      name={toolEntry.name}
                      status={toolEntry.status}
                      reason={toolEntry.reason}
                      result={toolEntry.result}
                    />
                  ))}
                </div>
              </ToolRunHeader>
              {item.textEntries.length > 0 && (
                <div className="turn-texts">
                  {item.textEntries.map((textEntry) => (
                    <div key={textEntry.id} className="turn-text">{textEntry.content}</div>
                  ))}
                </div>
              )}
              {(item.textEntries[0]?.traceId ?? item.toolEntries[0]?.traceId) && (
                <div className="turn-trace">
                  <button
                    type="button"
                    className="bubble-action-btn trace"
                    title="查看本次调用的调用链"
                    aria-label="查看本次调用的调用链"
                    onClick={() => {
                      const traceId = item.textEntries[0]?.traceId ?? item.toolEntries[0]?.traceId
                      if (traceId) {
                        window.open(`${jaegerUrl}/trace/${traceId}`, '_blank', 'noopener,noreferrer')
                      }
                    }}
                  >
                    <svg viewBox="0 0 24 24" aria-hidden="true">
                      <path d="M10 13a5 5 0 0 1 0-7l1-1a5 5 0 0 1 7 7l-1 1" />
                      <path d="M14 11a5 5 0 0 1 0 7l-1 1a5 5 0 0 1-7-7l1-1" />
                    </svg>
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )
    }

    if (item.kind === 'subtask') {
      return (
        <div
          key={item.id}
          ref={handleRowRef(item.id)}
          className="row subtask message-row"
          style={{ marginBottom }}
        >
          <div className="avatar subtask">🚀</div>
          <div className="bubble-wrap tool-wrap">
            <SubTaskCard
              goal={item.entry.goal}
              subId={item.entry.subId}
              status={item.entry.status}
              progress={item.entry.progress}
              resultText={item.entry.resultText}
              reason={item.entry.reason}
              expanded={Boolean(item.entry.expanded)}
              timedOut={Boolean(item.entry.timedOut)}
              startedAtMs={item.entry.startedAtMs}
              onToggleExpanded={() => onToggleSubTaskExpanded(item.entry.subId)}
            />
          </div>
        </div>
      )
    }

    if (item.kind === 'message') {
      return (
        <div
          key={item.id}
          ref={handleRowRef(item.id)}
          className={`row ${item.entry.role} message-row ${highlightedMessageId === item.id ? 'highlighted' : ''}`}
          style={{ marginBottom }}
        >
          {item.entry.role === 'assistant' && <div className="avatar ai">qi</div>}
          {item.entry.role === 'user' && <div className="avatar user">我</div>}
          <MessageBubble
            entry={item.entry}
            highlighted={highlightedMessageId === item.id}
            jaegerUrl={jaegerUrl}
            onCopy={onCopyMessage}
            onEdit={onEditMessage}
            onRetry={(message) => onRetryMessage(message.id, message.content)}
          />
        </div>
      )
    }

    return null
  }

  return (
    <main ref={containerRef} className="messages" onScroll={handleScroll}>
      {loading && (
        <div className="message-skeleton-list">
          {Array.from({ length: 4 }).map((_, index) => (
            <div key={index} className="message-skeleton-row">
              <div className={`message-skeleton-avatar ${index % 2 === 0 ? 'ai' : 'user'}`} />
              <div className="message-skeleton-card">
                <span className="message-skeleton-line short" />
                <span className="message-skeleton-line" />
                <span className="message-skeleton-line long" />
              </div>
            </div>
          ))}
        </div>
      )}

      {!loading && entries.length === 0 && (
        <div className="empty">
          <div className="empty-logo">qi</div>
          <h2>qi-agent Web Shell</h2>
          <p>输入消息开始对话，或者从左侧恢复之前的会话。断线时会自动重连，重连后会继续补回历史。</p>
          <div className="empty-hints">
            <span>/new 新建会话</span>
            <span>/history 恢复消息</span>
            <span>/theme 切换主题</span>
          </div>
        </div>
      )}

      {!loading && entries.length > 0 && (
        <>
          {topSpacer > 0 && <div className="message-spacer" style={{ height: topSpacer }} aria-hidden="true" />}
          {visibleItems.map((item, index) => renderItem(item, index))}
          {bottomSpacer > 0 && <div className="message-spacer" style={{ height: bottomSpacer }} aria-hidden="true" />}
        </>
      )}
    </main>
  )
}
