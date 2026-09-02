import { useCallback, useEffect, useRef } from "react"
import type { MutableRefObject } from "react"
import { type ConnectionStatus, type WsClient } from "../ws"
import {
  getErrorMessage,
  now,
  stringifyValue,
  type DelegateAsyncResponse,
  type SessionStatusResponse,
  type StreamEntry,
  type SubTaskEntry,
  type SubTaskProgressEntry,
} from "../appModel"

type UseSubtaskArgs = {
  clientRef: MutableRefObject<WsClient | null>
  connectionState: ConnectionStatus
  connectionStateRef: MutableRefObject<ConnectionStatus>
  sessionIdRef: MutableRefObject<string>
  entriesRef: MutableRefObject<StreamEntry[]>
  appendSubTaskEntry: (entry: Omit<SubTaskEntry, "id" | "kind">) => number
  updateSubTaskEntry: (subId: string, updater: (entry: SubTaskEntry) => SubTaskEntry) => void
  appendSystemMessage: (content: string, variant?: "default" | "error" | "info") => void
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

type TerminalStatus = "completed" | "failed" | "need_more_info" | "stopped"

const SUBTASK_POLL_INTERVAL_MS = 2000
const SUBTASK_TIMEOUT_MS = 60_000
const SUBTASK_PROGRESS_LIMIT = 50

function isSubTaskEntry(entry: StreamEntry, subId: string): entry is SubTaskEntry {
  return entry.kind === "subtask" && entry.subId === subId
}

function readText(value: unknown): string {
  if (typeof value === "string") {
    return value.trim()
  }
  if (value === null || value === undefined) {
    return ""
  }
  return stringifyValue(value).trim()
}

function pickText(source: unknown, keys: string[]): string {
  if (!source || typeof source !== "object" || Array.isArray(source)) {
    return ""
  }

  const record = source as Record<string, unknown>
  for (const key of keys) {
    const text = readText(record[key])
    if (text) {
      return text
    }
  }
  return ""
}

function formatTerminalResult(
  status: TerminalStatus,
  result: unknown,
  error: unknown,
): { resultText: string; reason: string; expanded: boolean } {
  const resultText = stringifyValue(result)
  const resultRecord = result && typeof result === "object" && !Array.isArray(result)
    ? result as Record<string, unknown>
    : null
  const errorRecord = error && typeof error === "object" && !Array.isArray(error)
    ? error as Record<string, unknown>
    : null

  if (status === "completed") {
    return {
      resultText,
      reason: "",
      expanded: true,
    }
  }

  if (status === "need_more_info") {
    const summary = pickText(resultRecord, ["summary"])
    const question = pickText(resultRecord, ["question"])
    return {
      resultText: [summary, question].filter(Boolean).join("\n") || resultText,
      reason: question || summary || "需要补充信息",
      expanded: false,
    }
  }

  if (status === "stopped") {
    const summary = pickText(resultRecord, ["summary"])
    const reason = pickText(resultRecord, ["reason", "error"])
      || pickText(errorRecord, ["message"])
      || readText(error)
      || "子任务已停止"
    return {
      resultText: [summary, reason].filter(Boolean).join("\n") || resultText,
      reason,
      expanded: false,
    }
  }

  const summary = pickText(resultRecord, ["summary"])
  const reason = pickText(resultRecord, ["error", "reason"])
    || pickText(errorRecord, ["message"])
    || readText(error)
    || "子任务执行失败"

  return {
    resultText: [summary, reason].filter(Boolean).join("\n") || resultText,
    reason,
    expanded: false,
  }
}

export function useSubtask({
  clientRef,
  connectionState,
  connectionStateRef,
  sessionIdRef,
  entriesRef,
  appendSubTaskEntry,
  updateSubTaskEntry,
  appendSystemMessage,
  showToast,
}: UseSubtaskArgs): UseSubtaskResult {
  const subTaskPollersRef = useRef(new Map<string, number>())
  const subTaskMetaRef = useRef(new Map<string, SubTaskMeta>())

  const clearSubTaskTimer = useCallback((subId: string): void => {
    const timerId = subTaskPollersRef.current.get(subId)
    if (timerId !== undefined) {
      window.clearTimeout(timerId)
      subTaskPollersRef.current.delete(subId)
    }
  }, [])

  const clearSubTaskPoll = useCallback((subId: string): void => {
    clearSubTaskTimer(subId)
    subTaskMetaRef.current.delete(subId)
  }, [clearSubTaskTimer])

  const updateProgress = useCallback((subId: string, line: SubTaskProgressEntry): void => {
    updateSubTaskEntry(subId, (entry) => ({
      ...entry,
      progress: [...entry.progress, line].slice(-SUBTASK_PROGRESS_LIMIT),
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

  const restoreMetaFromEntry = useCallback((subId: string): SubTaskMeta | null => {
    const entry = entriesRef.current.find((candidate) => isSubTaskEntry(candidate, subId))
    if (!entry || entry.status !== "running") {
      return null
    }

    const meta = {
      lastFingerprint: stringifyValue({
        status: entry.status,
        progress: entry.progress.slice(-SUBTASK_PROGRESS_LIMIT),
        resultText: entry.resultText,
        reason: entry.reason,
        timedOut: entry.timedOut,
      }),
      lastChangeAt: Date.now(),
      entryId: entry.id,
    }
    subTaskMetaRef.current.set(subId, meta)
    return meta
  }, [entriesRef])

  const scheduleSubTaskPoll = useCallback((subId: string): void => {
    clearSubTaskTimer(subId)
    const timerId = window.setTimeout(() => {
      void pollSubTask(subId)
    }, SUBTASK_POLL_INTERVAL_MS)
    subTaskPollersRef.current.set(subId, timerId)
  }, [clearSubTaskTimer])

  async function pollSubTask(subId: string): Promise<void> {
    const client = clientRef.current
    let meta = subTaskMetaRef.current.get(subId)
    if (!meta) {
      meta = restoreMetaFromEntry(subId)
      if (!meta) {
        clearSubTaskPoll(subId)
        return
      }
    }

    if (!client || connectionStateRef.current !== "connected") {
      if (Date.now() - meta.lastChangeAt >= SUBTASK_TIMEOUT_MS) {
        updateSubTaskEntry(subId, (entry) => ({
          ...entry,
          status: "timed_out",
          timedOut: true,
          reason: entry.reason ?? "子任务轮询超时",
        }))
        showToast("子任务 60 秒无变化，已停止轮询")
        clearSubTaskPoll(subId)
        return
      }
      scheduleSubTaskPoll(subId)
      return
    }

    try {
      const response = await client.call<SessionStatusResponse>("session/status", {
        session_id: subId,
      })
      // 顶层 status 是 context.status（线程结束态，子 agent 正常结束即 completed）；
      // 真实任务语义终态（need_more_info/stopped 等）在 result.status 里——
      // 若存在且合法，以 result.status 为准，否则回退顶层（兜底）。
      const resultStatus = (response.result as { status?: unknown } | null)?.status
      const rawStatus = typeof resultStatus === "string" && [
        "completed",
        "failed",
        "need_more_info",
        "stopped",
      ].includes(resultStatus.trim().toLowerCase())
        ? resultStatus.trim()
        : String(response.status ?? "")
      const status = rawStatus.toLowerCase()
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

      if (
        status === "completed"
        || status === "failed"
        || status === "need_more_info"
        || status === "stopped"
      ) {
        const terminal = formatTerminalResult(
          status as TerminalStatus,
          response.result,
          response.error,
        )
        updateSubTaskEntry(subId, (entry) => ({
          ...entry,
          status: status as SubTaskEntry["status"],
          resultText: terminal.resultText,
          reason: terminal.reason || entry.reason,
          expanded: status === "completed" ? true : entry.expanded,
          timedOut: false,
        }))
        clearSubTaskPoll(subId)
        return
      }

      if (nowMs - nextMeta.lastChangeAt >= SUBTASK_TIMEOUT_MS) {
        updateSubTaskEntry(subId, (entry) => ({
          ...entry,
          status: "timed_out",
          timedOut: true,
          reason: entry.reason ?? "子任务 60 秒无变化，已停止轮询",
        }))
        showToast("子任务 60 秒无变化，已停止轮询")
        appendSystemMessage(`子任务 ${subId.slice(0, 8)} 60 秒无变化，已停止轮询`, "info")
        clearSubTaskPoll(subId)
        return
      }
    } catch (error) {
      const nextMeta = subTaskMetaRef.current.get(subId) || restoreMetaFromEntry(subId)
      if (!nextMeta) {
        clearSubTaskPoll(subId)
        return
      }
      if (Date.now() - nextMeta.lastChangeAt >= SUBTASK_TIMEOUT_MS) {
        updateSubTaskEntry(subId, (entry) => ({
          ...entry,
          status: "timed_out",
          timedOut: true,
          reason: entry.reason ?? getErrorMessage(error),
        }))
        showToast("子任务 60 秒无变化，已停止轮询")
        clearSubTaskPoll(subId)
        return
      }
    }

    scheduleSubTaskPoll(subId)
  }

  const appendProgressFromNotify = useCallback((params: SubTaskProgressPayload): void => {
    const targetSessionId = String(params.session_id ?? "")
    const subId = String(params.sub_id ?? "")
    if (!targetSessionId || targetSessionId !== sessionIdRef.current || !subId) {
      return
    }
    const detail = String(params.detail ?? "").trim()
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

    client.onNotify("item/subtaskProgress", (params) => {
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
    if (!sessionIdRef.current || connectionStateRef.current !== "connected") {
      showToast("发起子任务前需要先连接 WebSocket")
      return
    }

    const client = clientRef.current
    if (!client) {
      showToast("当前连接不可用")
      return
    }

    const cleanedGoal = goal.trim()
    if (!cleanedGoal) {
      showToast("请输入子任务目标")
      return
    }

    try {
      const response = await client.call<DelegateAsyncResponse>("session/delegate_async", {
        session_id: sessionIdRef.current,
        goal: cleanedGoal,
      })
      const entryId = appendSubTaskEntry({
        sessionId: sessionIdRef.current,
        subId: response.sub_id,
        goal: cleanedGoal,
        status: "running",
        progress: [],
        resultText: undefined,
        reason: undefined,
        expanded: false,
        timedOut: false,
        startedAtMs: Date.now(),
        time: now(),
      })
      subTaskMetaRef.current.set(response.sub_id, {
        lastFingerprint: "",
        lastChangeAt: Date.now(),
        entryId,
      })
      scheduleSubTaskPoll(response.sub_id)
      showToast(`子任务已派发：${cleanedGoal}`)
    } catch (error) {
      const message = getErrorMessage(error)
      showToast(`子任务派发失败：${message}`)
      appendSystemMessage(`子任务派发失败：${message}`, "error")
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
