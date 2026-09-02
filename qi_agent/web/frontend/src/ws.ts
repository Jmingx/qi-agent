import { WS_PROTOCOL_VERSION } from './appModel'

export type RpcResponse = {
  id: number | string | null
  result?: unknown
  error?: { code: number; message: string }
}

export type ConnectionStatus = 'connected' | 'reconnecting' | 'disconnected'

type PendingCall = {
  resolve: (resp: RpcResponse) => void
  reject: (error: Error) => void
  timeoutId: number
}

type NotifyHandler = (params: Record<string, unknown>) => void
type StatusHandler = (status: ConnectionStatus) => void

const INITIAL_RECONNECT_DELAY = 1000
const MAX_RECONNECT_DELAY = 30_000
const CALL_TIMEOUT_MS = 30_000

export class WsClient {
  private ws: WebSocket | null = null
  private nextId = 1
  private pending = new Map<number | string, PendingCall>()
  private handlers = new Map<string, NotifyHandler>()
  private statusHandlers = new Set<StatusHandler>()
  private reconnectTimer: number | null = null
  private reconnectDelayMs = INITIAL_RECONNECT_DELAY
  private openPromise: Promise<void> | null = null
  private openResolve: (() => void) | null = null
  private openReject: ((error: Error) => void) | null = null
  private generation = 0
  private disposed = false
  private shouldReconnect = true
  private status: ConnectionStatus = 'disconnected'

  constructor(
    private readonly url = '/ws',
    private readonly tokenProvider: (() => string | null) | null = null,
  ) {}

  connect(): Promise<void> {
    if (this.disposed) {
      return Promise.reject(new Error('WebSocket client released'))
    }
    this.shouldReconnect = true
    if (!this.openPromise) {
      this.openPromise = new Promise<void>((resolve, reject) => {
        this.openResolve = resolve
        this.openReject = reject
      })
    }
    if (!this.ws || this.ws.readyState === WebSocket.CLOSED) {
      this.openSocket()
    } else if (this.ws.readyState === WebSocket.CONNECTING) {
      this.emitStatus('reconnecting')
    } else if (this.ws.readyState === WebSocket.OPEN) {
      this.emitStatus('connected')
      this.resolveOpenPromise()
    }
    return this.openPromise
  }

  dispose(): void {
    this.disposed = true
    this.shouldReconnect = false
    this.clearReconnectTimer()
    this.rejectOpenPromise(new Error('WebSocket closed'))
    this.rejectPending(new Error('WebSocket closed'))
    if (this.ws) {
      this.generation += 1
      this.ws.close()
      this.ws = null
    }
    this.emitStatus('disconnected')
  }

  onNotify(method: string, fn: NotifyHandler): void {
    this.handlers.set(method, fn)
  }

  onStatus(fn: StatusHandler): void {
    this.statusHandlers.add(fn)
    fn(this.status)
  }

