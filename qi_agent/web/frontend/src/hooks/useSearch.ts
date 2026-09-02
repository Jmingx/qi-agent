import { useEffect, useState } from 'react'
import type { MutableRefObject } from 'react'
import { type ConnectionStatus, type SessionSearchResponse, type SessionSearchResult, type WsClient } from '../appModel'

type UseSearchArgs = {
  clientRef: MutableRefObject<WsClient | null>
  connectionState: ConnectionStatus
  authToken: string | null
}

type UseSearchResult = {
  searchOpen: boolean
  setSearchOpen: (value: boolean | ((current: boolean) => boolean)) => void
  searchQuery: string
  setSearchQuery: (value: string) => void
  searchResults: SessionSearchResult[]
  searchLoading: boolean
}

export function useSearch({
  clientRef,
  connectionState,
  authToken,
}: UseSearchArgs): UseSearchResult {
  const [searchOpen, setSearchOpen] = useState(true)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<SessionSearchResult[]>([])
  const [searchLoading, setSearchLoading] = useState(false)

  useEffect(() => {
    const client = clientRef.current
    const query = searchQuery.trim()
    if (!query) {
      setSearchResults([])
      setSearchLoading(false)
      return undefined
    }
    if (!client || connectionState !== 'connected' || authToken === null) {
      setSearchLoading(false)
      return undefined
    }

    let cancelled = false
    const timerId = window.setTimeout(() => {
      setSearchLoading(true)
      void client.call<SessionSearchResponse>('session/search', { query })
        .then((response) => {
          if (!cancelled) {
            setSearchResults(response.results ?? [])
          }
        })
        .catch(() => {
          if (!cancelled) {
            setSearchResults([])
          }
        })
        .finally(() => {
          if (!cancelled) {
            setSearchLoading(false)
          }
        })
    }, 300)

    return () => {
      cancelled = true
      window.clearTimeout(timerId)
    }
  }, [searchQuery, authToken, connectionState, clientRef])

  return {
    searchOpen,
    setSearchOpen,
    searchQuery,
    setSearchQuery,
    searchResults,
    searchLoading,
  }
}
