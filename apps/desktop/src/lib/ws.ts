import { WS_URL } from '@/lib/api'
import type { AppEvent } from '@/lib/types'

export type SocketStatus = 'connecting' | 'open' | 'closed'

type EventListener = (event: AppEvent) => void
type StatusListener = (status: SocketStatus) => void

class EventStream {
  private socket: WebSocket | null = null
  private listeners = new Set<EventListener>()
  private statusListeners = new Set<StatusListener>()
  private reconnectTimer: number | null = null
  private attempts = 0
  private running = false
  private _status: SocketStatus = 'closed'

  get status(): SocketStatus {
    return this._status
  }

  start(): void {
    if (this.running) return
    this.running = true
    this.open()
  }

  stop(): void {
    this.running = false
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.socket?.close()
    this.socket = null
    this.setStatus('closed')
  }

  onEvent(listener: EventListener): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  onStatus(listener: StatusListener): () => void {
    this.statusListeners.add(listener)
    listener(this._status)
    return () => this.statusListeners.delete(listener)
  }

  private setStatus(status: SocketStatus) {
    if (this._status === status) return
    this._status = status
    for (const listener of this.statusListeners) listener(status)
  }

  private scheduleReconnect() {
    if (!this.running || this.reconnectTimer !== null) return
    const delay = Math.min(15_000, 750 * 2 ** Math.min(this.attempts, 4))
    this.attempts += 1
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null
      this.open()
    }, delay)
  }

  private open() {
    if (!this.running || this.socket) return
    if (typeof WebSocket === 'undefined') return

    this.setStatus('connecting')
    let socket: WebSocket
    try {
      socket = new WebSocket(WS_URL)
    } catch {
      this.setStatus('closed')
      this.scheduleReconnect()
      return
    }
    this.socket = socket

    socket.onopen = () => {
      this.attempts = 0
      this.setStatus('open')
    }

    socket.onmessage = (message) => {
      if (typeof message.data !== 'string') return
      let parsed: unknown
      try {
        parsed = JSON.parse(message.data)
      } catch {
        return
      }
      if (!parsed || typeof parsed !== 'object') return
      const record = parsed as Record<string, unknown>
      if (record.type === 'hello') return
      if (record.type === 'ping') return
      if (
        typeof record.seq !== 'number' ||
        typeof record.type !== 'string' ||
        typeof record.ts !== 'string'
      ) {
        return
      }
      const event: AppEvent = {
        seq: record.seq,
        type: record.type,
        ts: record.ts,
        payload: (record.payload as Record<string, unknown>) ?? {},
      }
      for (const listener of this.listeners) {
        try {
          listener(event)
        } catch {
          // A faulty subscriber must never break the stream.
        }
      }
    }

    socket.onerror = () => {
      socket.close()
    }

    socket.onclose = () => {
      if (this.socket === socket) this.socket = null
      this.setStatus('closed')
      this.scheduleReconnect()
    }
  }
}

export const eventStream = new EventStream()
