import { memo, useState } from 'react'
import { type ToolEntry } from '../appModel'
import { Icon, toolIcon } from './ui/Icon'
import { formatDuration, summarizeArguments } from '../utils/format'

type StepRowProps = {
  tool: ToolEntry
}

function statusOf(tool: ToolEntry): { label: string; cls: string } {
  if (tool.status === 'blocked' || (tool.result && !tool.result.ok)) {
    return { label: tool.status === 'blocked' ? '已拦截' : '失败', cls: 'chip--danger' }
  }
  if (!tool.result) {
    return { label: '运行中', cls: 'chip--running' }
  }
  return { label: '完成', cls: 'chip--ok' }
}

/**
 * 单个工具步骤（UI v3 §5.3）：一行摘要 + 按需展开。
 * 设计要点：参数摘要回归（回答"干了什么"）、输出可展开（回答"结果如何"）、
 * 过滤模板噪声进度（"已开始执行/执行完成"对判断无信息量）。
 */
function StepRowBase({ tool }: StepRowProps) {
  const [argsOpen, setArgsOpen] = useState(false)
  const [outputOpen, setOutputOpen] = useState(false)
  const [copied, setCopied] = useState(false)
  const status = statusOf(tool)
  const argsSummary = summarizeArguments(tool.toolArguments)
  const output = tool.result?.outputPreview ?? ''
  const failed = status.cls === 'chip--danger'
  const progressLines = tool.progress.filter((item) => {
    const text = item.text.trim()
    return text && text !== '已开始执行' && text !== '正在执行' && !text.startsWith('正在执行 ')
  })

  const copyOutput = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(output)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
    }
  }

  return (
    <div className={`step${failed ? ' is-failed' : ''}`}>
      <div className="step-head">
        <span className="step-icon">
          <Icon name={toolIcon(tool.name)} size={14} />
        </span>
        <span className="step-name">{tool.name}</span>
        {argsSummary && <span className="step-args" title={argsSummary}>{argsSummary}</span>}
        <span className="step-spacer" />
        <span className={`chip ${status.cls}`}>{status.label}</span>
        {tool.result && <span className="step-stat">{formatDuration(tool.result.durationMs)}</span>}
      </div>

      {tool.status === 'blocked' && tool.reason && (
        <div className="step-args" style={{ color: 'var(--danger)' }}>
          {tool.reason}
        </div>
      )}

      {progressLines.length > 0 && !tool.result && (
        <div className="step-progress" aria-live="polite">
          {progressLines.slice(-3).map((item, index) => (
            <div key={`${item.time}-${index}`}>{item.text}</div>
          ))}
        </div>
      )}

      <div className="step-head">
        {tool.toolArguments !== undefined && tool.toolArguments !== null && (
          <button type="button" className="step-toggle" onClick={() => setArgsOpen((current) => !current)}>
            <Icon name={argsOpen ? 'chevron-down' : 'chevron-right'} size={12} /> 参数
          </button>
        )}
        {tool.result && (
          <>
            <button type="button" className="step-toggle" onClick={() => setOutputOpen((current) => !current)}>
              <Icon name={outputOpen ? 'chevron-down' : 'chevron-right'} size={12} /> 输出
            </button>
            {tool.result.truncated && <span className="step-stat">已截断（原始 {tool.result.outputBytes} 字节）</span>}
          </>
        )}
      </div>

      {argsOpen && (
        <div className="step-detail">
          <div className="step-detail-head">
            <span>参数</span>
          </div>
          <pre className="step-detail-body">{JSON.stringify(tool.toolArguments ?? {}, null, 2)}</pre>
        </div>
      )}

      {outputOpen && tool.result && (
        <div className="step-detail">
          <div className="step-detail-head">
            <span>输出{tool.result.truncated ? '（预览，已截断）' : ''}</span>
            <button type="button" className="step-btn" onClick={() => void copyOutput()}>
              <Icon name={copied ? 'check' : 'copy'} size={12} /> {copied ? '已复制' : '复制'}
            </button>
          </div>
          <pre className="step-detail-body">{output || tool.result.summary || '（无输出）'}</pre>
        </div>
      )}
    </div>
  )
}

export const StepRow = memo(StepRowBase)
