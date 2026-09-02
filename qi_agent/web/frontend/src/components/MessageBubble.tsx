import { memo } from 'react'
import { type TextEntry } from '../appModel'
import { ZH_CN } from '../i18n/zh-CN'

type MessageBubbleProps = {
  entry: TextEntry
  highlighted: boolean
  onCopy: (text: string) => void
  onEdit?: (entry: TextEntry) => void
  onRetry?: (entry: TextEntry) => void
}

function MessageBubbleBase({
  entry,
  highlighted,
  onCopy,
  onEdit,
  onRetry,
}: MessageBubbleProps) {
  const bubbleClassName = [
    'bubble',
    highlighted ? 'highlighted' : '',
    entry.variant === 'error' ? 'error' : '',
  ].filter(Boolean).join(' ')

  const canEdit = entry.role === 'user' && Boolean(onEdit)
  const canRetry = entry.role === 'user' && entry.variant === 'error' && Boolean(onRetry)

  return (
    <div className="bubble-wrap">
      {entry.role !== 'system' && (
        <div className={bubbleClassName}>
          <button
            type="button"
            className="copy-btn"
            onClick={() => onCopy(entry.content)}
            title={ZH_CN.messageBubble.copyTitle}
          >
            {ZH_CN.messageBubble.copyLabel}
          </button>
          <div className="bubble-content">{entry.content}</div>
          {(canEdit || canRetry) && (
            <div className="bubble-actions">
              {canEdit && (
                <button
                  type="button"
                  className="bubble-action-btn"
                  onClick={() => onEdit?.(entry)}
                >
                  {ZH_CN.messageBubble.editLabel}
                </button>
              )}
              {canRetry && (
                <button
                  type="button"
                  className="bubble-action-btn primary"
                  onClick={() => onRetry?.(entry)}
                >
                  {ZH_CN.messageBubble.retryLabel}
                </button>
              )}
            </div>
          )}
        </div>
      )}
      {entry.role === 'system' && (
        <div className={`sys-note ${entry.variant === 'error' ? 'error' : ''}`}>
          {entry.content}
        </div>
      )}
      {entry.role !== 'system' && entry.time && <div className="time">{entry.time}</div>}
    </div>
  )
}

export const MessageBubble = memo(MessageBubbleBase)
