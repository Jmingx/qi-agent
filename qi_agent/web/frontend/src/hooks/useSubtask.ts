import { useCallback, useEffect, useRef } from 'react'
import type { MutableRefObject } from 'react'
import { type ConnectionStatus, type WsClient } from '../ws'
import {
  getErrorMessage,
  now,
  stringifyValue,
  type DelegateAsyncResponse,
  type PendingScrollTarget,
  type SessionStatusResponse,
  type SubTaskEntry,
  type SubTaskProgressEntry,
} from '../appModel'

type UseSubtaskArgs = {
  clientRef: MutableRefObject<WsClient | null>
  connectionState: ConnectionStatus
  connectionStateRef: MutableRefObject<ConnectionStatus>
  sessionIdRef: MutableRefObject<string>
  appendSubTaskEntry: (entry: Omit<SubTaskEntry, 'id' | 'kind'>) => number
  updateSubTaskEntry: (subId: string, updater: (entry: SubTaskEntry) => SubTaskEntry) => void
  appendSystemMessage: (content: string, variant?: 'default' | 'error' | 'info') => void
  showToast: (message: string) => void
}

type UseSubtaskResult = {
  startDelegate: (goal: string) => Promise<void>
}

type SubTaskMeta = {
  lastFingerprint: string
  lastChangeAt: number
  entryId: number
}

type SubTaskProgressPayload = {
  session_id?: string
  sub_id?: string
  event?: string
  detail?: string
}

const SUBTASK_POLL_INTERVAL_MS = 2000
const SUBTASK_TIMEOUT_MS = 60_000

