import type { IconName } from './components/ui/Icon'

export type ThemeMode = 'light' | 'dark' | 'system'

export const THEME_STORAGE_KEY = 'qi_theme'

// 这里只登记 TS 侧已经稳定下来的主题 token 名称，真正的 CSS 变量由 styles/tokens.css 维护。
// 后续如果新增 token，先补这个清单，再考虑是否要扩展主题映射层。
export const THEME_TOKEN_KEYS = [
  '--bg',
  '--bg-subtle',
  '--bg-muted',
  '--fg',
  '--fg-muted',
  '--fg-subtle',
  '--border',
  '--border-strong',
  '--accent',
  '--accent-hover',
  '--accent-subtle',
  '--accent-contrast',
  '--ok',
  '--ok-bg',
  '--warn',
  '--warn-bg',
  '--danger',
  '--danger-bg',
  '--code-bg',
  '--code-border',
  '--code-fg',
  '--overlay',
  '--toast-bg',
  '--toast-fg',
  '--r-sm',
  '--r-md',
  '--r-lg',
  '--r-full',
  '--shadow-pop',
  '--shadow-focus',
  '--sidebar-w',
  '--panel-w',
  '--content-max',
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

export function getThemeIcon(mode: ThemeMode): IconName {
  if (mode === 'system') {
    return 'monitor'
  }
  if (mode === 'light') {
    return 'sun'
  }
  return 'moon'
}
