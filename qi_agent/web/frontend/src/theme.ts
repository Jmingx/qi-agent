export type ThemeMode = 'light' | 'dark' | 'system'

export const THEME_STORAGE_KEY = 'qi_theme'

// 这里只登记 TS 侧已经稳定下来的主题 token 名称，真正的 CSS 变量仍由 style.css 维护。
// 后续如果新增 token，先补这个清单，再考虑是否要扩展主题映射层。
export const THEME_TOKEN_KEYS = [
  '--bg',
  '--bg-soft',
  '--panel',
  '--panel-strong',
  '--panel-muted',
  '--border',
  '--border-strong',
  '--text',
  '--muted',
  '--muted-2',
  '--brand',
  '--brand-2',
  '--brand-3',
  '--user-start',
  '--user-end',
  '--ai-start',
  '--ai-end',
  '--success-bg',
  '--success-text',
  '--success-dot',
  '--warn-bg',
  '--warn-text',
  '--warn-dot',
  '--danger-bg',
  '--danger-text',
  '--danger-dot',
  '--sys-bg',
  '--sys-text',
  '--sys-border',
  '--toast-bg',
  '--toast-text',
  '--shadow-lg',
  '--shadow-md',
  '--shadow-sm',
  '--bg-blob-1',
  '--bg-blob-2',
] as const

export function readThemeMode(): ThemeMode {
  const value = window.localStorage.getItem(THEME_STORAGE_KEY)
  if (value === 'light' || value === 'dark' || value === 'system') {
    return value
  }
  return 'system'
}

export function resolveTheme(mode: ThemeMode): 'light' | 'dark' {
  if (mode === 'light' || mode === 'dark') {
    return mode
  }
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function cycleThemeMode(mode: ThemeMode): ThemeMode {
  if (mode === 'system') {
    return 'light'
  }
  if (mode === 'light') {
    return 'dark'
  }
  return 'system'
}

export function getThemeLabel(mode: ThemeMode): string {
  if (mode === 'system') {
    return '跟随系统'
  }
  if (mode === 'light') {
    return '浅色'
  }
  return '深色'
}

export function getThemeIcon(mode: ThemeMode): string {
  if (mode === 'system') {
    return '◐'
  }
  if (mode === 'light') {
    return '☀'
  }
  return '☾'
}
