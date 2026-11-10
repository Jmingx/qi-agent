import { memo } from 'react'
import { type TextEntry } from '../appModel'
import { ZH_CN } from '../i18n/zh-CN'
import { MarkdownView } from './MarkdownView'
import { Icon } from './ui/Icon'
import { formatClock } from '../utils/format'

type MessageBubbleProps = {
  entry: TextEntry
  highlighted: boolean
  jaegerUrl: string
  onCopy: (text: string) => void
  onEdit?: (entry: TextEntry) => void
  onRetry?: (entry: TextEntry) => void
}

/**
 * 单条消息（用户 / 助手 / 系统）。
 * 视觉（UI v3 §5.2）：用户消息是浅底块（不再是紫渐变实心），助手消息走 markdown 文档流。
 */
function MessageBubbleBase({
  entry,
  highlighted,
  jaegerUrl,
  onCopy,
  onEdit,
  onRetry,
}: MessageBubbleProps) {
  const canEdit = entry.role === 'user' && Boolean(onEdit)
  const canRetry = entry.role === 'user' && entry.variant === 'error' && Boolean(onRetry)
  const canOpenTrace = entry.role === 'assistant' && Boolean(entry.traceId)
  const time = formatClock(entry.time)

  if (entry.role === 'system') {
    return (
      <div className={`msg msg--system${highlighted ? ' highlighted' : ''}`}>
        <div className={`sys-note${entry.variant === 'error' ? ' is-error' : ''}`}>{entry.content}</div>
      </div>
    )
  }

  const isUser = entry.role === 'user'

  return (
    <div className={`msg ${isUser ? 'msg--user' : 'msg--assistant'}${highlighted ? ' highlighted' : ''}`}>
      {isUser ? (
        <div className={`user-bubble${entry.variant === 'error' ? ' is-error' : ''}`}>
          {entry.variant === 'error' && (
            <span className="chip chip--danger" style={{ marginRight: 6 }}>
              <Icon name="alert" size={11} /> 失败
            </span>
          )}
          {entry.content}
        </div>
      ) : (
        <div className="turn-body">
          <MarkdownView content={entry.content} />
        </div>
      )}

      <div className="msg-actions">
        <button
          type="button"
          className="icon-action"
          onClick={() => onCopy(entry.content)}
          title={ZH_CN.messageBubble.copyTitle}
          aria-label={ZH_CN.messageBubble.copyTitle}
        >
          <Icon name="copy" size={13} />
        </button>
        {canEdit && (
          <button
            type="button"
            className="icon-action"
            onClick={() => onEdit?.(entry)}
            title={`${ZH_CN.messageBubble.editLabel}并重发`}
            aria-label={`${ZH_CN.messageBubble.editLabel}并重发`}
          >
            <Icon name="pencil" size={13} />
          </button>
        )}
        {canRetry && (
          <button
            type="button"
            className="icon-action"
            onClick={() => onRetry?.(entry)}
            title="重试这条消息"
            aria-label="重试这条消息"
          >
            <Icon name="regenerate" size={13} />
          </button>
        )}
        {canOpenTrace && (
          <button
            type="button"
            className="icon-action"
            title="在 Jaeger 中查看本次调用链"
            aria-label="在 Jaeger 中查看本次调用链"
            onClick={() => {
              if (!entry.traceId) {
                return
              }
              window.open(`${jaegerUrl}/trace/${entry.traceId}`, '_blank', 'noopener,noreferrer')
            }}
          >
            <Icon name="link" size={13} />
          </button>
        )}
        {time && <span className="msg-time">{time}</span>}
      </div>
    </div>
  )
}

export const MessageBubble = memo(MessageBubbleBase)
