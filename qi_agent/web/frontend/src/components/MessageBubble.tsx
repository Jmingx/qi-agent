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
          <div className="bubble-actions" aria-label={ZH_CN.messageBubble.copyTitle}>
            <button
              type="button"
              className="bubble-action-btn copy"
              onClick={() => onCopy(entry.content)}
              title={ZH_CN.messageBubble.copyTitle}
              aria-label={ZH_CN.messageBubble.copyTitle}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <rect x="9" y="9" width="10" height="10" rx="2" />
                <rect x="5" y="5" width="10" height="10" rx="2" />
              </svg>
            </button>
            {(canEdit || canRetry) && (
              <>
                {canEdit && (
                  <button
                    type="button"
                    className="bubble-action-btn"
                    onClick={() => onEdit?.(entry)}
                    title={ZH_CN.messageBubble.editLabel}
                    aria-label={ZH_CN.messageBubble.editLabel}
                  >
                    <svg viewBox="0 0 24 24" aria-hidden="true">
                      <path d="M4 20h4l11-11-4-4L4 16v4Z" />
                      <path d="M13.5 6.5 17.5 10.5" />
                    </svg>
                  </button>
                )}
                {canRetry && (
                  <button
                    type="button"
                    className="bubble-action-btn primary"
                    onClick={() => onRetry?.(entry)}
                    title={ZH_CN.messageBubble.retryLabel}
                    aria-label={ZH_CN.messageBubble.retryLabel}
                  >
                    <svg viewBox="0 0 24 24" aria-hidden="true">
                      <path d="M20 12a8 8 0 1 1-2.34-5.66" />
                      <path d="M20 4v6h-6" />
                    </svg>
                  </button>
                )}
              </>
            )}
          </div>
          <div className="bubble-content">{entry.content}</div>
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
