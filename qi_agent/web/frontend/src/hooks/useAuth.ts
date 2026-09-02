import { useCallback, useState } from 'react'
import { readAuthToken, writeAuthToken } from '../auth'

type UseAuthResult = {
  authToken: string | null
  authBusy: boolean
  authError: string | null
  setAuthBusy: (value: boolean) => void
  setAuthError: (value: string | null) => void
  handleLogin: (nextToken: string) => void
}

export function useAuth(): UseAuthResult {
  const [authToken, setAuthToken] = useState<string | null>(() => readAuthToken())
  const [authBusy, setAuthBusy] = useState(false)
  const [authError, setAuthError] = useState<string | null>(null)

  const handleLogin = useCallback((nextToken: string) => {
    const trimmed = nextToken.trim()
    setAuthError(null)
    setAuthBusy(true)
    writeAuthToken(trimmed)
    setAuthToken(trimmed)
  }, [])

  return {
    authToken,
    authBusy,
    authError,
    setAuthBusy,
    setAuthError,
    handleLogin,
  }
}
