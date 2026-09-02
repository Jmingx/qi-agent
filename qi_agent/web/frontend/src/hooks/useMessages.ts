import { useCallback, useEffect, useRef, useState } from 'react'
import type { Dispatch, MutableRefObject, SetStateAction } from 'react'
import { type ConnectionStatus, type WsClient } from '../ws'
import {
  now,
  normalizeToolArguments,
  type MessageVariant,
  type PendingScrollTarget,
  type Role,
  type StreamEntry,
  type SubTaskEntry,
  type TextEntry,
  type ToolCallPayload,
  type ToolEntry,
  type ToolResultEntry,
  type ToolResultPayload,
} from '../appModel'

type UseMessagesArgs = {
  clientRef: MutableRefObject<WsClient | null>
  connectionState: ConnectionStatus
  sessionIdRef: MutableRefObject<string>
  setRunning: Dispatch<SetStateAction<boolean>>
  refreshSessions: () => Promise<void>
  onApprovalChange: Dispatch<SetStateAction<Record<string, unknown> | null>>
}

type UseMessagesResult = {
  entries: StreamEntry[]
  entriesRef: MutableRefObject<StreamEntry[]>
  highlightedMessageId: number | null
  setHighlightedMessageId: Dispatch<SetStateAction<number | null>>
  highlightMessage: (messageId: number) => void
  pendingScrollTarget: PendingScrollTarget
  messagesEndRef: MutableRefObject<HTMLDivElement | null>
  messageNodeRefs: MutableRefObject<Map<number, HTMLDivElement | null>>
  registerMessageNode: (messageId: number) => (node: HTMLDivElement | null) => void
  requestScrollToMessage: (target: PendingScrollTarget) => void
  appendMessage: (role: Role, content: string, variant?: MessageVariant) => number
  appendSystemMessage: (content: string, variant?: MessageVariant) => number
  clearEntries: () => void
  replaceEntries: (next: StreamEntry[]) => void
  updateEntriesById: (entryId: number, updater: (entry: StreamEntry) => StreamEntry) => void
  updateSubTaskEntry: (subId: string, updater: (entry: SubTaskEntry) => SubTaskEntry) => void
  appendSubTaskEntry: (entry: Omit<SubTaskEntry, 'id' | 'kind'>) => number
  beginTurn: () => void
  currentTurnRef: MutableRefObject<number>
  currentTurnAssistantSeenRef: MutableRefObject<boolean>
  turnErrorNotifiedRef: MutableRefObject<boolean>
}

function createMessageEntry(
  id: number,
  role: Role,
  content: string,
  variant: MessageVariant = 'default',
): TextEntry {
  return {
    id,
    kind: 'message',
    role,
    content,
    time: now(),
    variant,
  }
}

