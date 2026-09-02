export const AUTH_TOKEN_STORAGE_KEY = 'qi_web_token'

export function readAuthToken(): string | null {
  const value = window.localStorage.getItem(AUTH_TOKEN_STORAGE_KEY)
  return value === null ? null : value
}

export function writeAuthToken(nextToken: string | null): void {
  const trimmed = nextToken?.trim() ?? ''
  if (trimmed) {
    window.localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, trimmed)
    return
  }
  window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY)
}

export function clearAuthToken(): void {
  window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY)
}

export function isLoopbackHost(hostname: string): boolean {
  return hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '::1'
    || hostname.startsWith('127.')
}
