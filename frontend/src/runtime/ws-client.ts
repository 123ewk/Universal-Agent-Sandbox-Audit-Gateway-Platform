/**
 * WebSocket 客户端 — 连接管理 + 自动重连
 *
 * 重连策略：指数退避 1s → 2s → 4s → max 30s
 * 重连成功后：通过 REST GET /api/v1/tasks/{sessionId} 补偿丢失的状态
 *
 * 生命周期：
 *   connect(sessionId) → onMessage 回调接收事件 → disconnect()
 *
 * 使用方式：
 *   const client = new WSClient()
 *   client.onMessage((msg) => { eventBus.dispatch(msg) })
 *   client.connect(sessionId)
 */
import type { WSMessage } from './event-types'

const INITIAL_RECONNECT_DELAY = 1000
const MAX_RECONNECT_DELAY = 30000
const BACKOFF_MULTIPLIER = 2

export type MessageCallback = (msg: WSMessage) => void
export type StatusCallback = (connected: boolean) => void

export class WSClient {
  private ws: WebSocket | null = null
  private sessionId: number | null = null
  private reconnectDelay = INITIAL_RECONNECT_DELAY
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private shouldReconnect = false
  private _onMessage: MessageCallback | null = null
  private _onStatusChange: StatusCallback | null = null

  // ================================================================
  // Public API
  // ================================================================

  onMessage(cb: MessageCallback): void {
    this._onMessage = cb
  }

  onStatusChange(cb: StatusCallback): void {
    this._onStatusChange = cb
  }

  connect(sessionId: number): void {
    this.sessionId = sessionId
    this.shouldReconnect = true
    this.reconnectDelay = INITIAL_RECONNECT_DELAY
    this._openConnection()
  }

  disconnect(): void {
    this.shouldReconnect = false
    this._clearReconnectTimer()
    if (this.ws) {
      this.ws.close(1000, 'client disconnect')
      this.ws = null
    }
    this._notifyStatus(false)
  }

  get connected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN
  }

  get sessionIdValue(): number | null {
    return this.sessionId
  }

  // ================================================================
  // Internal
  // ================================================================

  private _openConnection(): void {
    if (this.sessionId === null) return

    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const host = window.location.host
    const url = `${protocol}://${host}/api/v1/ws/sessions/${this.sessionId}`

    try {
      this.ws = new WebSocket(url)
    } catch {
      this._scheduleReconnect()
      return
    }

    this.ws.onopen = () => {
      this.reconnectDelay = INITIAL_RECONNECT_DELAY
      this._notifyStatus(true)
    }

    this.ws.onmessage = (event: MessageEvent) => {
      try {
        const msg: WSMessage = JSON.parse(event.data as string)
        if (this._onMessage) {
          this._onMessage(msg)
        }
      } catch {
        // ignore malformed messages
      }
    }

    this.ws.onclose = (event: CloseEvent) => {
      this._notifyStatus(false)
      // 1000 = normal close, don't reconnect
      if (event.code !== 1000 && this.shouldReconnect) {
        this._scheduleReconnect()
      }
    }

    this.ws.onerror = () => {
      // onclose will fire after onerror, reconnect is handled there
    }
  }

  private _scheduleReconnect(): void {
    if (!this.shouldReconnect) return
    this._clearReconnectTimer()

    this.reconnectTimer = setTimeout(() => {
      this._openConnection()
      this.reconnectDelay = Math.min(
        this.reconnectDelay * BACKOFF_MULTIPLIER,
        MAX_RECONNECT_DELAY,
      )
    }, this.reconnectDelay)
  }

  private _clearReconnectTimer(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
  }

  private _notifyStatus(connected: boolean): void {
    if (this._onStatusChange) {
      this._onStatusChange(connected)
    }
  }
}
