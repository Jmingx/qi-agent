import { useCallback, useEffect, useRef, useState } from 'react'
import type { MutableRefObject } from 'react'
import { clearAuthToken } from '../auth'
import { ConnectionStatus, ReconnectLimitExceededError, WsClient } from '../ws'
import { getErrorMessage } from '../appModel'

type UseWsClientArgs = {
  authToken: string | null
  setAuthBusy: (value: boolean) => void
  setAuthError: (value: string | null) => void
}

type UseWsClientResult = {
  clientRef: MutableRefObject<WsClient | null>
  connectionState: ConnectionStatus
  connectionStateRef: MutableRefObject<ConnectionStatus>
  hasReconnectExceeded: boolean
  manualReconnect: () => void
}

export function useWsClient({
  authToken,
  setAuthBusy,
  setAuthError,
}: UseWsClientArgs): UseWsClientResult {
  const clientRef = useRef<WsClient | null>(null)
  const connectionStateRef = useRef<ConnectionStatus>('disconnected')
  const [connectionState, setConnectionState] = useState<ConnectionStatus>('disconnected')
  const [hasReconnectExceeded, setHasReconnectExceeded] = useState(false)

  useEffect(() => {
    connectionStateRef.current = connectionState
  }, [connectionState])

  useEffect(() => {
    if (authToken === null) {
      clientRef.current = null
      setConnectionState('disconnected')
      setHasReconnectExceeded(false)
      return undefined
    }

    const client = new WsClient('/ws', () => authToken)
    clientRef.current = client
    setAuthBusy(true)
    setAuthError(null)

    client.onStatus((status) => {
      connectionStateRef.current = status
      setConnectionState(status)
      setHasReconnectExceeded(client.hasReconnectExceeded)
    })

    void client.connect()
      .then(() => {
        setAuthBusy(false)
      })
      .catch((error) => {
        const message = getErrorMessage(error)
        setAuthBusy(false)
        setConnectionState('disconnected')
        setHasReconnectExceeded(client.hasReconnectExceeded)
        if (error instanceof ReconnectLimitExceededError) {
          setAuthError(null)
          return
        }
        setAuthError(message)
        clearAuthToken()
      })

    return () => {
      client.dispose()
      clientRef.current = null
      connectionStateRef.current = 'disconnected'
      setConnectionState('disconnected')
      setHasReconnectExceeded(false)
    }
  }, [authToken, setAuthBusy, setAuthError])

  const manualReconnect = useCallback((): void => {
    const client = clientRef.current
    if (!client) {
      return
    }
    setHasReconnectExceeded(false)
    void client.manualReconnect()
  }, [clientRef, setHasReconnectExceeded])

  return {
    clientRef,
    connectionState,
    connectionStateRef,
    hasReconnectExceeded,
    manualReconnect,
  }
}
