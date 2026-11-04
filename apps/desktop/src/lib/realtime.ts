import { eventStream, type SocketStatus } from '@/lib/ws'
import type { AppEvent } from '@/lib/types'
import { useAppStore } from '@/stores/app'
import { useChatStore } from '@/stores/chat'
import { useLiveStore } from '@/stores/live'

let started = false
let unsubscribeEvent: (() => void) | null = null
let unsubscribeStatus: (() => void) | null = null

function dispatch(event: AppEvent): void {
  // Every store handles only the events it understands. The backend remains
  // the single source of truth; nothing is fabricated in the client.
  useChatStore.getState().ingestEvent(event)
  useLiveStore.getState().ingestEvent(event)
  useAppStore.getState().ingestEvent(event)
}

function onStatus(status: SocketStatus): void {
  const app = useAppStore.getState()
  if (status === 'open') {
    if (app.backendStatus !== 'online') void app.refreshHealth()
    return
  }
  if (status === 'closed' && app.backendStatus === 'online') {
    // The HTTP health poll will confirm whether the backend truly went away.
    void app.refreshHealth()
  }
}

export function startRealtime(): void {
  if (started) return
  started = true
  unsubscribeEvent = eventStream.onEvent(dispatch)
  unsubscribeStatus = eventStream.onStatus(onStatus)
  eventStream.start()
}

export function stopRealtime(): void {
  if (!started) return
  started = false
  unsubscribeEvent?.()
  unsubscribeStatus?.()
  unsubscribeEvent = null
  unsubscribeStatus = null
  eventStream.stop()
}
