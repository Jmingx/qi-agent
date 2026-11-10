export type EventLogEntry = {
  ts: string
  action: string
  payload?: unknown
}

export const EVENT_LOG_STORAGE_KEY = 'qi_event_log'
export const EVENT_LOG_LIMIT = 200

function readEventLog(): EventLogEntry[] {
  try {
    const raw = window.localStorage.getItem(EVENT_LOG_STORAGE_KEY)
    if (!raw) {
      return []
    }
    const parsed = JSON.parse(raw) as EventLogEntry[]
    return Array.isArray(parsed) ? parsed.filter((item) => item && typeof item.action === 'string') : []
  } catch {
    return []
  }
}

export function logEvent(action: string, payload?: unknown): void {
  try {
    const next: EventLogEntry[] = [
      ...readEventLog(),
      {
        ts: new Date().toISOString(),
        action,
        payload,
      },
    ].slice(-EVENT_LOG_LIMIT)
    window.localStorage.setItem(EVENT_LOG_STORAGE_KEY, JSON.stringify(next))
  } catch {
    // 本地事件日志只做辅助排查，写失败不能影响主流程。
  }
}
