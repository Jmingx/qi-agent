import { useEffect, useMemo, useRef, useState } from 'react'
import { type ToolApprovalEntry } from '../appModel'
import { Icon } from './ui/Icon'
import { formatDuration } from '../utils/format'

type Props = {
  approval: ToolApprovalEntry
  /** 提交选择（选项 value）；失败时组件重新启用按钮 */
  onRespond: (choice: string) => void | Promise<void>
}

/** 档位徽章：常规 / 沙箱降级 / 沙箱升级（code 由决策层给出）。 */
const CODE_LABELS: Record<string, { text: string; danger: boolean }> = {
  SEC_APPROVAL_GENERAL: { text: '常规审批', danger: false },
  SEC_APPROVAL_SANDBOX: { text: '沙箱降级', danger: true },
  SEC_APPROVAL_ESCALATION: { text: '沙箱升级', danger: true },
}

/** 视为"拒绝"的选项值（默认焦点与 Esc 都落在它上面——fail-closed 语义）。 */
const DENY_VALUES = new Set(['deny', 'reject', 'cancel'])

const FALLBACK_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'once', label: '允许一次' },
  { value: 'deny', label: '拒绝' },
]

const DEFAULT_TIMEOUT_MS = 60_000

const OUTCOMES: Record<string, { text: string; cls: string; icon: string }> = {
  allowed: { text: '已允许', cls: 'is-ok', icon: 'check' },
  denied: { text: '已拒绝', cls: 'is-danger', icon: 'alert' },
  timeout: { text: '超时拒绝', cls: 'is-danger', icon: 'alert' },
}

/**
 * 审批卡片（2026-09-14 定稿）：**长在对话流里**——就插在触发它的工具行上方，
 * 决策后原地降级为记录（不再是浮层，浮层一收起用户就看不到发生过什么）。
 *
 * 与浮层版的差异：`role="dialog"` 去掉（它是消息，不是模态）；待决时按钮仍
 * 固定在命令滚动区之外（命令再长也挤不掉按钮）。
 * 选项一律由 payload 渲染（前端零硬编码档位逻辑）；默认焦点与 Esc = 拒绝。
 */
