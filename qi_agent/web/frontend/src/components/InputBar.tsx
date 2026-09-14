import { lazy, Suspense, useEffect, useRef, type KeyboardEvent, type RefObject } from 'react'
import { type CommandDefinition, type CommandName } from '../commands'
import { ZH_CN } from '../i18n/zh-CN'
import { Icon } from './ui/Icon'

const LazyCommandPalette = lazy(() =>
  import('./CommandPalette').then((module) => ({ default: module.CommandPalette })),
)

type InputBarProps = {
  input: string
  /** 连接断开才禁用；运行中不禁用（可引导/排队） */
  disabled: boolean
  running: boolean
  placeholder: string
  commandPaletteVisible: boolean
  commandPaletteCommands: CommandDefinition[]
  commandPaletteQuery: string
  inputRef: RefObject<HTMLTextAreaElement>
  onCommandButtonClick: () => void
  onChange: (value: string) => void
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void
  onSend: () => void
  onStop: () => void
  onSelectCommand: (command: CommandName) => void
  onCloseCommandPalette: () => void
  /** 仅当当前 Context 尚未绑定目录时显示；选择后由上层创建新会话。 */
  onSetWorkspace: () => void
  workspaceLabel?: string | null
  workspaceAvailable?: boolean
}

const MAX_HEIGHT = 200

export function InputBar({
  input,
  disabled,
  running,
  placeholder,
  commandPaletteVisible,
  commandPaletteCommands,
  commandPaletteQuery,
  inputRef,
  onCommandButtonClick,
  onChange,
  onKeyDown,
  onSend,
  onStop,
  onSelectCommand,
  onCloseCommandPalette,
  onSetWorkspace,
  workspaceLabel,
  workspaceAvailable,
}: InputBarProps) {
  const boxRef = useRef<HTMLDivElement | null>(null)

  // 自增高：内容撑开到上限后内部滚动（多行提示/粘贴日志是 agent 场景刚需）
  useEffect(() => {
    const node = inputRef.current
    if (!node) {
      return
    }
    node.style.height = 'auto'
    node.style.height = `${Math.min(node.scrollHeight, MAX_HEIGHT)}px`
  }, [input, inputRef])

  const canSend = input.trim().length > 0 && input.trim() !== '/' && !disabled

  return (
    <footer className="composer">
      <div className={`workspace-status${!workspaceLabel || workspaceAvailable === false ? ' is-unavailable' : ''}`}>
          <Icon name="folder" size={14} />
          <span>工作目录：{workspaceLabel ?? '未绑定'}</span>
          <span className="workspace-status-hint">
            {!workspaceLabel ? (
              <button type="button" className="workspace-status-action" onClick={onSetWorkspace}>
                设置工作目录
              </button>
            ) : workspaceAvailable === false ? '不可用' : '已绑定 · 换项目请新建会话'}
          </span>
      </div>
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

      <div className="composer-inner">
        <div className="composer-box" ref={boxRef}>
          <button
            type="button"
            className="icon-btn"
            onClick={onCommandButtonClick}
            title={ZH_CN.commandPalette.buttonTitle}
            aria-label={ZH_CN.commandPalette.buttonAria}
          >
            <Icon name="braces" size={16} />
          </button>

          <textarea
            ref={inputRef}
            className="composer-input"
            rows={1}
            value={input}
            onChange={(event) => onChange(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder={placeholder}
            disabled={disabled}
            spellCheck={false}
            autoComplete="off"
            aria-label="消息输入"
          />

          {running ? (
            <button
              type="button"
              className="icon-btn"
              onClick={onStop}
              title="停止当前回合（Esc）"
              aria-label="停止当前回合"
            >
              <Icon name="stop" size={16} />
            </button>
          ) : (
            <button
              type="button"
              className="btn btn-primary"
              onClick={onSend}
              disabled={!canSend}
              title={ZH_CN.composer.sendTitle}
              aria-label={ZH_CN.composer.sendTitle}
            >
              <Icon name="send" size={15} />
              发送
            </button>
          )}
        </div>
      </div>

      <div className="composer-hint">
        <span>
          <span className="kbd">Enter</span> 发送 · <span className="kbd">Shift</span>+
          <span className="kbd">Enter</span> 换行 · <span className="kbd">Ctrl</span>+
          <span className="kbd">K</span> 命令
        </span>
        {running && <span>运行中：可直接输入作为引导（下一轮生效）</span>}
      </div>
    </footer>
  )
}
