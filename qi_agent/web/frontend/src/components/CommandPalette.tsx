import type { KeyboardEvent } from 'react'

type CommandPaletteItem = {
  name: string
  label: string
  description: string
}

type CommandPaletteProps = {
  commands: CommandPaletteItem[]
  query: string
  onSelect: (command: string) => void
  onClose: () => void
}

export function CommandPalette({ commands, query, onSelect, onClose }: CommandPaletteProps) {
  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    if (event.key === 'Escape') {
      event.preventDefault()
      onClose()
    }
  }

  return (
    <div className="command-palette" role="dialog" aria-label="命令面板" onKeyDown={handleKeyDown}>
      <div className="command-palette-head">
        <div>
          <div className="command-palette-title">命令面板</div>
          <div className="command-palette-subtitle">
            {query ? `过滤：${query}` : '输入 / 过滤命令，点击执行或回填'}
          </div>
        </div>
        <button type="button" className="command-palette-close" onClick={onClose}>
          关闭
        </button>
      </div>

      <div className="command-palette-list">
        {commands.length === 0 && <div className="command-palette-empty">没有匹配的命令</div>}

        {commands.map((command) => (
          <button
            key={command.name}
            type="button"
            className="command-palette-item"
            onClick={() => onSelect(command.name)}
          >
            <span className="command-palette-name">{command.label}</span>
            <span className="command-palette-description">{command.description}</span>
          </button>
        ))}
      </div>
    </div>
  )
}
