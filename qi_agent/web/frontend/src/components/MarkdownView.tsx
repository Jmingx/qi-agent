import { memo, useMemo, useState } from 'react'
import { parseMarkdown, type InlineToken, type MarkdownNode } from '../utils/markdown'
import { useThrottledValue } from '../hooks/useThrottledValue'
import { Icon } from './ui/Icon'

/** 单块渲染：行内 token → React 元素（不产生 HTML 字符串，天然免疫 XSS）。 */
function Inline({ tokens }: { tokens: InlineToken[] }) {
  return (
    <>
      {tokens.map((token, index) => {
        switch (token.type) {
          case 'code':
            return <code key={index} className="md-code-inline">{token.text}</code>
          case 'strong':
            return <strong key={index}>{token.text}</strong>
          case 'em':
            return <em key={index}>{token.text}</em>
          case 'strike':
            return <del key={index}>{token.text}</del>
          case 'link':
            return (
              <a key={index} href={token.href} target="_blank" rel="noopener noreferrer">
                {token.text}
              </a>
            )
          default:
            return <span key={index}>{token.text}</span>
        }
      })}
    </>
  )
}

/** 代码块：语言标签 + 复制 + 超长折叠。 */
function CodeBlock({ lang, code, closed }: { lang: string; code: string; closed: boolean }) {
  const [copied, setCopied] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const lines = code.split('\n')
  const collapsible = lines.length > 40
  const visible = collapsible && !expanded ? lines.slice(0, 24).join('\n') : code

  const copy = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
    } catch {
      setCopied(false)
    }
  }

  return (
    <figure className={`code-block${closed ? '' : ' streaming'}`}>
      <figcaption className="code-head">
        <span className="code-lang">{lang || 'text'}</span>
        <span className="code-meta">{lines.length} 行</span>
        <button type="button" className="code-copy" onClick={() => void copy()} title="复制代码">
          <Icon name={copied ? 'check' : 'copy'} size={13} />
          <span>{copied ? '已复制' : '复制'}</span>
        </button>
      </figcaption>
      <pre className="code-body">
        <code>{visible}</code>
      </pre>
      {collapsible && (
        <button type="button" className="code-expand" onClick={() => setExpanded((current) => !current)}>
          {expanded ? '收起' : `展开全部（${lines.length} 行）`}
        </button>
      )}
    </figure>
  )
}

function Block({ node }: { node: MarkdownNode }) {
  switch (node.type) {
    case 'heading': {
      const level = Math.min(3, Math.max(1, node.level))
      const Tag = (`h${level + 2}`) as 'h3' | 'h4' | 'h5'
      return (
        <Tag className={`md-h md-h${level}`}>
          <Inline tokens={node.tokens} />
        </Tag>
      )
    }
    case 'code':
      return <CodeBlock lang={node.lang} code={node.code} closed={node.closed} />
    case 'list':
      return node.ordered
        ? (
            <ol className="md-list">
              {node.items.map((item, index) => (
                <li key={index} className={`md-li depth-${item.depth}`}>
                  <Inline tokens={item.tokens} />
                </li>
              ))}
            </ol>
          )
        : (
            <ul className="md-list">
              {node.items.map((item, index) => (
                <li key={index} className={`md-li depth-${item.depth}`}>
                  <Inline tokens={item.tokens} />
                </li>
              ))}
            </ul>
          )
    case 'quote':
      return (
        <blockquote className={`md-quote${node.variant ? ' callout' : ''}`}>
          {node.variant && <span className="md-quote-tag">{node.variant}</span>}
          <Inline tokens={node.tokens} />
        </blockquote>
      )
    case 'table':
      return (
        <div className="md-table-wrap">
          <table className="md-table">
            <thead>
              <tr>
                {node.header.map((cell, index) => (
                  <th key={index}><Inline tokens={cell} /></th>
                ))}
              </tr>
            </thead>
            <tbody>
              {node.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {row.map((cell, cellIndex) => (
                    <td key={cellIndex}><Inline tokens={cell} /></td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )
    case 'hr':
      return <hr className="md-hr" />
    default:
      return (
        <p className="md-p">
          <Inline tokens={node.tokens} />
        </p>
      )
  }
}

type MarkdownViewProps = {
  content: string
  /** 流式：节流重解析 + 尾部光标 */
  streaming?: boolean
}

function MarkdownViewBase({ content, streaming = false }: MarkdownViewProps) {
  // 流式期间按 120ms 节流（长回答避免每个 delta 全量重解析）
  const throttled = useThrottledValue(content, 120, streaming)
  const nodes = useMemo(() => parseMarkdown(throttled), [throttled])

  return (
    <div className={`md${streaming ? ' is-streaming' : ''}`}>
      {nodes.map((node, index) => (
        <Block key={index} node={node} />
      ))}
      {streaming && <span className="stream-caret" aria-hidden="true" />}
    </div>
  )
}

export const MarkdownView = memo(MarkdownViewBase)
