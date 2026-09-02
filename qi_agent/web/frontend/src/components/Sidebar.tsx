import { lazy, Suspense } from 'react'
import { type SessionItem, type SessionSearchResult } from '../appModel'

const LazySearchPanel = lazy(() =>
  import('./SearchPanel').then((module) => ({ default: module.SearchPanel })),
)

type SidebarProps = {
  open: boolean
  sessionId: string
  sessions: SessionItem[]
  searchOpen: boolean
  searchQuery: string
  searchLoading: boolean
  searchResults: SessionSearchResult[]
  onClose: () => void
  onNewSession: () => void
  onSwitchSession: (sessionId: string) => void
  onDeleteSession: (sessionId: string) => void
  onSearchToggle: () => void
  onSearchQueryChange: (value: string) => void
  onSearchSelect: (result: SessionSearchResult) => void
}

export function Sidebar({
  open,
  sessionId,
  sessions,
  searchOpen,
  searchQuery,
  searchLoading,
  searchResults,
  onClose,
  onNewSession,
  onSwitchSession,
  onDeleteSession,
  onSearchToggle,
  onSearchQueryChange,
  onSearchSelect,
}: SidebarProps) {
  if (!open) {
    return null
  }

  return (
    <div className="sidebar-overlay" onClick={onClose}>
      <aside className="sidebar" onClick={(event) => event.stopPropagation()}>
        <div className="sidebar-header">
          <div>
            <h3>会话</h3>
            <p>本地保留最近会话 id，重开页面不会丢。</p>
          </div>
          <button className="icon-btn" onClick={onNewSession} title="新建会话">+</button>
        </div>
        <Suspense
          fallback={(
            <div className="search-panel search-panel-loading" aria-label="搜索面板加载中">
              搜索面板加载中...
            </div>
          )}
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
        <div className="session-list">
          <div className={`session-item-row ${sessionId === '' ? 'active' : ''}`}>
            <button
              type="button"
              className={`session-item ${sessionId === '' ? 'active' : ''}`}
              onClick={onNewSession}
            >
              <span className="sess-icon">＋</span>
              <span className="sess-title">新建会话</span>
            </button>
            <span className="session-item-spacer" />
          </div>
          {sessions.map((item) => (
            <div key={item.id} className={`session-item-row ${item.id === sessionId ? 'active' : ''}`}>
              <button
                type="button"
                className={`session-item ${item.id === sessionId ? 'active' : ''}`}
                onClick={() => onSwitchSession(item.id)}
              >
                <span className="sess-icon">💬</span>
                <span className="sess-title">{item.title || item.id.slice(0, 12)}</span>
              </button>
              <button
                type="button"
                className="session-delete-btn"
                onClick={() => onDeleteSession(item.id)}
                title="删除会话"
                aria-label={`删除会话 ${item.title || item.id}`}
              >
                🗑
              </button>
            </div>
          ))}
        </div>
      </aside>
    </div>
  )
}
