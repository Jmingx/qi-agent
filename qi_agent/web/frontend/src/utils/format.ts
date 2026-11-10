/** 展示格式化：耗时 / token / 时间 / 日期分组。纯函数，便于单测。 */

const DAY_NAMES = ['天', '一', '二', '三', '四', '五', '六']

/** 耗时：<1ms / 12ms / 1.4s（取代以前失真的 0ms）。 */
export function formatDuration(ms: number | undefined | null): string {
  if (ms === undefined || ms === null || Number.isNaN(ms)) {
    return ''
  }
  if (ms < 1) {
    return '<1ms'
  }
  if (ms < 1000) {
    return `${Math.round(ms)}ms`
  }
  const seconds = ms / 1000
  if (seconds < 60) {
    return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)}s`
  }
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  return `${minutes}m${rest.toString().padStart(2, '0')}s`
}

/** token 数：820 / 3.4k / 128k。 */
export function formatTokens(tokens: number | undefined | null): string {
  if (!tokens || tokens < 0) {
    return '0'
  }
  if (tokens < 1000) {
    return String(Math.round(tokens))
  }
  const k = tokens / 1000
  return `${k >= 100 ? Math.round(k) : k.toFixed(1).replace(/\.0$/, '')}k`
}

/** 环状进度百分比（0–100 整数，夹紧）。 */
export function percent(used: number, limit: number): number {
  if (!limit || limit <= 0) {
    return 0
  }
  return Math.max(0, Math.min(100, Math.round((used / limit) * 100)))
}

function toDate(value?: string | number | null): Date | null {
  if (value === undefined || value === null || value === '') {
    return null
  }
  if (typeof value === 'number') {
    const fromNumber = new Date(value < 1e12 ? value * 1000 : value)
    return Number.isNaN(fromNumber.getTime()) ? null : fromNumber
  }
  const text = String(value).trim()
  // 仅 HH:MM(:SS) 的会话内时间戳 → 按今天处理（不做跨天判断）
  if (/^\d{1,2}:\d{2}(:\d{2})?$/.test(text)) {
    const [h, m, s] = text.split(':').map((part) => Number(part))
    const date = new Date()
    date.setHours(h, m, s || 0, 0)
    return date
  }
  const parsed = new Date(text.replace(' ', 'T'))
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

/** 消息时间：HH:MM（无效则空串）。 */
export function formatClock(value?: string | number | null): string {
  const date = toDate(value)
  if (!date) {
    return ''
  }
  return `${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}`
}

/**
 * 日期分组标签：今天 / 昨天 / M月D日 周X。
 * 只有能解析出「日期」的输入才返回标签（仅 HH:MM 的输入返回 null，避免历史会话被误判成今天）。
 */
export function formatDayLabel(value?: string | number | null): string | null {
  if (typeof value !== 'number') {
    const text = String(value ?? '').trim()
    if (!/\d{4}-\d{2}-\d{2}|\d{4}\/\d{1,2}\/\d{1,2}/.test(text)) {
      return null
    }
  }
  const date = toDate(value)
  if (!date) {
    return null
  }
  const today = new Date()
  const startOf = (input: Date): number => new Date(input.getFullYear(), input.getMonth(), input.getDate()).getTime()
  const diffDays = Math.round((startOf(today) - startOf(date)) / 86_400_000)
  if (diffDays === 0) {
    return '今天'
  }
  if (diffDays === 1) {
    return '昨天'
  }
  if (diffDays > 1 && diffDays < 7) {
    return `${date.getMonth() + 1}月${date.getDate()}日 周${DAY_NAMES[date.getDay()]}`
  }
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`
}

/** 会话列表的分组键：今天 / 昨天 / 7 天内 / 更早。 */
export function sessionGroupKey(updatedAt?: number | null): 'today' | 'yesterday' | 'week' | 'earlier' {
  if (!updatedAt) {
    return 'earlier'
  }
  const date = new Date(updatedAt < 1e12 ? updatedAt * 1000 : updatedAt)
  if (Number.isNaN(date.getTime())) {
    return 'earlier'
  }
  const startOf = (input: Date): number => new Date(input.getFullYear(), input.getMonth(), input.getDate()).getTime()
  const diffDays = Math.round((startOf(new Date()) - startOf(date)) / 86_400_000)
  if (diffDays <= 0) {
    return 'today'
  }
  if (diffDays === 1) {
    return 'yesterday'
  }
  if (diffDays < 7) {
    return 'week'
  }
  return 'earlier'
}

export const SESSION_GROUP_LABELS: Record<'today' | 'yesterday' | 'week' | 'earlier', string> = {
  today: '今天',
  yesterday: '昨天',
  week: '7 天内',
  earlier: '更早',
}

/** 相对时间：刚刚 / 5 分钟前 / 3 小时前 / M月D日。 */
export function formatRelative(updatedAt?: number | null): string {
  if (!updatedAt) {
    return ''
  }
  const date = new Date(updatedAt < 1e12 ? updatedAt * 1000 : updatedAt)
  if (Number.isNaN(date.getTime())) {
    return ''
  }
  const diffMs = Date.now() - date.getTime()
  const minutes = Math.floor(diffMs / 60_000)
  if (minutes < 1) {
    return '刚刚'
  }
  if (minutes < 60) {
    return `${minutes} 分钟前`
  }
  const hours = Math.floor(minutes / 60)
  if (hours < 24) {
    return `${hours} 小时前`
  }
  return `${date.getMonth() + 1}月${date.getDate()}日`
}

/** 工具参数一行摘要（用于回合摘要行，避免展开也能看到"干了什么"）。 */
export function summarizeArguments(args: unknown, maxLength = 60): string {
  if (args === null || args === undefined) {
    return ''
  }
  if (typeof args === 'string') {
    return truncate(args, maxLength)
  }
  if (typeof args !== 'object') {
    return String(args)
  }
  const entries = Object.entries(args as Record<string, unknown>)
  if (entries.length === 0) {
    return ''
  }
  const parts = entries.map(([key, value]) => {
    if (typeof value === 'string') {
      return `${key}=${truncate(value, 40)}`
    }
    if (value === null || value === undefined || typeof value !== 'object') {
      return `${key}=${String(value)}`
    }
    if (Array.isArray(value)) {
      return `${key}=[${value.length}]`
    }
    return `${key}={…}`
  })
  return truncate(parts.join(' '), maxLength)
}

export function truncate(text: string, maxLength: number): string {
  const normalized = String(text ?? '').replace(/\s+/g, ' ').trim()
  return normalized.length > maxLength ? `${normalized.slice(0, maxLength)}…` : normalized
}
