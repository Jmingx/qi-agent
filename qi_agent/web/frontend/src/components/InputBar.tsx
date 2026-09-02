import { lazy, Suspense, type KeyboardEvent } from 'react'
import { type CommandDefinition, type CommandName } from '../commands'
import { ZH_CN } from '../i18n/zh-CN'

const LazyCommandPalette = lazy(() =>
  import('./CommandPalette').then((module) => ({ default: module.CommandPalette })),
)

type InputBarProps = {
  input: string
  disabled: boolean
  placeholder: string
  commandPaletteVisible: boolean
  commandPaletteCommands: CommandDefinition[]
  commandPaletteQuery: string
  onCommandButtonClick: () => void
  onChange: (value: string) => void
  onKeyDown: (event: KeyboardEvent<HTMLInputElement>) => void
  onSend: () => void
  onSelectCommand: (command: CommandName) => void
  onCloseCommandPalette: () => void
}

export function InputBar({
  input,
  disabled,
  placeholder,
  commandPaletteVisible,
  commandPaletteCommands,
  commandPaletteQuery,
  onCommandButtonClick,
  onChange,
  onKeyDown,
  onSend,
  onSelectCommand,
  onCloseCommandPalette,
}: InputBarProps) {
  return (
    <footer className="composer">
      {commandPaletteVisible && (
        <Suspense
          fallback={(
            <div className="command-palette command-palette-loading" aria-label={ZH_CN.commandPalette.loadingAria}>
              {ZH_CN.commandPalette.loading}
            </div>
          )}
        >
          <LazyCommandPalette
            commands={commandPaletteCommands}
            query={commandPaletteQuery}
            onSelect={onSelectCommand}
            onClose={onCloseCommandPalette}
          />
        </Suspense>
      )}
      <div className="input-bar">
        <button
          type="button"
          className="command-btn"
          onClick={onCommandButtonClick}
          title={ZH_CN.commandPalette.buttonTitle}
          aria-label={ZH_CN.commandPalette.buttonAria}
        >
          /
        </button>
        <input
          value={input}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={onKeyDown}
          placeholder={placeholder}
          disabled={disabled}
        />
        <button
          className="send-btn"
          onClick={onSend}
          disabled={disabled || input.trim().length === 0 || input.trim() === '/'}
          title={ZH_CN.composer.sendTitle}
          aria-label={ZH_CN.composer.sendTitle}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M22 2L11 13" />
            <path d="M22 2L15 22L11 13L2 9L22 2Z" />
          </svg>
        </button>
      </div>
    </footer>
  )
}
