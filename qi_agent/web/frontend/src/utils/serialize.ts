import { stringifyValue, type StreamEntry, type SubTaskEntry, type TextEntry, type ToolEntry } from '../appModel'

export type SerializeThreadMeta = Record<string, unknown>

export type SerializedThread = {
  markdown: string
  json: string
}

function isTextEntry(entry: StreamEntry): entry is TextEntry {
  return entry.kind === 'message'
}

function isToolEntry(entry: StreamEntry): entry is ToolEntry {
  return entry.kind === 'tool'
}

function isSubTaskEntry(entry: StreamEntry): entry is SubTaskEntry {
  return entry.kind === 'subtask'
}

function toJsonValue(value: unknown): unknown {
  if (
    value === null
    || typeof value === 'string'
    || typeof value === 'number'
    || typeof value === 'boolean'
  ) {
    return value
  }
  if (Array.isArray(value)) {
    return value.map((item) => toJsonValue(item))
  }
  if (typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([key, nextValue]) => [
        key,
        toJsonValue(nextValue),
      ]),
    )
  }
  return String(value ?? '')
}

function renderTextEntry(entry: TextEntry): string {
  const heading = `## ${entry.role.toUpperCase()}${entry.time ? ` · ${entry.time}` : ''}`
  return [heading, entry.content].join('\n\n')
}

function renderToolEntry(entry: ToolEntry): string {
  const parts = [
    `## TOOL · ${entry.name}${entry.time ? ` · ${entry.time}` : ''}`,
    `- 状态：${entry.status}`,
    `- 参数：`,
    '```json',
    stringifyValue(entry.toolArguments),
    '```',
  ]
  if (entry.reason) {
    parts.push(`- 原因：${entry.reason}`)
  }
  if (entry.result) {
    parts.push(
      '- 结果：',
      '```json',
      stringifyValue(entry.result),
      '```',
    )
  }
  return parts.join('\n')
}

function renderSubTaskEntry(entry: SubTaskEntry): string {
  const parts = [
    `## SUBTASK · ${entry.subId}${entry.time ? ` · ${entry.time}` : ''}`,
    `- 目标：${entry.goal}`,
    `- 状态：${entry.status}`,
  ]
  if (entry.progress.length > 0) {
    parts.push('- 进度：', ...entry.progress.map((item) => `  - ${item.time} ${item.text}`))
  }
  if (entry.resultText) {
    parts.push(`- 结果：${entry.resultText}`)
  }
  if (entry.reason) {
    parts.push(`- 原因：${entry.reason}`)
  }
  return parts.join('\n')
}

function renderMarkdown(entries: StreamEntry[], meta: SerializeThreadMeta): string {
  const header = ['# 会话导出']
  for (const [key, value] of Object.entries(meta)) {
    header.push(`- ${key}：${stringifyValue(value)}`)
  }

  const body = entries.map((entry) => {
    if (isTextEntry(entry)) {
      return renderTextEntry(entry)
    }
    if (isToolEntry(entry)) {
      return renderToolEntry(entry)
    }
    if (isSubTaskEntry(entry)) {
      return renderSubTaskEntry(entry)
    }
    return `## UNKNOWN · ${entry.id}`
  })

  return [...header, '', ...body].join('\n\n')
}

export function serializeThread(entries: StreamEntry[], meta: SerializeThreadMeta = {}): SerializedThread {
  const json = JSON.stringify(
    {
      meta: toJsonValue(meta),
      entries: entries.map((entry) => toJsonValue(entry)),
    },
    null,
    2,
  )

  return {
    markdown: renderMarkdown(entries, meta),
    json,
  }
}
