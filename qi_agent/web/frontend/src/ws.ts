export type RpcResponse = {
  id: number | string | null
  result?: unknown
  error?: { code: number; message: string }
}

export type ConnectionStatus = 'connected' | 'reconnecting' | 'disconnected'

type PendingCall = {
  resolve: (resp: RpcResponse) => void
  reject: (error: Error) => void
}

type NotifyHandler = (params: Record<string, unknown>) => void
type StatusHandler = (status: ConnectionStatus) => void

const INITIAL_RECONNECT_DELAY = 1000
const MAX_RECONNECT_DELAY = 30_000

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
      return Promise.reject(new Error('WebSocket 客户端已释放'))
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
    this.rejectOpenPromise(new Error('WebSocket 已断开'))
    this.rejectPending(new Error('WebSocket 已断开'))
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
      return Promise.reject(new Error('WebSocket 未连接'))
    }
    return new Promise<T>((resolve, reject) => {
      const id = this.nextId++
      this.pending.set(id, {
        resolve: (resp) => {
          if (resp.error) {
            reject(new Error(resp.error.message))
            return
          }
          resolve(resp.result as T)
        },
        reject,
      })
      this.ws?.send(JSON.stringify({ jsonrpc: '2.0', id, method, params }))
    })
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
      // 连接错误会在 onclose 里统一转成重连流程，避免重复提示。
    }

    socket.onclose = (event) => {
      if (currentGeneration !== this.generation) {
        return
      }
      this.ws = null
      if (event.code === 4401 || event.code === 4403) {
        this.shouldReconnect = false
        this.rejectOpenPromise(new Error(event.reason || 'WebSocket 鉴权失败'))
        this.rejectPending(new Error(event.reason || 'WebSocket 鉴权失败'))
        this.emitStatus('disconnected')
        return
      }
      this.rejectPending(new Error('WebSocket 连接已断开'))
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

    if (data.id !== undefined && data.id !== null) {
      const cb = this.pending.get(data.id as number | string)
      if (!cb) {
        return
      }
      this.pending.delete(data.id as number | string)
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
