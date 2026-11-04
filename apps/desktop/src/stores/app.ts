import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { api, errorMessage } from '@/lib/api'
import type { Approval, AppEvent, Health, SettingsSnapshot } from '@/lib/types'
import { toast } from '@/stores/toast'

export type BackendStatus = 'connecting' | 'online' | 'offline'
export type ThemePreference = 'light' | 'dark' | 'system'
export type MotionPreference = 'full' | 'reduced' | 'system'
export type AccentPreference = 'lilac' | 'seafoam' | 'blush' | 'honey'

interface Preferences {
  theme: ThemePreference
  accent: AccentPreference
  motion: MotionPreference
  sidebarCollapsed: boolean
}

interface AppState extends Preferences {
  health: Health | null
  settings: SettingsSnapshot | null
  backendStatus: BackendStatus
  backendError: string | null
  approvals: Approval[]
  approvalsLoading: boolean
  stoppedLoading: boolean
  initialized: boolean

  initialize: () => Promise<void>
  refreshHealth: () => Promise<void>
  refreshSettings: () => Promise<void>
  refreshApprovals: () => Promise<void>
  setTheme: (theme: ThemePreference) => void
  setAccent: (accent: AccentPreference) => void
  setMotion: (motion: MotionPreference) => void
  toggleSidebar: () => void
  decideApproval: (id: string, approved: boolean, note?: string) => Promise<boolean>
  stopEverything: () => Promise<void>
  resumeEverything: () => Promise<void>
  ingestEvent: (event: AppEvent) => void
}

const systemPrefersDark = () =>
  typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches
const systemPrefersReduced = () =>
  typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

export function applyPreferences(state: Pick<AppState, 'theme' | 'accent' | 'motion'>) {
  if (typeof document === 'undefined') return
  const root = document.documentElement
  const dark = state.theme === 'dark' || (state.theme === 'system' && systemPrefersDark())
  root.classList.toggle('dark', dark)
  root.dataset.accent = state.accent
  const reduced = state.motion === 'reduced' || (state.motion === 'system' && systemPrefersReduced())
  root.dataset.motion = reduced ? 'reduce' : 'full'
}

let healthTimer: number | null = null

export const useAppStore = create<AppState>()(
  persist(
    (set, get) => ({
      theme: 'light',
      accent: 'lilac',
      motion: 'system',
      sidebarCollapsed: false,

      health: null,
      settings: null,
      backendStatus: 'connecting',
      backendError: null,
      approvals: [],
      approvalsLoading: false,
      stoppedLoading: false,
      initialized: false,

      initialize: async () => {
        if (get().initialized) return
        set({ initialized: true })
        applyPreferences(get())

        if (typeof window !== 'undefined') {
          const themeQuery = window.matchMedia('(prefers-color-scheme: dark)')
          const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
          const reapply = () => applyPreferences(get())
          themeQuery.addEventListener?.('change', reapply)
          motionQuery.addEventListener?.('change', reapply)
        }

        await Promise.all([get().refreshHealth(), get().refreshSettings(), get().refreshApprovals()])

        if (healthTimer === null) {
          healthTimer = window.setInterval(() => {
            if (document.visibilityState === 'visible') void get().refreshHealth()
          }, 12_000)
        }

        const onVisible = () => {
          if (document.visibilityState === 'visible') {
            void get().refreshHealth()
            void get().refreshApprovals()
          }
        }
        document.addEventListener('visibilitychange', onVisible)
      },

      refreshHealth: async () => {
        try {
          const health = await api.health()
          set({ health, backendStatus: 'online', backendError: null })
        } catch (error) {
          set({
            backendStatus: 'offline',
            backendError: errorMessage(error),
          })
        }
      },

      refreshSettings: async () => {
        try {
          const settings = await api.settings()
          set({ settings })
        } catch {
          // Settings remain null; the UI shows an honest unavailable state.
        }
      },

      refreshApprovals: async () => {
        set({ approvalsLoading: true })
        try {
          const response = await api.approvals('pending')
          set({ approvals: response.approvals })
        } catch {
          set({ approvals: [] })
        } finally {
          set({ approvalsLoading: false })
        }
      },

      setTheme: (theme) => {
        set({ theme })
        applyPreferences({ ...get(), theme })
      },
      setAccent: (accent) => {
        set({ accent })
        applyPreferences({ ...get(), accent })
      },
      setMotion: (motion) => {
        set({ motion })
        applyPreferences({ ...get(), motion })
      },
      toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),

      decideApproval: async (id, approved, note = '') => {
        try {
          await api.decideApproval(id, approved, note)
          set((state) => ({
            approvals: state.approvals.filter((approval) => approval.id !== id),
          }))
          toast.success(
            approved ? 'Approved — Mochi is continuing' : 'Rejected — Mochi will stop that action',
            approved ? 'The action will run with the same permissions as the request.' : undefined,
          )
          return true
        } catch (error) {
          toast.error('Could not resolve the approval', errorMessage(error))
          return false
        }
      },

      stopEverything: async () => {
        set({ stoppedLoading: true })
        try {
          const result = await api.emergencyStop()
          set((state) => ({
            health: state.health
              ? {
                  ...state.health,
                  services: { ...state.health.services, emergency_stopped: true },
                }
              : state.health,
          }))
          toast.warning('Everything is paused', `${result.cancelled_tasks} active task(s) were cancelled.`)
        } catch (error) {
          toast.error('Could not stop Mochi', errorMessage(error))
        } finally {
          set({ stoppedLoading: false })
        }
      },

      resumeEverything: async () => {
        set({ stoppedLoading: true })
        try {
          await api.emergencyResume()
          set((state) => ({
            health: state.health
              ? {
                  ...state.health,
                  services: { ...state.health.services, emergency_stopped: false },
                }
              : state.health,
          }))
          toast.success('Mochi is ready again')
        } catch (error) {
          toast.error('Could not resume Mochi', errorMessage(error))
        } finally {
          set({ stoppedLoading: false })
        }
      },

      ingestEvent: (event) => {
        if (event.type === 'approval.requested') {
          void get().refreshApprovals()
          toast.info('Mochi needs your approval', 'An action is waiting for you to review it.')
        }
        if (event.type === 'approval.resolved') {
          void get().refreshApprovals()
        }
        if (event.type === 'settings.updated') {
          void get().refreshSettings()
          void get().refreshHealth()
        }
        if (
          event.type === 'task.completed' ||
          event.type === 'task.failed' ||
          event.type === 'task.cancelled' ||
          event.type === 'task.queued' ||
          event.type === 'task.status'
        ) {
          void get().refreshHealth()
        }
      },
    }),
    {
      name: 'mochi-preferences',
      partialize: (state) => ({
        theme: state.theme,
        accent: state.accent,
        motion: state.motion,
        sidebarCollapsed: state.sidebarCollapsed,
      }),
      onRehydrateStorage: () => (state) => {
        if (state) applyPreferences(state)
      },
    },
  ),
)
