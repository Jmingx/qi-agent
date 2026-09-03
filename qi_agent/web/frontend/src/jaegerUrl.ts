const JAEGER_BASE_URL = '/jaeger'
const JAEGER_STORAGE_KEY = 'qi_jaeger_url'

export function normalizeJaegerUrl(raw: string | null | undefined): string {
  const candidate = (raw ?? '').trim().replace(/\/+$/, '')
  if (!candidate) {
    return JAEGER_BASE_URL
  }
  // 默认使用同源相对路径，这样本地 9004 和远程隧道都能通过 web 反代命中 Jaeger。
  if (candidate.startsWith('/')) {
    return candidate || JAEGER_BASE_URL
  }
  try {
    return new URL(candidate).toString().replace(/\/$/, '')
  } catch {
    return JAEGER_BASE_URL
  }
}

export function readJaegerUrl(): string {
  try {
    return normalizeJaegerUrl(window.localStorage.getItem(JAEGER_STORAGE_KEY))
  } catch {
    return JAEGER_BASE_URL
  }
}
