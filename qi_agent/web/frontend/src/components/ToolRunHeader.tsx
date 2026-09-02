import { memo, useEffect, useState, type ReactNode } from 'react'
import { type ToolEntry } from '../appModel'

type ToolRunHeaderProps = {
  /** 本回合的全部工具 entry（running→result 实时更新，父级数据驱动） */
  toolEntries: ToolEntry[]
  /** 展开时渲染的工具明细行 */
  children: ReactNode
}

/**
 * 回合容器内的"工具区"头部：控制折叠。
 * 规则（产品约定）：
 * - 运行中（存在未完成工具）→ 展开，实时可见工具进度；
 * - 全部完成 → 自动收起为一行摘要（"已完成 N 次工具调用"）；
 * - 用户手动点过 → 进入手动控制，不再随完成度自动变化。
 */
function ToolRunHeaderBase({ toolEntries, children }: ToolRunHeaderProps) {
  const allDone = toolEntries.length > 0 && toolEntries.every((entry) => entry.result)
  const [collapsed, setCollapsed] = useState(false)
  const [userControlled, setUserControlled] = useState(false)

  useEffect(() => {
    if (allDone && !userControlled) {
      setCollapsed(true)
    }
  }, [allDone, userControlled])

  const pendingCount = toolEntries.filter((entry) => !entry.result).length
  const label = allDone
    ? `已完成 ${toolEntries.length} 次工具调用`
    : pendingCount > 0
      ? `工具调用中…（${toolEntries.length} 次）`
      : `工具调用（${toolEntries.length} 次）`

  return (
    <div className="tool-run-head">
      <button
        type="button"
        className="tool-run-toggle"
        onClick={() => {
          setUserControlled(true)
          setCollapsed((current) => !current)
        }}
        aria-expanded={!collapsed}
      >
        <span className="tool-run-chevron" aria-hidden="true">{collapsed ? '▸' : '▾'}</span>
        <span className={`tool-run-label${allDone ? ' done' : ''}`}>
          {allDone ? '✓' : '🔧'} {label}
        </span>
      </button>
      {!collapsed && <div className="tool-run-body">{children}</div>}
    </div>
  )
}

export const ToolRunHeader = memo(ToolRunHeaderBase)