export function ApprovalCard({ approval, onRespond }: Props) {
  const pending = approval.state === 'pending'
  const options = useMemo(
    () => (approval.options.length > 0 ? approval.options : FALLBACK_OPTIONS),
    [approval.options],
  )
  const denyChoice = useMemo(() => {
    const deny = options.find((option) => DENY_VALUES.has(option.value))
    return deny?.value ?? options[options.length - 1].value
  }, [options])

  const totalMs = typeof approval.timeoutMs === 'number' && approval.timeoutMs > 0
    ? approval.timeoutMs
    : DEFAULT_TIMEOUT_MS
  const [remainingMs, setRemainingMs] = useState(totalMs)
  const [busy, setBusy] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [commandOpen, setCommandOpen] = useState(false)
  const denyRef = useRef<HTMLButtonElement | null>(null)
  // 一次性闩锁用 ref（不能用 setState 更新器：React 会丢弃/重复调用更新器里的
  // 副作用——实测点击"允许一次"后 RPC 根本没发出，2026-09-14 验收抓出）
  const answeredRef = useRef(false)

  const command = approval.command.trim().length > 0
    ? approval.command
    : JSON.stringify({}, null, 2)

  const answer = useMemo(() => (choice: string): void => {
    if (answeredRef.current) {
      return // 已提交：响应落地前不重复提交
    }
    answeredRef.current = true
    setBusy(choice)
    void Promise.resolve(onRespond(choice)).catch(() => {
      answeredRef.current = false // 响应失败 → 重新启用按钮供重试
      setBusy(null)
    })
  }, [onRespond])

  // 倒计时（本地计时，非轮询）：归零自动提交拒绝——与网关等待上限一致
  useEffect(() => {
    if (!pending) {
      return undefined
    }
    setRemainingMs(totalMs)
    const start = Date.now()
    const timer = window.setInterval(() => {
      const left = totalMs - (Date.now() - start)
      setRemainingMs(left > 0 ? left : 0)
      if (left <= 0) {
        window.clearInterval(timer)
        answer(denyChoice)
      }
    }, 250)
    return () => window.clearInterval(timer)
  }, [answer, denyChoice, pending, totalMs])

  // 默认焦点落在"拒绝"上 + Esc = 拒绝（沉默不等于同意，对齐 TUI/Hermes）。
  // 仅在这一条待决时才挂全局监听：多条待决时最后一条拿焦点。
  useEffect(() => {
    if (!pending) {
      return undefined
    }
    denyRef.current?.focus()
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        answer(denyChoice)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [answer, denyChoice, pending])

  const badge = CODE_LABELS[approval.code] ?? { text: approval.code || '需要批准', danger: false }
  const seconds = Math.ceil(remainingMs / 1000)

  const copyCommand = (): void => {
    void navigator.clipboard?.writeText(command).then(() => {
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    })
  }

  if (!pending) {
    const outcome = OUTCOMES[approval.state] ?? OUTCOMES.denied
    const choiceLabel = options.find((option) => option.value === approval.choice)?.label ?? approval.choice
    return (
      <section className={`approval-card is-settled ${outcome.cls}`} data-approval-key={approval.approvalId}>
        <div className="approval-strip">
          <span className="approval-dot" aria-hidden />
          <span>审批 · {badge.text}</span>
          <span className="approval-outcome">
            <Icon name={outcome.icon} size={12} /> {outcome.text}
            {approval.state === 'allowed' && choiceLabel ? `（${choiceLabel}）` : ''}
          </span>
          {approval.waitedMs !== undefined && (
            <span className="approval-waited">等了 {formatDuration(approval.waitedMs)}</span>
          )}
        </div>
        <p className="approval-headline">{approval.question || 'agent 请求执行一个需要授权的操作'}</p>
        <div className="approval-actions-line">
          <button type="button" className="step-toggle" onClick={() => setCommandOpen((current) => !current)}>
            <Icon name={commandOpen ? 'chevron-down' : 'chevron-right'} size={12} /> 命令
          </button>
        </div>
        {commandOpen && <pre className="approval-command">{command}</pre>}
      </section>
    )
  }

  return (
    <section
      className={`approval-card${badge.danger ? ' is-danger' : ''}`}
      data-approval-key={approval.approvalId}
      aria-label="等待审批"
    >
      <div className="approval-strip">
        <span className="approval-dot" aria-hidden />
        <span>等待审批 · {badge.text}</span>
        <span className={`approval-countdown${seconds <= 10 ? ' is-urgent' : ''}`} aria-live="polite">
          {seconds}s
        </span>
      </div>

      <p className="approval-headline">{approval.question || 'agent 请求执行一个需要授权的操作'}</p>

      <div className="approval-meta">
        <Icon name="terminal" size={13} />
        <code>{approval.name && approval.name.length > 0 ? approval.name : '未知工具'}</code>
        <button type="button" className="approval-copy" onClick={copyCommand}>
          {copied ? '已复制' : '复制'}
        </button>
      </div>

      <div className="approval-body" tabIndex={0} role="group" aria-label="审批详情">
        <pre className="approval-command">{command}</pre>
      </div>

      <div className="approval-actions">
        {options.map((option) => {
          const isDeny = option.value === denyChoice
          return (
            <button
              key={option.value}
              ref={isDeny ? denyRef : undefined}
              type="button"
              className={`btn ${toneClass(undefined, isDeny)}`}
              disabled={busy !== null}
              onClick={() => answer(option.value)}
            >
              {option.label ?? option.value}
            </button>
          )
        })}
      </div>

      <p className="approval-hint">
        同意后以当前权限执行；拒绝会让 agent 收到 [审批拒绝] 并调整策略。
        <span className="approval-hint-key">Esc</span> 拒绝 · 超时视为拒绝
      </p>
    </section>
  )
}

/** tone → 按钮样式（tone 由决策层给，前端不猜业务语义）。 */
function toneClass(tone: string | undefined, isDeny: boolean): string {
  if (tone === 'danger' || isDeny) {
    return 'btn-danger'
  }
  if (tone === 'muted') {
    return ''
  }
  return 'btn-primary'
}