export function useMessages({
  clientRef,
  connectionState,
  sessionIdRef,
  setRunning,
  refreshSessions,
  onApprovalChange,
}: UseMessagesArgs): UseMessagesResult {
  const [entries, setEntries] = useState<StreamEntry[]>([])
  const entriesRef = useRef<StreamEntry[]>([])
  const [highlightedMessageId, setHighlightedMessageId] = useState<number | null>(null)
  const [pendingScrollTarget, setPendingScrollTarget] = useState<PendingScrollTarget>(null)
  const messagesEndRef = useRef<HTMLDivElement | null>(null)
  const messageNodeRefs = useRef(new Map<number, HTMLDivElement | null>())
  const highlightTimerRef = useRef<number | null>(null)
  const nextIdRef = useRef(0)
  const currentTurnRef = useRef(0)
  const currentTurnAssistantSeenRef = useRef(false)
  const turnErrorNotifiedRef = useRef(false)
  const streamingRef = useRef<{ id: number; turn: number; full: string } | null>(null)

  const syncNextId = useCallback((next: StreamEntry[]): void => {
    const maxId = next.reduce((accumulator, entry) => Math.max(accumulator, entry.id), 0)
    nextIdRef.current = Math.max(nextIdRef.current, maxId)
  }, [])

  const commitEntries = useCallback((next: StreamEntry[]): void => {
    entriesRef.current = next
    syncNextId(next)
    setEntries(next)
  }, [syncNextId])

  const nextId = useCallback((): number => {
    nextIdRef.current += 1
    return nextIdRef.current
  }, [])

  const clearStreamingState = useCallback((): void => {
    streamingRef.current = null
    currentTurnAssistantSeenRef.current = false
    turnErrorNotifiedRef.current = false
  }, [])

  const replaceEntries = useCallback((next: StreamEntry[]): void => {
    commitEntries(next)
  }, [commitEntries])

  const clearEntries = useCallback((): void => {
    commitEntries([])
  }, [commitEntries])

  const appendMessage = useCallback((
    role: Role,
    content: string,
    variant: MessageVariant = 'default',
  ): number => {
    const id = nextId()
    const next = [
      ...entriesRef.current,
      createMessageEntry(id, role, content, variant),
    ]
    commitEntries(next)
    return id
  }, [commitEntries, nextId])

  const appendSystemMessage = useCallback((
    content: string,
    variant: MessageVariant = 'default',
  ): number => appendMessage('system', content, variant), [appendMessage])

  const updateEntriesById = useCallback((
    entryId: number,
    updater: (entry: StreamEntry) => StreamEntry,
  ): void => {
    const nextEntries = entriesRef.current.map((entry) => (
      entry.id === entryId ? updater(entry) : entry
    ))
    commitEntries(nextEntries)
  }, [commitEntries])

  const updateSubTaskEntry = useCallback((
    subId: string,
    updater: (entry: SubTaskEntry) => SubTaskEntry,
  ): void => {
    const nextEntries = entriesRef.current.map((entry) => (
      entry.kind === 'subtask' && entry.subId === subId ? updater(entry) : entry
    ))
    commitEntries(nextEntries)
  }, [commitEntries])

  const appendSubTaskEntry = useCallback((entry: Omit<SubTaskEntry, 'id' | 'kind'>): number => {
    const entryId = nextId()
    const nextEntries: StreamEntry[] = [
      ...entriesRef.current,
      {
        id: entryId,
        kind: 'subtask',
        ...entry,
      },
    ]
    commitEntries(nextEntries)
    return entryId
  }, [commitEntries, nextId])

  const requestScrollToMessage = useCallback((target: PendingScrollTarget): void => {
    setPendingScrollTarget(target)
  }, [])

  const registerMessageNode = useCallback((messageId: number) => {
    return (node: HTMLDivElement | null): void => {
      if (node) {
        messageNodeRefs.current.set(messageId, node)
      } else {
        messageNodeRefs.current.delete(messageId)
      }
    }
  }, [])

  const highlightMessage = useCallback((messageId: number): void => {
    setHighlightedMessageId(messageId)
    if (highlightTimerRef.current !== null) {
      window.clearTimeout(highlightTimerRef.current)
    }
    highlightTimerRef.current = window.setTimeout(() => {
      setHighlightedMessageId((current) => (current === messageId ? null : current))
      highlightTimerRef.current = null
    }, 1800)
  }, [])

  const beginTurn = useCallback((): void => {
    clearStreamingState()
    currentTurnRef.current += 1
  }, [clearStreamingState])

  useEffect(() => {
    const client = clientRef.current
    if (!client) {
      return undefined
    }

    client.onNotify('item/agentMessage/delta', (params) => {
      const delta = String((params.text as string | undefined) ?? (params.delta as string | undefined) ?? '')
      if (!delta) {
        return
      }

      const turn = Number(params.turn ?? 0)
      if (turn !== currentTurnRef.current) {
        return
      }

      currentTurnAssistantSeenRef.current = true

      const existing = streamingRef.current
      if (existing && existing.turn === turn) {
        existing.full += delta
        const nextEntries = entriesRef.current.map((entry) => (
          entry.kind === 'message' && entry.id === existing.id
            ? { ...entry, content: existing.full }
            : entry
        ))
        commitEntries(nextEntries)
        return
      }

      const id = nextId()
      streamingRef.current = { id, turn, full: delta }
      commitEntries([
        ...entriesRef.current,
        { id, kind: 'message', role: 'assistant', content: delta, time: now() },
      ])
    })

    client.onNotify('item/toolCall', (params) => {
      const payload = params as ToolCallPayload
      const targetSessionId = String(payload.session_id ?? '')
      if (!targetSessionId || targetSessionId !== sessionIdRef.current) {
        return
      }

      const nextEntries: StreamEntry[] = [
        ...entriesRef.current,
        {
          id: nextId(),
          kind: 'tool',
          sessionId: targetSessionId,
          name: String(payload.name ?? '未知工具'),
          toolArguments: normalizeToolArguments(payload.arguments),
          status: payload.status === 'blocked' ? 'blocked' : 'running',
          reason: payload.reason ? String(payload.reason) : undefined,
          time: now(),
        } satisfies ToolEntry,
      ]
      commitEntries(nextEntries)
    })

    client.onNotify('item/toolResult', (params) => {
      const payload = params as ToolResultPayload
      const targetSessionId = String(payload.session_id ?? '')
      if (!targetSessionId || targetSessionId !== sessionIdRef.current) {
        return
      }

      const toolName = String(payload.name ?? '未知工具')
      const result: ToolResultEntry = {
        ok: Boolean(payload.ok),
        summary: String(payload.summary ?? ''),
        durationMs: Number(payload.duration_ms ?? 0),
      }

      const nextEntries = entriesRef.current.slice()
      for (let index = nextEntries.length - 1; index >= 0; index -= 1) {
        const entry = nextEntries[index]
        if (
          entry.kind === 'tool'
          && entry.sessionId === targetSessionId
          && entry.name === toolName
          && !entry.result
        ) {
          nextEntries[index] = {
            ...entry,
            result,
          }
          commitEntries(nextEntries)
          return
        }
      }

      commitEntries([
        ...entriesRef.current,
        {
          id: nextId(),
          kind: 'tool',
          sessionId: targetSessionId,
          name: toolName,
          toolArguments: {},
          status: 'running',
          result,
          time: now(),
        } satisfies ToolEntry,
      ])
    })

    client.onNotify('turn/end', (params) => {
      setRunning(false)
      streamingRef.current = null
      const error = String((params.error as string | undefined) ?? '')
      turnErrorNotifiedRef.current = Boolean(error)
      if (error) {
        appendSystemMessage(`内核错误：${error}`, 'error')
      }
      void refreshSessions()
    })

    client.onNotify('serverRequest/approval', (params) => {
      onApprovalChange(params)
    })

    return undefined
  }, [
    appendSystemMessage,
    clientRef,
    commitEntries,
    nextId,
    onApprovalChange,
    refreshSessions,
    sessionIdRef,
    setRunning,
  ])

  useEffect(() => {
    if (connectionState !== 'connected') {
      setRunning(false)
      streamingRef.current = null
    }
  }, [connectionState, setRunning])

  useEffect(() => {
    return () => {
      if (highlightTimerRef.current !== null) {
        window.clearTimeout(highlightTimerRef.current)
        highlightTimerRef.current = null
      }
    }
  }, [])

  return {
    entries,
    entriesRef,
    highlightedMessageId,
    setHighlightedMessageId,
    highlightMessage,
    pendingScrollTarget,
    messagesEndRef,
    messageNodeRefs,
    registerMessageNode,
    requestScrollToMessage,
    appendMessage,
    appendSystemMessage,
    clearEntries,
    replaceEntries,
    updateEntriesById,
    updateSubTaskEntry,
    appendSubTaskEntry,
    beginTurn,
    currentTurnRef,
    currentTurnAssistantSeenRef,
    turnErrorNotifiedRef,
  }
}
