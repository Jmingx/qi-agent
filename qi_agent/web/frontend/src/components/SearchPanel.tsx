import type { KeyboardEvent } from 'react'

type SearchResult = {
  session_id: string
  title?: string
  role?: string
  content: string
  time?: string
}

type SearchPanelProps = {
  open: boolean
  query: string
  loading: boolean
  results: SearchResult[]
  onToggle: () => void
  onQueryChange: (value: string) => void
  onSelect: (result: SearchResult) => void
}

function normalizeText(value: string): string {
  return value.replace(/\s+/g, ' ').trim().toLowerCase()
}

function buildExcerpt(content: string, query: string): string {
  const trimmedContent = content.trim()
  if (!trimmedContent) {
    return ''
  }
  const normalizedQuery = normalizeText(query)
  if (!normalizedQuery) {
    return trimmedContent.length > 80 ? `${trimmedContent.slice(0, 80)}…` : trimmedContent
  }

  const normalizedContent = normalizeText(trimmedContent)
  const matchIndex = normalizedContent.indexOf(normalizedQuery)
  if (matchIndex < 0) {
    return trimmedContent.length > 80 ? `${trimmedContent.slice(0, 80)}…` : trimmedContent
  }

  const start = Math.max(0, matchIndex - 40)
  const end = Math.min(trimmedContent.length, matchIndex + normalizedQuery.length + 40)
  const prefix = start > 0 ? '…' : ''
  const suffix = end < trimmedContent.length ? '…' : ''
  return `${prefix}${trimmedContent.slice(start, end).replace(/\s+/g, ' ').trim()}${suffix}`
}

export function SearchPanel({
  open,
  query,
  loading,
  results,
  onToggle,
  onQueryChange,
  onSelect,
}: SearchPanelProps) {
  const hasQuery = query.trim().length > 0

  const handleKeyDown = (event: KeyboardEvent<HTMLElement>): void => {
    if (event.key === 'Escape') {
      event.preventDefault()
      onToggle()
    }
  }

  return (
    <section className={`search-panel ${open ? 'open' : 'collapsed'}`} onKeyDown={handleKeyDown}>
      <div className="search-panel-head">
        <div>
          <h4>会话搜索</h4>
          <p>{open ? '输入关键词，300ms 防抖检索历史消息。' : '已收起，点击展开后再搜。'}</p>
        </div>
        <button type="button" className="search-toggle-btn" onClick={onToggle}>
          {open ? '收起' : '展开'}
        </button>
      </div>

      {open && (
        <>
          <label className="search-input-wrap" htmlFor="session-search">
            <span className="search-input-icon">⌕</span>
            <input
              id="session-search"
              value={query}
              onChange={(event) => onQueryChange(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Escape') {
                  event.preventDefault()
                  onToggle()
                }
              }}
              placeholder="搜索会话标题或消息内容"
              spellCheck={false}
              autoComplete="off"
            />
          </label>
          <div className="search-results">
            {!hasQuery && <div className="search-empty">输入关键词后会显示结果。</div>}
            {hasQuery && loading && <div className="search-empty">搜索中...</div>}
            {hasQuery && !loading && results.length === 0 && (
              <div className="search-empty">没有匹配到会话。</div>
            )}
            {hasQuery && results.map((result) => (
              <button
                key={`${result.session_id}-${result.time ?? result.content.slice(0, 16)}`}
                type="button"
                className="search-result"
                onClick={() => onSelect(result)}
              >
                <div className="search-result-head">
                  <span className="search-result-title">{result.title || result.session_id.slice(0, 12)}</span>
                  {result.time && <span className="search-result-time">{result.time}</span>}
                </div>
                <div className="search-result-snippet">{buildExcerpt(result.content, query)}</div>
              </button>
            ))}
          </div>
        </>
      )}
    </section>
  )
}
