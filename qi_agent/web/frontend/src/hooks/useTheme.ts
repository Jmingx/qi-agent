import { useCallback, useEffect, useState } from 'react'
import {
  cycleThemeMode,
  getThemeIcon,
  getThemeLabel,
  readThemeMode,
  resolveTheme,
  THEME_STORAGE_KEY,
  type ThemeMode,
} from '../theme'

type UseThemeResult = {
  themeMode: ThemeMode
  themeLabel: string
  themeIcon: string
  cycleTheme: () => void
}

export function useTheme(): UseThemeResult {
  const [themeMode, setThemeMode] = useState<ThemeMode>(() => readThemeMode())

  useEffect(() => {
    const root = document.documentElement
    const media = window.matchMedia('(prefers-color-scheme: dark)')

    const applyTheme = (): void => {
      const resolved = resolveTheme(themeMode)
      root.dataset.theme = resolved
      root.style.colorScheme = resolved
      window.localStorage.setItem(THEME_STORAGE_KEY, themeMode)
    }

    applyTheme()
    if (themeMode === 'system') {
      const onChange = (): void => applyTheme()
      media.addEventListener('change', onChange)
      return () => media.removeEventListener('change', onChange)
    }
    return undefined
  }, [themeMode])

  const cycleTheme = useCallback(() => {
    setThemeMode((current) => cycleThemeMode(current))
  }, [])

  return {
    themeMode,
    themeLabel: getThemeLabel(themeMode),
    themeIcon: getThemeIcon(themeMode),
    cycleTheme,
  }
}