  call<T = unknown>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error('WebSocket not connected'))
    }
    return new Promise<T>((resolve, reject) => {
      const id = this.nextId++
      const timeoutId = window.setTimeout(() => {
        const pending = this.pending.get(id)
        if (!pending) {
          return
        }
        this.pending.delete(id)
        // RPC request cannot hang forever, or session restore loading will never clear.
        pending.reject(new Error(`RPC call ${method} timed out after 30s`))
      }, CALL_TIMEOUT_MS)

      this.pending.set(id, {
        timeoutId,
        resolve: (resp) => {
          if (resp.error) {
            reject(new Error(resp.error.message))
            return
          }
          resolve(resp.result as T)
        },
        reject,
      })

      this.ws?.send(JSON.stringify({ jsonrpc: '2.0', id, method, params, v: WS_PROTOCOL_VERSION }))
    })
  }

  notify(method: string, params: Record<string, unknown> = {}): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      return
    }
    this.ws.send(JSON.stringify({ jsonrpc: '2.0', method, params, v: WS_PROTOCOL_VERSION }))
  }

  private openSocket(): void {
    if (this.disposed) {
      return
    }
    this.clearReconnectTimer()
    this.emitStatus('reconnecting')
    const socket = new WebSocket(this.buildUrl())
    const currentGeneration = ++this.generation
    this.ws = socket

    socket.onopen = () => {
      if (currentGeneration !== this.generation || this.disposed) {
        return
      }
      this.reconnectDelayMs = INITIAL_RECONNECT_DELAY
      this.emitStatus('connected')
      this.resolveOpenPromise()
    }

    socket.onmessage = (event) => {
      if (currentGeneration !== this.generation) {
        return
      }
      this.onMessage(String(event.data))
    }

    socket.onerror = () => {
      // 鏉╃偞甯撮柨娆掝嚖缂佺喍绔存禍銈囩舶 onclose 婢跺嫮鎮婇敍宀勪缉閸忓秹鍣告径宥嗗絹缁€鍝勬嫲闁插秴顦查柌宥堢箾閵?
    }

    socket.onclose = (event) => {
      if (currentGeneration !== this.generation) {
        return
      }
      this.ws = null
      if (event.code === 4401 || event.code === 4403) {
        this.shouldReconnect = false
        this.rejectOpenPromise(new Error(event.reason || 'WebSocket auth failed'))
        this.rejectPending(new Error(event.reason || 'WebSocket auth failed'))
        this.emitStatus('disconnected')
        return
      }
      this.rejectPending(new Error('WebSocket connection closed'))
      if (this.disposed || !this.shouldReconnect) {
        this.emitStatus('disconnected')
        return
      }
      this.emitStatus('reconnecting')
      this.scheduleReconnect()
    }
  }

  private scheduleReconnect(): void {
    this.clearReconnectTimer()
    const delay = this.reconnectDelayMs
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null
      if (!this.disposed && this.shouldReconnect) {
        this.openSocket()
      }
    }, delay)
    this.reconnectDelayMs = Math.min(this.reconnectDelayMs * 2, MAX_RECONNECT_DELAY)
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
  }

  private rejectPending(error: Error): void {
    for (const [, pending] of this.pending) {
      window.clearTimeout(pending.timeoutId)
      pending.reject(error)
    }
    this.pending.clear()
  }

  private resolveOpenPromise(): void {
    if (this.openResolve) {
      this.openResolve()
      this.openResolve = null
      this.openReject = null
    }
  }

  private rejectOpenPromise(error: Error): void {
    if (this.openReject) {
      const reject = this.openReject
      this.openResolve = null
      this.openReject = null
      this.openPromise = null
      reject(error)
    }
  }

  private buildUrl(): string {
    const url = new URL(this.url, window.location.href)
    const token = this.tokenProvider?.()?.trim() ?? ''
    if (token) {
      url.searchParams.set('token', token)
    } else {
      url.searchParams.delete('token')
    }
    return url.toString()
  }

  private emitStatus(status: ConnectionStatus): void {
    this.status = status
    for (const handler of this.statusHandlers) {
      handler(status)
    }
  }

  private onMessage(raw: string): void {
    let data: Record<string, unknown>
    try {
      data = JSON.parse(raw) as Record<string, unknown>
    } catch {
      return
    }

    const protocolVersion = typeof data.v === 'number' ? data.v : WS_PROTOCOL_VERSION
    if (protocolVersion !== WS_PROTOCOL_VERSION) {
      return
    }

    if (data.id !== undefined && data.id !== null) {
      const cb = this.pending.get(data.id as number | string)
      if (!cb) {
        return
      }
      this.pending.delete(data.id as number | string)
      window.clearTimeout(cb.timeoutId)
      cb.resolve(data as RpcResponse)
      return
    }

    const method = typeof data.method === 'string' ? data.method : ''
    if (!method) {
      return
    }
    const handler = this.handlers.get(method)
    if (handler) {
      handler((data.params as Record<string, unknown>) || {})
    }
  }
}
