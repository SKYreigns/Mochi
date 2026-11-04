import { create } from 'zustand'

export type ToastTone = 'info' | 'success' | 'warning' | 'error'

export interface ToastItem {
  id: string
  title: string
  description?: string
  tone: ToastTone
}

interface ToastState {
  items: ToastItem[]
  push: (toast: Omit<ToastItem, 'id'>) => string
  dismiss: (id: string) => void
}

let counter = 0

export const useToastStore = create<ToastState>((set, get) => ({
  items: [],
  push: (toast) => {
    counter += 1
    const id = `toast-${counter}`
    set((state) => ({ items: [...state.items.slice(-3), { ...toast, id }] }))
    window.setTimeout(() => {
      if (get().items.some((item) => item.id === id)) {
        set((state) => ({ items: state.items.filter((item) => item.id !== id) }))
      }
    }, toast.tone === 'error' ? 7000 : 4500)
    return id
  },
  dismiss: (id) => set((state) => ({ items: state.items.filter((item) => item.id !== id) })),
}))

function emit(tone: ToastTone, title: string, description?: string) {
  return useToastStore.getState().push({ tone, title, description })
}

export const toast = {
  info: (title: string, description?: string) => emit('info', title, description),
  success: (title: string, description?: string) => emit('success', title, description),
  warning: (title: string, description?: string) => emit('warning', title, description),
  error: (title: string, description?: string) => emit('error', title, description),
}
