import { NavLink, useLocation } from 'react-router-dom'
import {
  Activity,
  CalendarClock,
  ChevronLeft,
  FolderOpen,
  House,
  ListChecks,
  Menu,
  MessageCircle,
  Monitor,
  PanelLeftOpen,
  Settings,
  Sparkles,
  Workflow,
  Brain,
  X,
} from 'lucide-react'
import { motion, useReducedMotion } from 'framer-motion'
import { useAppStore } from '@/stores/app'
import { Mascot, MascotAvatar } from '@/components/Mascot'
import { Badge, Button } from '@/components/ui'
import { cn } from '@/lib/utils'

const NAV_ITEMS = [
  { to: '/', label: 'Home', icon: House, end: true },
  { to: '/chat', label: 'Chat with Mochi', icon: MessageCircle },
  { to: '/activity', label: 'Live Activity', icon: Activity },
  { to: '/tasks', label: 'My Tasks', icon: ListChecks, badge: 'approvals' as const },
  { to: '/computer', label: 'Computer', icon: Monitor },
  { to: '/files', label: 'Files & Documents', icon: FolderOpen },
  { to: '/workflows', label: 'Workflows', icon: Workflow },
  { to: '/memory', label: 'Memory', icon: Brain },
  { to: '/schedule', label: 'Schedule', icon: CalendarClock },
  { to: '/settings', label: 'Settings', icon: Settings },
]

function StatusDot({ status }: { status: 'online' | 'offline' | 'connecting' }) {
  return (
    <span className="relative flex size-2.5" aria-hidden>
      {status === 'online' && (
        <span className="absolute inline-flex size-full animate-ping rounded-full bg-success opacity-40" />
      )}
      <span
        className={cn(
          'relative inline-flex size-2.5 rounded-full',
          status === 'online' && 'bg-success',
          status === 'connecting' && 'bg-warning',
          status === 'offline' && 'bg-danger',
        )}
      />
    </span>
  )
}

