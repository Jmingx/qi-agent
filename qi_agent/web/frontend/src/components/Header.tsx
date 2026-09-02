type HeaderProps = {
  sessionId: string
  sessionLabel: string
  connectionState: 'connected' | 'reconnecting' | 'disconnected'
  running: boolean
  usageLabel: string
  usageClass: 'ok' | 'warn'
  themeLabel: string
  themeIcon: string
  onOpenSidebar: () => void
  onToggleTheme: () => void
  onStop: () => void
  onClear: () => void
  onCompact: () => void
  onOpenMemory: () => void
}

export function Header({
  sessionId,
  sessionLabel,
  connectionState,
  running,
  usageLabel,
  usageClass,
  themeLabel,
  themeIcon,
  onOpenSidebar,
  onToggleTheme,
  onStop,
  onClear,
  onCompact,
  onOpenMemory,
}: HeaderProps) {
  const statusText = connectionState === 'connected'
    ? '已连接'
    : connectionState === 'reconnecting'
      ? '重连中'
      : '已断开'

  const statusClass = connectionState === 'connected'
    ? 'ok'
    : connectionState === 'reconnecting'
      ? 'warn'
      : 'bad'

  return (
    <header className="header">
      <button className="icon-btn menu-btn" onClick={onOpenSidebar}>☰</button>
      <div className="brand">
        <div className="logo">qi</div>
        <div className="brand-text">
          <span className="brand-name">qi-agent</span>
          <span className="brand-sub">{sessionLabel}</span>
        </div>
      </div>
      <div className="header-right">
        <button
          type="button"
          className="theme-btn"
          onClick={onToggleTheme}
          title="切换浅色 / 深色 / 跟随系统"
        >
          <span className="theme-btn-icon">{themeIcon}</span>
          <span className="theme-btn-text">{themeLabel}</span>
        </button>
        <span className={`badge ${usageClass} usage-badge`} title="当前会话上下文占用">
          <span className="dot" />
          {usageLabel}
        </span>
        {running && <span className="badge running-badge"><span className="spinner" />运行中</span>}
        <button className="icon-btn" onClick={onStop} disabled={!running} title="停止">⏹</button>
        <button className="icon-btn" onClick={onClear} disabled={!sessionId} title="清空当前会话">🧹</button>
        <button className="icon-btn" onClick={onCompact} disabled={!sessionId} title="压缩上下文">🗜</button>
        <button className="icon-btn" onClick={onOpenMemory} title="记忆">🧠</button>
        <span className={`badge ${statusClass}`}>
          <span className="dot" />
          {statusText}
        </span>
      </div>
    </header>
  )
}
