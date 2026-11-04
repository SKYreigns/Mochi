import { create } from 'zustand'
import { api, errorMessage } from '@/lib/api'
import type { AppEvent, LiveState, SystemMetrics } from '@/lib/types'
import { toast } from '@/stores/toast'

interface LiveActivityState {
  live: LiveState | null
  metrics: SystemMetrics | null
  events: AppEvent[]
  loading: boolean
  metricsLoading: boolean
  captureLoading: boolean
  error: string | null
  refresh: () => Promise<void>
  refreshMetrics: () => Promise<void>
  capture: () => Promise<void>
  ingestEvent: (event: AppEvent) => void
}

let refreshTimer: number | null = null

function scheduleRefresh(run: () => Promise<void>) {
  if (refreshTimer !== null) return
  refreshTimer = window.setTimeout(() => {
    refreshTimer = null
    void run()
  }, 400)
}

export const useLiveStore = create<LiveActivityState>()((set, get) => ({
  live: null,
  metrics: null,
  events: [],
  loading: false,
  metricsLoading: false,
  captureLoading: false,
  error: null,

  refresh: async () => {
    set({ loading: true })
    try {
      const live = await api.liveState()
      set((state) => {
        const seen = new Set(state.events.map((event) => event.seq))
        const merged = [...state.events]
        for (const event of live.events ?? []) {
          if (!seen.has(event.seq)) merged.push(event)
        }
        merged.sort((a, b) => a.seq - b.seq)
        return {
          live,
          events: merged.slice(-200),
          error: null,
        }
      })
    } catch (error) {
      set({ error: errorMessage(error) })
    } finally {
      set({ loading: false })
    }
  },

  refreshMetrics: async () => {
    set({ metricsLoading: true })
    try {
      const metrics = await api.metrics()
      set({ metrics })
    } catch {
      // Metrics may be temporarily unavailable; keep the last known values.
    } finally {
      set({ metricsLoading: false })
    }
  },

  capture: async () => {
    set({ captureLoading: true })
    try {
      await api.captureScreenshot()
      await get().refresh()
      toast.success('Screenshot captured', 'It is now visible in Live Activity.')
    } catch (error) {
      toast.error('Could not capture the screen', errorMessage(error))
    } finally {
      set({ captureLoading: false })
    }
  },

  ingestEvent: (event) => {
    set((state) => {
      if (state.events.some((item) => item.seq === event.seq)) return state
      return { events: [...state.events, event].slice(-200) }
    })
    if (
      event.type.startsWith('task.') ||
      event.type.startsWith('tool.') ||
      event.type.startsWith('approval.') ||
      event.type.startsWith('workflow.') ||
      event.type === 'agent.thinking' ||
      event.type === 'agent.idle'
    ) {
      scheduleRefresh(get().refresh)
    }
  },
}))