export function Sidebar({
  mobileOpen,
  onClose,
}: {
  mobileOpen: boolean
  onClose: () => void
}) {
  const collapsed = useAppStore((state) => state.sidebarCollapsed)
  const toggleSidebar = useAppStore((state) => state.toggleSidebar)
  const backendStatus = useAppStore((state) => state.backendStatus)
  const health = useAppStore((state) => state.health)
  const approvals = useAppStore((state) => state.approvals)
  const reduced = useReducedMotion()
  const location = useLocation()

  const provider = health?.services.provider
  const mode = health?.services.permission_mode ?? 'assisted'
  const emergency = health?.services.emergency_stopped ?? false

  return (
    <>
      <div
        className={cn(
          'fixed inset-0 z-30 bg-ink-deep/25 backdrop-blur-sm transition-opacity lg:hidden',
          mobileOpen ? 'opacity-100' : 'pointer-events-none opacity-0',
        )}
        onClick={onClose}
        aria-hidden
      />
      <aside
        className={cn(
          'fixed inset-y-0 left-0 z-40 flex w-[272px] flex-col border-r border-line bg-surface/85 backdrop-blur-2xl transition-[transform,width] duration-300 lg:static lg:translate-x-0',
          collapsed ? 'lg:w-[88px]' : 'lg:w-[272px]',
          mobileOpen ? 'translate-x-0' : '-translate-x-full',
        )}
        aria-label="Main navigation"
      >
        <div className={cn('flex items-center gap-3 px-4 pb-2 pt-5', collapsed && 'lg:justify-center lg:px-2')}>
          <div className="relative shrink-0">
            <Mascot state={emergency ? 'error' : 'idle'} size={collapsed ? 44 : 52} />
          </div>
          <div className={cn('min-w-0 flex-1', collapsed && 'lg:hidden')}>
            <p className="text-[22px] font-extrabold leading-none tracking-tight text-ink">Mochi</p>
            <p className="mt-1 truncate text-[11px] font-semibold text-ink-faint">Your little AI companion</p>
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="lg:hidden"
            onClick={onClose}
            aria-label="Close navigation"
          >
            <X className="size-4" aria-hidden />
          </Button>
        </div>

        <nav className="mochi-scroll mt-3 flex-1 overflow-y-auto px-3">
          <ul className="space-y-1">
            {NAV_ITEMS.map((item) => {
              const Icon = item.icon
              const active = item.end ? location.pathname === item.to : location.pathname.startsWith(item.to)
              return (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    onClick={onClose}
                    title={collapsed ? item.label : undefined}
                    className={cn(
                      'ring-focus group relative flex items-center gap-3 rounded-2xl px-3 py-2.5 text-sm font-bold transition-all duration-200',
                      active
                        ? 'bg-accent-soft text-accent-strong shadow-[inset_0_1px_0_rgba(255,255,255,0.5)]'
                        : 'text-ink-soft hover:bg-surface-muted hover:text-ink',
                      collapsed && 'lg:justify-center lg:px-0',
                    )}
                    aria-current={active ? 'page' : undefined}
                  >
                    {active && (
                      <motion.span
                        layoutId="nav-active"
                        className="absolute -left-3 top-1/2 h-6 w-1 -translate-y-1/2 rounded-r-full bg-accent"
                        transition={{ duration: reduced ? 0 : 0.25 }}
                      />
                    )}
                    <Icon
                      className={cn(
                        'size-[18px] shrink-0 transition-transform group-hover:scale-105',
                        active ? 'text-accent-strong' : 'text-ink-faint group-hover:text-accent',
                      )}
                      aria-hidden
                    />
                    <span className={cn('flex-1 truncate', collapsed && 'lg:hidden')}>{item.label}</span>
                    {item.badge === 'approvals' && approvals.length > 0 && (
                      <span
                        className={cn(
                          'grid min-w-5 place-items-center rounded-full bg-warning-soft px-1.5 py-0.5 text-[10px] font-extrabold text-warning',
                          collapsed && 'lg:absolute lg:right-2 lg:top-2',
                        )}
                      >
                        {approvals.length}
                      </span>
                    )}
                  </NavLink>
                </li>
              )
            })}
          </ul>

          <div className={cn('mt-5 rounded-2xl border border-line bg-surface-muted/70 p-3', collapsed && 'lg:hidden')}>
            <div className="flex items-center gap-2">
              <StatusDot status={backendStatus} />
              <p className="text-xs font-extrabold text-ink">
                {backendStatus === 'online' ? 'Connected' : backendStatus === 'connecting' ? 'Waking up…' : 'Backend offline'}
              </p>
            </div>
            <p className="mt-1 text-[11px] leading-relaxed text-ink-faint">
              {provider?.configured
                ? `${provider.provider} · ${provider.model}`
                : 'No AI model configured yet.'}
            </p>
            <div className="mt-2 flex flex-wrap gap-1">
              <Badge tone={emergency ? 'danger' : 'accent'}>{emergency ? 'Paused' : mode}</Badge>
              {provider && !provider.configured && <Badge tone="warning">setup needed</Badge>}
            </div>
          </div>
        </nav>

        <div className="border-t border-line p-3">
          <div className={cn('flex items-center gap-2', collapsed && 'lg:justify-center')}>
            <button
              type="button"
              onClick={toggleSidebar}
              className="ring-focus hidden size-9 place-items-center rounded-xl text-ink-faint transition-colors hover:bg-surface-muted hover:text-ink lg:grid"
              aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
              aria-pressed={collapsed}
            >
              {collapsed ? <PanelLeftOpen className="size-4" aria-hidden /> : <ChevronLeft className="size-4" aria-hidden />}
            </button>
            <div className={cn('flex-1', collapsed && 'lg:hidden')}>
              <NavLink
                to="/settings"
                onClick={onClose}
                className="ring-focus flex items-center gap-2 rounded-xl px-2 py-1.5 text-xs font-bold text-ink-soft transition-colors hover:bg-surface-muted hover:text-ink"
              >
                <MascotAvatar state="idle" size={24} />
                <span className="truncate">Local workspace</span>
                <Settings className="ml-auto size-3.5 text-ink-faint" aria-hidden />
              </NavLink>
            </div>
          </div>
          <p className={cn('mt-2 flex items-center gap-1 px-2 text-[10px] font-semibold text-ink-faint', collapsed && 'lg:hidden')}>
            <Sparkles className="size-3 text-accent" aria-hidden /> Everything stays on this PC
          </p>
        </div>
      </aside>
    </>
  )
}

export function MobileMenuButton({ onClick }: { onClick: () => void }) {
  return (
    <Button variant="outline" size="icon" onClick={onClick} aria-label="Open navigation" className="lg:hidden">
      <Menu className="size-4" aria-hidden />
    </Button>
  )
}
