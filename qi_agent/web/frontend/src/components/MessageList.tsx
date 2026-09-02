import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { MessageBubble } from './MessageBubble'
import { ToolCard } from './ToolCard'
import { SubTaskCard } from './SubTaskCard'
import {
  normalizeSearchText,
  stringifyValue,
  type PendingScrollTarget,
  type StreamEntry,
  type SubTaskEntry,
  type TextEntry,
} from '../appModel'

type MessageListProps = {
  entries: StreamEntry[]
  highlightedMessageId: number | null
  loading: boolean
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

function isTextEntry(entry: StreamEntry): entry is TextEntry {
  return entry.kind === 'message'
}

function isSubTaskEntry(entry: StreamEntry): entry is SubTaskEntry {
  return entry.kind === 'subtask'
}

function estimateTextHeight(entry: TextEntry): number {
  const base = entry.role === 'system' ? 56 : 82
  const charsPerLine = entry.role === 'system' ? 52 : 66
  const lines = Math.max(1, Math.ceil(entry.content.length / charsPerLine))
  const timeHeight = entry.time ? 18 : 0
  return Math.min(420, base + (lines * 18) + timeHeight)
}

function estimateToolHeight(entry: StreamEntry): number {
  if (entry.kind !== 'tool') {
    return 0
  }
  const jsonLength = stringifyValue(entry.toolArguments).length
  const jsonRows = Math.max(1, Math.ceil(jsonLength / 120))
  const resultRows = entry.result ? 2 : 0
  return Math.min(520, 160 + (jsonRows * 22) + (resultRows * 18))
}

function estimateSubTaskHeight(entry: SubTaskEntry): number {
  const progressRows = Math.min(6, entry.progress.length)
  const contentLength = (entry.resultText?.length ?? 0) + (entry.reason?.length ?? 0) + entry.goal.length
  const bodyRows = Math.max(3, Math.ceil(contentLength / 96))
  const expandedBoost = entry.expanded && entry.resultText ? Math.min(220, Math.ceil(entry.resultText.length / 24)) : 0
  return Math.min(620, 210 + (progressRows * 18) + (bodyRows * 14) + expandedBoost)
}

function estimateEntryHeight(entry: StreamEntry): number {
  if (entry.kind === 'message') {
    return estimateTextHeight(entry)
  }
  if (entry.kind === 'tool') {
    return estimateToolHeight(entry)
  }
  return estimateSubTaskHeight(entry)
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
    if (!isTextEntry(entry)) {
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

  const heights = useMemo(() => entries.map((entry) => (
    measuredHeightsRef.current.get(entry.id) ?? estimateEntryHeight(entry)
  )), [entries, measureVersion])

  const offsets = useMemo(() => {
    const next: number[] = []
    let offset = 0
    entries.forEach((_, index) => {
      next[index] = offset
      offset += heights[index]
      if (index < entries.length - 1) {
        offset += ROW_GAP
      }
    })
    return next
  }, [entries, heights])

  const totalHeight = useMemo(() => {
    if (entries.length === 0) {
      return 0
    }
    return offsets[entries.length - 1] + heights[entries.length - 1]
  }, [entries.length, heights, offsets])

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
    if (loading || entries.length === 0 || scrollTarget) {
      return
    }
    const container = containerRef.current
    if (!container) {
      return
    }
    const nextScrollTop = Math.max(0, totalHeight - viewportHeight)
    container.scrollTop = nextScrollTop
    setScrollTop(nextScrollTop)
  }, [entries, loading, scrollTarget, totalHeight, viewportHeight])

  useLayoutEffect(() => {
    if (!scrollTarget || entries.length === 0) {
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

    const index = entries.findIndex((entry) => entry.id === matchedEntry.id)
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
  }, [entries, heights, offsets, onHighlightMessage, onScrollTargetHandled, scrollTarget, totalHeight, viewportHeight])

  useEffect(() => {
    return () => {
      for (const observer of resizeObserversRef.current.values()) {
        observer.disconnect()
      }
      resizeObserversRef.current.clear()
    }
  }, [])

  const startIndex = useMemo(() => {
    if (entries.length === 0) {
      return 0
    }
    return findFirstVisibleIndex(offsets, heights, Math.max(0, scrollTop - OVERSCAN_PX))
  }, [entries.length, heights, offsets, scrollTop])

  const endIndex = useMemo(() => {
    if (entries.length === 0) {
      return 0
    }
    const index = findLastVisibleIndex(offsets, scrollTop + viewportHeight + OVERSCAN_PX)
    return Math.min(entries.length, Math.max(startIndex + 1, index + 1))
  }, [entries.length, offsets, scrollTop, startIndex, viewportHeight])

  const visibleEntries = entries.slice(startIndex, endIndex)
  const topSpacer = entries.length > 0 ? offsets[startIndex] : 0
  const bottomSpacer = entries.length > 0
    ? Math.max(0, totalHeight - (offsets[endIndex] ?? totalHeight))
    : 0

  const renderEntry = (entry: StreamEntry, index: number): JSX.Element | null => {
    const globalIndex = startIndex + index
    const isLastOverall = globalIndex === entries.length - 1
    const marginBottom = isLastOverall ? 0 : ROW_GAP

    if (entry.kind === 'tool') {
      return (
        <div
          key={entry.id}
          ref={handleRowRef(entry.id)}
          className="row tool message-row"
          style={{ marginBottom }}
        >
          <div className="avatar tool">🔧</div>
          <div className="bubble-wrap tool-wrap">
            <ToolCard
              name={entry.name}
              toolArguments={entry.toolArguments}
              status={entry.status}
              reason={entry.reason}
              result={entry.result}
            />
          </div>
        </div>
      )
    }

    if (isSubTaskEntry(entry)) {
      return (
        <div
          key={entry.id}
          ref={handleRowRef(entry.id)}
          className="row subtask message-row"
          style={{ marginBottom }}
        >
          <div className="avatar subtask">🚀</div>
          <div className="bubble-wrap tool-wrap">
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
              onToggleExpanded={() => onToggleSubTaskExpanded(entry.subId)}
            />
          </div>
        </div>
      )
    }

    if (isTextEntry(entry)) {
      return (
        <div
          key={entry.id}
          ref={handleRowRef(entry.id)}
          className={`row ${entry.role} message-row ${highlightedMessageId === entry.id ? 'highlighted' : ''}`}
          style={{ marginBottom }}
        >
          {entry.role === 'assistant' && <div className="avatar ai">qi</div>}
          {entry.role === 'user' && <div className="avatar user">我</div>}
          <MessageBubble
            entry={entry}
            highlighted={highlightedMessageId === entry.id}
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
          {visibleEntries.map((entry, index) => renderEntry(entry, index))}
          {bottomSpacer > 0 && <div className="message-spacer" style={{ height: bottomSpacer }} aria-hidden="true" />}
        </>
      )}
    </main>
  )
}
