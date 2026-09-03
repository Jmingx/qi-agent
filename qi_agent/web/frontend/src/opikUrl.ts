const OPIK_BASE_URL = 'http://127.0.0.1:5173'
const OPIK_STORAGE_KEY = 'qi_opik_url'

export function normalizeOpikUrl(raw: string | null | undefined): string {
  const candidate = (raw ?? '').trim().replace(/\/+$/, '')
  if (!candidate) {
    return OPIK_BASE_URL
  }
  // 评测平台默认连本地自托管地址；若用户改成相对路径，也允许继续沿用。
  if (candidate.startsWith('/')) {
    return candidate || OPIK_BASE_URL
  }
  try {
    return new URL(candidate).toString().replace(/\/$/, '')
  } catch {
    return OPIK_BASE_URL
  }
}

export function readOpikUrl(): string {
  try {
    return normalizeOpikUrl(window.localStorage.getItem(OPIK_STORAGE_KEY))
  } catch {
    return OPIK_BASE_URL
  }
}
