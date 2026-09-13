import { lazy, Suspense, useMemo, useState } from 'react'
import { type SessionItem, type SessionSearchResult } from '../appModel'
import { SESSION_GROUP_LABELS, formatRelative, sessionGroupKey } from '../utils/format'
import { Icon } from './ui/Icon'

const LazySearchPanel = lazy(() =>
  import('./SearchPanel').then((module) => ({ default: module.SearchPanel })),
)

type SidebarProps = {
  isDrawer: boolean
  sessionId: string
  sessions: SessionItem[]
  searchOpen: boolean
  searchQuery: string
  searchLoading: boolean
  searchResults: SessionSearchResult[]
  onClose: () => void
  onNewSession: () => void
  onSwitchSession: (sessionId: string) => void
  onRequestDelete: (item: SessionItem) => void
  onCleanupEmpty: (items: SessionItem[]) => void
  onSearchToggle: () => void
  onSearchQueryChange: (value: string) => void
  onSearchSelect: (result: SessionSearchResult) => void
}

const GROUP_ORDER: Array<'today' | 'yesterday' | 'week' | 'earlier'> = ['today', 'yesterday', 'week', 'earlier']

function isEmptySession(item: SessionItem): boolean {
  return (item.message_count ?? 0) === 0 && (item.turn ?? 0) === 0
}

/**
 * 会话栏（UI v3 §4.4）：按时间分组、显示消息数与更新时间、空壳会话折叠、
 * 删除走二次确认（旧实现 hover 出垃圾桶即一键删）。
 */
export function Sidebar({
  isDrawer,
  sessionId,
  sessions,
  searchOpen,
  searchQuery,
  searchLoading,
  searchResults,
  onClose,
  onNewSession,
  onSwitchSession,
  onRequestDelete,
  onCleanupEmpty,
  onSearchToggle,
  onSearchQueryChange,
  onSearchSelect,
}: SidebarProps) {
  const [emptyOpen, setEmptyOpen] = useState(false)

  const { grouped, emptySessions } = useMemo(() => {
    const buckets: Record<'today' | 'yesterday' | 'week' | 'earlier', SessionItem[]> = {
      today: [], yesterday: [], week: [], earlier: [],
    }
    const empties: SessionItem[] = []
    for (const item of sessions) {
      if (isEmptySession(item) && item.id !== sessionId) {
        empties.push(item)
        continue
      }
      buckets[sessionGroupKey(item.updated_at)].push(item)
    }
    return { grouped: buckets, emptySessions: empties }
  }, [sessionId, sessions])

  const rail = (
    <aside className={`session-rail${isDrawer ? ' is-drawer' : ''}`} aria-label="会话列表">
      <div className="rail-head">
        <span className="rail-title">会话</span>
        <button
          type="button"
          className="icon-btn"
          onClick={onNewSession}
          title="新建会话"
          aria-label="新建会话"
        >
          <Icon name="plus" size={16} />
        </button>
        {isDrawer && (
          <button type="button" className="icon-btn" onClick={onClose} title="关闭" aria-label="关闭会话列表">
            <Icon name="close" size={16} />
          </button>
        )}
      </div>

      <Suspense
        fallback={<div className="search-panel search-panel-loading">搜索面板加载中…</div>}
      >
        <LazySearchPanel
          open={searchOpen}
          query={searchQuery}
          loading={searchLoading}
          results={searchResults}
          onToggle={onSearchToggle}
          onQueryChange={onSearchQueryChange}
          onSelect={onSearchSelect}
        />
      </Suspense>

      <div className="rail-body">
        {sessions.length === 0 && <div className="rail-empty">还没有会话，点右上角 ＋ 新建。</div>}

        {GROUP_ORDER.map((key) => {
          const items = grouped[key]
          if (items.length === 0) {
            return null
          }
          return (
            <div className="session-group" key={key}>
              <div className="session-group-title">{SESSION_GROUP_LABELS[key]}</div>
              {items.map((item) => (
                <SessionRow
                  key={item.id}
                  item={item}
                  active={item.id === sessionId}
                  onSwitch={onSwitchSession}
                  onRequestDelete={onRequestDelete}
                />
              ))}
            </div>
          )
        })}

        {emptySessions.length > 0 && (
          <div className="session-group">
            <button
              type="button"
              className="session-group-title"
              style={{ background: 'transparent', border: 0, width: '100%', textAlign: 'left' }}
              onClick={() => setEmptyOpen((current) => !current)}
            >
              <Icon name={emptyOpen ? 'chevron-down' : 'chevron-right'} size={11} /> 空会话（
              {emptySessions.length}）
            </button>
            {emptyOpen && (
              <>
                <div className="rail-empty" style={{ paddingTop: 0 }}>
                  这些会话没有任何消息（打开页面时会自动创建），可一键清理。
                </div>
                {emptySessions.slice(0, 12).map((item) => (
                  <SessionRow
                    key={item.id}
                    item={item}
                    active={false}
                    onSwitch={onSwitchSession}
                    onRequestDelete={onRequestDelete}
                  />
                ))}
                <button
                  type="button"
                  className="btn btn-block"
                  style={{ marginTop: 6 }}
                  onClick={() => onCleanupEmpty(emptySessions)}
                >
                  <Icon name="eraser" size={14} /> 清理 {emptySessions.length} 个空会话
                </button>
              </>
            )}
          </div>
        )}
      </div>
    </aside>
  )

  if (!isDrawer) {
    return rail
  }

  return (
    <>
      <div className="mobile-scrim" onClick={onClose} />
      {rail}
    </>
  )
}

function SessionRow({
  item,
  active,
  onSwitch,
  onRequestDelete,
}: {
  item: SessionItem
  active: boolean
  onSwitch: (sessionId: string) => void
  onRequestDelete: (item: SessionItem) => void
}) {
  const count = item.message_count ?? 0
  const meta = [
    count > 0 ? `${count} 条` : '空会话',
    formatRelative(item.updated_at),
  ].filter(Boolean).join(' · ')
  const title = item.title?.trim() || '新会话'

  return (
    <div className="session-item-row">
      <button
        type="button"
        className={`session-item${active ? ' is-active' : ''}`}
        onClick={() => onSwitch(item.id)}
        title={item.id}
      >
        <span className="session-item-text">
          <span className="session-item-title">{title}</span>
          <span className="session-item-meta">{meta}</span>
        </span>
      </button>
      <div className="session-row-actions">
        <button
          type="button"
          className="icon-btn"
          title="删除会话"
          aria-label={`删除会话 ${title}`}
          onClick={() => onRequestDelete(item)}
        >
          <Icon name="trash" size={14} />
        </button>
      </div>
    </div>
  )
}
