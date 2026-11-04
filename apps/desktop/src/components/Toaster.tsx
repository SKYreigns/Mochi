import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from 'lucide-react'
import { useToastStore, type ToastTone } from '@/stores/toast'
import { cn } from '@/lib/utils'

const TONE: Record<ToastTone, { icon: typeof Info; className: string; bar: string }> = {
  info: { icon: Info, className: 'text-info', bar: 'bg-info' },
  success: { icon: CheckCircle2, className: 'text-success', bar: 'bg-success' },
  warning: { icon: AlertTriangle, className: 'text-warning', bar: 'bg-warning' },
  error: { icon: XCircle, className: 'text-danger', bar: 'bg-danger' },
}

export function Toaster() {
  const items = useToastStore((state) => state.items)
  const dismiss = useToastStore((state) => state.dismiss)
  const reduced = useReducedMotion()

  return (
    <div
      className="pointer-events-none fixed bottom-5 right-5 z-[60] flex w-[min(360px,calc(100vw-2.5rem))] flex-col gap-2"
      role="region"
      aria-label="Notifications"
    >
      <AnimatePresence initial={false}>
        {items.map((item) => {
          const tone = TONE[item.tone]
          const Icon = tone.icon
          return (
            <motion.div
              key={item.id}
              layout
              initial={{ opacity: 0, y: reduced ? 0 : 14, scale: reduced ? 1 : 0.97 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: reduced ? 0 : 8, scale: reduced ? 1 : 0.98 }}
              transition={{ duration: reduced ? 0 : 0.22, ease: [0.22, 1, 0.36, 1] }}
              className="glass-strong pointer-events-auto relative overflow-hidden rounded-2xl p-3.5 pr-10"
            >
              <span className={cn('absolute inset-y-0 left-0 w-1', tone.bar)} aria-hidden />
              <div className="flex gap-3">
                <Icon className={cn('mt-0.5 size-4 shrink-0', tone.className)} aria-hidden />
                <div className="min-w-0">
                  <p className="text-sm font-bold text-ink">{item.title}</p>
                  {item.description && (
                    <p className="mt-0.5 text-xs leading-relaxed text-ink-soft">{item.description}</p>
                  )}
                </div>
              </div>
              <button
                type="button"
                onClick={() => dismiss(item.id)}
                className="ring-focus absolute right-2 top-2 rounded-lg p-1 text-ink-faint transition-colors hover:bg-surface-muted hover:text-ink"
                aria-label="Dismiss notification"
              >
                <X className="size-3.5" aria-hidden />
              </button>
            </motion.div>
          )
        })}
      </AnimatePresence>
    </div>
  )
}
