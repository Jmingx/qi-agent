import { useEffect, useRef, useState } from 'react'

/**
 * 节流值：流式场景下限制 markdown 重解析频率（默认 120ms）。
 * leading + trailing 都保证：首个增量立即出现，最后一个增量不丢。
 */
export function useThrottledValue<T>(value: T, intervalMs = 120, enabled = true): T {
  const [throttled, setThrottled] = useState(value)
  const lastRunRef = useRef(0)
  const timerRef = useRef<number | null>(null)

  useEffect(() => {
    if (!enabled || intervalMs <= 0) {
      setThrottled(value)
      return undefined
    }
    const now = Date.now()
    const elapsed = now - lastRunRef.current
    if (elapsed >= intervalMs) {
      lastRunRef.current = now
      setThrottled(value)
      return undefined
    }
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
    }
    timerRef.current = window.setTimeout(() => {
      lastRunRef.current = Date.now()
      timerRef.current = null
      setThrottled(value)
    }, intervalMs - elapsed)
    return () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current)
        timerRef.current = null
      }
    }
  }, [enabled, intervalMs, value])

  return enabled ? throttled : value
}
