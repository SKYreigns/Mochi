import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import { AlertTriangle, CloudOff, RefreshCw } from 'lucide-react'
import { Sidebar } from '@/components/Sidebar'
import { Button } from '@/components/ui'
import { useAppStore } from '@/stores/app'

export function AppShell({ children }: { children: ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false)
  const location = useLocation()
  const scrollRef = useRef<HTMLDivElement>(null)

  const backendStatus = useAppStore((state) => state.backendStatus)
  const backendError = useAppStore((state) => state.backendError)
  const health = useAppStore((state) => state.health)
  const stoppedLoading = useAppStore((state) => state.stoppedLoading)
  const resumeEverything = useAppStore((state) => state.resumeEverything)
  const refreshHealth = useAppStore((state) => state.refreshHealth)

  const emergency = health?.services.emergency_stopped ?? false

  useEffect(() => {
    setMobileOpen(false)
    scrollRef.current?.scrollTo({ top: 0, behavior: 'instant' as ScrollBehavior })
  }, [location.pathname])

  return (
    <div className="relative flex h-full overflow-hidden">
      <Sidebar mobileOpen={mobileOpen} onClose={() => setMobileOpen(false)} />

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-surface/50 px-4 backdrop-blur-xl lg:hidden">
          <Button
            variant="outline"
            size="icon"
            onClick={() => setMobileOpen(true)}
            aria-label="Open navigation"
          >
            <span className="flex flex-col gap-1" aria-hidden>
              <span className="h-0.5 w-4 rounded-full bg-current" />
              <span className="h-0.5 w-4 rounded-full bg-current" />
              <span className="h-0.5 w-2.5 rounded-full bg-current" />
            </span>
          </Button>
          <p className="text-sm font-extrabold text-ink">Mochi</p>
          <span className="ml-auto inline-flex items-center gap-1.5 text-[11px] font-bold text-ink-faint">
            <span
              className={
                backendStatus === 'online'
                  ? 'size-2 rounded-full bg-success'
                  : backendStatus === 'connecting'
                    ? 'size-2 rounded-full bg-warning'
                    : 'size-2 rounded-full bg-danger'
              }
              aria-hidden
            />
            {backendStatus === 'online' ? 'Connected' : backendStatus === 'connecting' ? 'Connecting' : 'Offline'}
          </span>
        </header>

        {emergency && (
          <div className="flex flex-wrap items-center gap-2 border-b border-danger/20 bg-danger-soft px-4 py-2.5 text-danger">
            <AlertTriangle className="size-4 shrink-0" aria-hidden />
            <p className="text-xs font-bold sm:text-sm">
              Mochi is paused. New computer actions will wait until you resume.
            </p>
            <Button
              size="sm"
              variant="danger"
              className="ml-auto border border-danger/30 bg-surface/70"
              loading={stoppedLoading}
              onClick={() => void resumeEverything()}
            >
              Resume Mochi
            </Button>
          </div>
        )}

        {backendStatus === 'offline' && (
          <div className="flex flex-wrap items-center gap-2 border-b border-warning/20 bg-warning-soft px-4 py-2.5 text-warning">
            <CloudOff className="size-4 shrink-0" aria-hidden />
            <p className="text-xs font-bold sm:text-sm">
              Can’t reach Mochi’s local backend{backendError ? ` — ${backendError}` : ''}.
            </p>
            <Button
              size="sm"
              variant="outline"
              className="ml-auto"
              icon={<RefreshCw className="size-3.5" aria-hidden />}
              onClick={() => void refreshHealth()}
            >
              Try again
            </Button>
          </div>
        )}

        <main ref={scrollRef} className="mochi-scroll flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-[1380px] px-4 pb-20 pt-5 sm:px-6 lg:px-8 lg:pt-8">
            {children}
          </div>
        </main>
      </div>
    </div>
  )
}

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
  icon,
}: {
  eyebrow?: string
  title: string
  description?: string
  actions?: ReactNode
  icon?: ReactNode
}) {
  return (
    <header className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div className="flex min-w-0 items-start gap-3">
        {icon && (
          <div className="mt-0.5 grid size-11 shrink-0 place-items-center rounded-2xl bg-accent-soft text-accent-strong shadow-[inset_0_1px_0_rgba(255,255,255,0.45)]">
            {icon}
          </div>
        )}
        <div className="min-w-0">
          {eyebrow && (
            <p className="mb-0.5 text-[11px] font-extrabold uppercase tracking-[0.14em] text-ink-faint">
              {eyebrow}
            </p>
          )}
          <h1 className="text-balance text-2xl font-extrabold tracking-tight text-ink sm:text-[28px]">
            {title}
          </h1>
          {description && (
            <p className="mt-1 max-w-2xl text-sm leading-relaxed text-ink-soft">{description}</p>
          )}
        </div>
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  )
}