export function useSubtask({
  clientRef,
  connectionState,
  connectionStateRef,
  sessionIdRef,
  appendSubTaskEntry,
  updateSubTaskEntry,
  appendSystemMessage,
  showToast,
}: UseSubtaskArgs): UseSubtaskResult {
  const subTaskPollersRef = useRef(new Map<string, number>())
  const subTaskMetaRef = useRef(new Map<string, SubTaskMeta>())

  const clearSubTaskPoll = useCallback((subId: string): void => {
    const timerId = subTaskPollersRef.current.get(subId)
    if (timerId !== undefined) {
      window.clearTimeout(timerId)
      subTaskPollersRef.current.delete(subId)
    }
    subTaskMetaRef.current.delete(subId)
  }, [])

  const scheduleSubTaskPoll = useCallback((subId: string): void => {
    clearSubTaskPoll(subId)
    const timerId = window.setTimeout(() => {
      void pollSubTask(subId)
    }, SUBTASK_POLL_INTERVAL_MS)
    subTaskPollersRef.current.set(subId, timerId)
  }, [clearSubTaskPoll])

  const updateProgress = useCallback((subId: string, line: SubTaskProgressEntry): void => {
    updateSubTaskEntry(subId, (entry) => ({
      ...entry,
      progress: [...entry.progress, line],
      timedOut: false,
    }))
    const meta = subTaskMetaRef.current.get(subId)
    if (meta) {
      subTaskMetaRef.current.set(subId, {
        ...meta,
        lastChangeAt: Date.now(),
      })
    }
  }, [updateSubTaskEntry])

  async function pollSubTask(subId: string): Promise<void> {
    // 轮询只负责推进状态，不做额外写操作，避免定时器与渲染逻辑交叉。
    console.debug('[subtask] poll', subId)
    const client = clientRef.current
    const meta = subTaskMetaRef.current.get(subId)
    if (!meta) {
      console.debug('[subtask] meta missing, skip', subId)
      return
    }

    if (!client || connectionStateRef.current !== 'connected') {
      console.debug('[subtask] conn not ready', subId, connectionStateRef.current)
      if (Date.now() - meta.lastChangeAt >= SUBTASK_TIMEOUT_MS) {
        updateSubTaskEntry(subId, (entry) => ({
          ...entry,
          status: 'timed_out',
          timedOut: true,
          reason: entry.reason ?? '子任务轮询超时',
        }))
        showToast('子任务 60 秒无变化，已停止轮询')
        clearSubTaskPoll(subId)
        return
      }
      scheduleSubTaskPoll(subId)
      return
    }

    try {
      console.debug('[subtask] calling session/status', subId)
      const response = await client.call<SessionStatusResponse>('session/status', { session_id: subId })
      console.debug('[subtask] status resp', subId, response.status)
      const status = String(response.status ?? '').toLowerCase()
      const fingerprint = stringifyValue({
        status,
        result: response.result,
        error: response.error,
      })
      const nowMs = Date.now()
      const changed = fingerprint !== meta.lastFingerprint
      const nextMeta = changed
        ? { ...meta, lastFingerprint: fingerprint, lastChangeAt: nowMs }
        : meta
      subTaskMetaRef.current.set(subId, nextMeta)

      if (status === 'completed' || status === 'failed') {
        updateSubTaskEntry(subId, (entry) => ({
          ...entry,
          status: status === 'completed' ? 'completed' : 'failed',
          resultText: status === 'completed' ? stringifyValue(response.result) : entry.resultText,
          reason: status === 'failed'
            ? stringifyValue(response.error ?? response.result ?? '子任务执行失败')
            : entry.reason,
          timedOut: false,
        }))
        clearSubTaskPoll(subId)
        return
      }

      if (nowMs - nextMeta.lastChangeAt >= SUBTASK_TIMEOUT_MS) {
        updateSubTaskEntry(subId, (entry) => ({
          ...entry,
          status: 'timed_out',
          timedOut: true,
          reason: entry.reason ?? '子任务 60 秒无变化，已停止轮询',
        }))
        showToast('子任务 60 秒无变化，已停止轮询')
        appendSystemMessage(`子任务 ${subId.slice(0, 8)} 60 秒无变化，已停止轮询`, 'info')
        clearSubTaskPoll(subId)
        return
      }
    } catch (error) {
      console.debug('[subtask] poll error', subId, getErrorMessage(error))
      const nextMeta = subTaskMetaRef.current.get(subId)
      if (!nextMeta) {
        return
      }
      if (Date.now() - nextMeta.lastChangeAt >= SUBTASK_TIMEOUT_MS) {
        updateSubTaskEntry(subId, (entry) => ({
          ...entry,
          status: 'timed_out',
          timedOut: true,
          reason: entry.reason ?? getErrorMessage(error),
        }))
        showToast('子任务 60 秒无变化，已停止轮询')
        clearSubTaskPoll(subId)
        return
      }
    }

    scheduleSubTaskPoll(subId)
  }

  const appendProgressFromNotify = useCallback((params: SubTaskProgressPayload): void => {
    const targetSessionId = String(params.session_id ?? '')
    const subId = String(params.sub_id ?? '')
    if (!targetSessionId || targetSessionId !== sessionIdRef.current || !subId) {
      return
    }
    const detail = String(params.detail ?? '').trim()
    if (!detail) {
      return
    }

    updateProgress(subId, {
      time: now(),
      text: detail,
    })
  }, [sessionIdRef, updateProgress])

  useEffect(() => {
    const client = clientRef.current
    if (!client) {
      return undefined
    }

    client.onNotify('item/subtaskProgress', (params) => {
      appendProgressFromNotify(params as SubTaskProgressPayload)
    })

    return undefined
  }, [appendProgressFromNotify, clientRef, connectionState])

  useEffect(() => {
    return () => {
      for (const timerId of subTaskPollersRef.current.values()) {
        window.clearTimeout(timerId)
      }
      subTaskPollersRef.current.clear()
      subTaskMetaRef.current.clear()
    }
  }, [])

  const startDelegate = useCallback(async (goal: string): Promise<void> => {
    if (!sessionIdRef.current || connectionStateRef.current !== 'connected') {
      showToast('发起子任务需要先连接 WebSocket')
      return
    }
    const client = clientRef.current
    if (!client) {
      showToast('当前连接不可用')
      return
    }

    const cleanedGoal = goal.trim()
    if (!cleanedGoal) {
      showToast('请输入子任务目标')
      return
    }

    try {
      const response = await client.call<DelegateAsyncResponse>('session/delegate_async', {
        session_id: sessionIdRef.current,
        goal: cleanedGoal,
      })
      const entryId = appendSubTaskEntry({
        sessionId: sessionIdRef.current,
        subId: response.sub_id,
        goal: cleanedGoal,
        status: 'running',
        progress: [],
        resultText: undefined,
        reason: undefined,
        expanded: false,
        timedOut: false,
        startedAtMs: Date.now(),
        time: now(),
      })
      subTaskMetaRef.current.set(response.sub_id, {
        lastFingerprint: '',
        lastChangeAt: Date.now(),
        entryId,
      })
      scheduleSubTaskPoll(response.sub_id)
      showToast(`子任务已派发：${cleanedGoal}`)
    } catch (error) {
      const message = getErrorMessage(error)
      showToast(`子任务派发失败：${message}`)
      appendSystemMessage(`子任务派发失败：${message}`, 'error')
    }
  }, [
    appendSubTaskEntry,
    appendSystemMessage,
    clientRef,
    connectionStateRef,
    scheduleSubTaskPoll,
    sessionIdRef,
    showToast,
  ])

  return {
    startDelegate,
  }
}
