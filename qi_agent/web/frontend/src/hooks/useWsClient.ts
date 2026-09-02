import { useEffect, useRef, useState } from 'react'
import type { MutableRefObject } from 'react'
import { clearAuthToken } from '../auth'
import { ConnectionStatus, WsClient } from '../ws'
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
}

export function useWsClient({
  authToken,
  setAuthBusy,
  setAuthError,
}: UseWsClientArgs): UseWsClientResult {
  const clientRef = useRef<WsClient | null>(null)
  const connectionStateRef = useRef<ConnectionStatus>('disconnected')
  const [connectionState, setConnectionState] = useState<ConnectionStatus>('disconnected')

  useEffect(() => {
    connectionStateRef.current = connectionState
  }, [connectionState])

  useEffect(() => {
    if (authToken === null) {
      clientRef.current = null
      setConnectionState('disconnected')
      return undefined
    }

    const client = new WsClient('/ws', () => authToken)
    clientRef.current = client
    setAuthBusy(true)
    setAuthError(null)

    client.onStatus((status) => {
      connectionStateRef.current = status
      setConnectionState(status)
    })

    void client.connect()
      .then(() => {
        setAuthBusy(false)
      })
      .catch((error) => {
        const message = getErrorMessage(error)
        setAuthBusy(false)
        setConnectionState('disconnected')
        setAuthError(message)
        clearAuthToken()
      })

    return () => {
      client.dispose()
      clientRef.current = null
      connectionStateRef.current = 'disconnected'
      setConnectionState('disconnected')
    }
  }, [authToken, setAuthBusy, setAuthError])

  return {
    clientRef,
    connectionState,
    connectionStateRef,
  }
}
