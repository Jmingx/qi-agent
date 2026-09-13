import { useEffect, useRef, useState } from 'react'
import { Icon, type IconName } from './ui/Icon'

type HeaderProps = {
  sessionTitle: string
  sessionId: string
  connectionState: 'connected' | 'reconnecting' | 'disconnected'
  running: boolean
  contextTokens: number
  contextLimit: number
  percent: number
  usageLevel: 'ok' | 'warn' | 'danger'
  themeLabel: string
  themeIcon: IconName
  traceId: string | null
  onToggleRail: () => void
  onOpenContext: () => void
  onOpenTrace: () => void
  onToggleTheme: () => void
  onStop: () => void
  onClear: () => void
  onCompact: () => void
  onOpenMemory: () => void
  onOpenJaeger: () => void
  onOpenOpik: () => void
}

const RING_SIZE = 18
const RING_STROKE = 2.6

/** 上下文占用环：一眼看出「还剩多少窗口」，点击展开明细。 */
function ContextRing({ percent, level }: { percent: number; level: 'ok' | 'warn' | 'danger' }) {
  const radius = (RING_SIZE - RING_STROKE) / 2
  const circumference = 2 * Math.PI * radius
  const dash = circumference * (Math.max(0, Math.min(100, percent)) / 100)
  return (
    <svg className="ring" width={RING_SIZE} height={RING_SIZE} viewBox={`0 0 ${RING_SIZE} ${RING_SIZE}`}>
      <circle
        className="ring-track"
        cx={RING_SIZE / 2}
        cy={RING_SIZE / 2}
        r={radius}
        fill="none"
        strokeWidth={RING_STROKE}
      />
      <circle
        className="ring-value"
        cx={RING_SIZE / 2}
        cy={RING_SIZE / 2}
        r={radius}
        fill="none"
        strokeWidth={RING_STROKE}
        strokeLinecap="round"
        strokeDasharray={`${dash} ${circumference - dash}`}
        transform={`rotate(-90 ${RING_SIZE / 2} ${RING_SIZE / 2})`}
        data-level={level}
      />
    </svg>
  )
}

function formatTokens(tokens: number): string {
  if (tokens < 1000) {
    return String(tokens)
  }
  const k = tokens / 1000
  return `${k >= 100 ? Math.round(k) : k.toFixed(1).replace(/\.0$/, '')}k`
}

export function Header({
  sessionTitle,
  sessionId,
  connectionState,
  running,
  contextTokens,
  contextLimit,
  percent,
  usageLevel,
  themeLabel,
  themeIcon,
  traceId,
  onToggleRail,
  onOpenContext,
  onOpenTrace,
  onToggleTheme,
  onStop,
  onClear,
  onCompact,
  onOpenMemory,
  onOpenJaeger,
  onOpenOpik,
}: HeaderProps) {
  const [menuOpen, setMenuOpen] = useState(false)
  const anchorRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!menuOpen) {
      return undefined
    }
    const onPointerDown = (event: MouseEvent): void => {
      if (anchorRef.current && !anchorRef.current.contains(event.target as Node)) {
        setMenuOpen(false)
      }
    }
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        setMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [menuOpen])

  const statusLabel = connectionState === 'connected'
    ? '已连接'
    : connectionState === 'reconnecting'
      ? '重连中'
      : '已断开'
  const statusClass = connectionState === 'connected'
    ? ''
    : connectionState === 'reconnecting'
      ? ' is-warn'
      : ' is-bad'

  const closeAnd = (action: () => void) => (): void => {
    setMenuOpen(false)
    action()
  }

  return (
    <header className="chat-header">
      <button
        type="button"
        className="icon-btn rail-toggle"
        onClick={onToggleRail}
        title="会话列表"
        aria-label="打开会话列表"
      >
        <Icon name="panel-left" size={17} />
      </button>

      <div className="header-titles">
        <div className="header-title" title={sessionId || undefined}>
          {sessionTitle}
        </div>
        <div className="header-sub">
          <span className={`status-dot${statusClass}`} aria-hidden="true" />
          <span>{statusLabel}</span>
          {running && (
            <span className="badge running-badge">
              <span className="spinner" />
              运行中
            </span>
          )}
        </div>
      </div>

      <div className="header-actions">
        <button
          type="button"
          className={`context-chip${usageLevel === 'ok' ? '' : usageLevel === 'warn' ? ' is-warn' : ' is-danger'}`}
          onClick={onOpenContext}
          title={`上下文占用 ${contextTokens.toLocaleString()} / ${contextLimit.toLocaleString()} tokens（估算）· 点击查看构成`}
        >
          <ContextRing percent={percent} level={usageLevel} />
          <span className="chip-label">
            {formatTokens(contextTokens)}/{formatTokens(contextLimit)} · {percent}%
          </span>
        </button>

        {running && (
          <button type="button" className="icon-btn" onClick={onStop} title="停止当前回合（Esc）" aria-label="停止">
            <Icon name="stop" size={16} />
          </button>
        )}

        <div className="menu-anchor" ref={anchorRef}>
          <button
            type="button"
            className="icon-btn"
            onClick={() => setMenuOpen((current) => !current)}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            title="更多操作"
            aria-label="更多操作"
          >
            <Icon name="more" size={17} />
          </button>

          {menuOpen && (
            <div className="menu" role="menu">
              <div className="menu-label">会话</div>
              <button type="button" className="menu-item" role="menuitem" onClick={closeAnd(onOpenContext)}>
                <Icon name="layers" size={15} /> 上下文构成
              </button>
              <button type="button" className="menu-item" role="menuitem" onClick={closeAnd(onOpenTrace)}>
                <Icon name="route" size={15} /> 调用轨迹
              </button>
              <button type="button" className="menu-item" role="menuitem" onClick={closeAnd(onOpenMemory)}>
                <Icon name="memory" size={15} /> 跨会话记忆
              </button>
              <button type="button" className="menu-item" role="menuitem" onClick={closeAnd(onCompact)}>
                <Icon name="archive" size={15} /> 压缩上下文
              </button>
              <div className="menu-sep" />
              <div className="menu-label">外观与观测</div>
              <button type="button" className="menu-item" role="menuitem" onClick={closeAnd(onToggleTheme)}>
                <Icon name={themeIcon} size={15} /> 主题：{themeLabel}
              </button>
              <button
                type="button"
                className="menu-item"
                role="menuitem"
                disabled={!traceId}
                onClick={closeAnd(onOpenJaeger)}
              >
                <Icon name="link" size={15} /> 打开 Jaeger 调用链
                {!traceId && <span className="menu-hint">无 trace</span>}
              </button>
              <button type="button" className="menu-item" role="menuitem" onClick={closeAnd(onOpenOpik)}>
                <Icon name="flask" size={15} /> 打开 Opik 评测平台
              </button>
              <div className="menu-sep" />
              <button type="button" className="menu-item is-danger" role="menuitem" onClick={closeAnd(onClear)}>
                <Icon name="eraser" size={15} /> 清空当前会话…
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  )
}
