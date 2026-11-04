import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Activity,
  AlertTriangle,
  Brain,
  CalendarClock,
  Camera,
  CheckCircle2,
  CircleSlash,
  ExternalLink,
  Globe,
  ListTree,
  Loader2,
  Pause,
  Play,
  Radio,
  Settings2,
  ShieldQuestion,
  Sparkles,
  Square,
  Wrench,
  XCircle,
} from 'lucide-react'
import { api, errorMessage } from '@/lib/api'
import type { AppEvent } from '@/lib/types'
import { useAppStore } from '@/stores/app'
import { useLiveStore } from '@/stores/live'
import { toast } from '@/stores/toast'
import { PageHeader } from '@/components/AppShell'
import { Mascot } from '@/components/Mascot'
import { TaskTimeline } from '@/components/TaskTimeline'
import { Badge, Button, Card, EmptyState, Progress, SectionHeading, Skeleton } from '@/components/ui'
import { cn, formatDateTime, humanizeTool, relativeTime, statusLabel, taskTone, type Tone } from '@/lib/utils'

function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined
}

function eventInfo(event: AppEvent): { title: string; detail?: string; tone: Tone; icon: typeof Activity } {
  const payload = event.payload
  switch (event.type) {
    case 'task.queued':
      return { title: 'Task queued', detail: stringValue(payload.instruction), tone: 'info', icon: Radio }
    case 'task.status':
      return {
        title: `Task is ${statusLabel(stringValue(payload.status) ?? 'running').toLowerCase()}`,
        detail: stringValue(payload.instruction),
        tone: 'accent',
        icon: Activity,
      }
    case 'task.plan':
      return { title: 'Plan ready', detail: stringValue(payload.objective), tone: 'accent', icon: ListTree }
    case 'agent.thinking':
      return { title: 'Thinking', detail: 'Mochi is choosing the next careful step.', tone: 'accent', icon: Brain }
    case 'llm.completed':
      return { title: 'Response ready', tone: 'info', icon: Sparkles }
    case 'tool.started':
      return {
        title: humanizeTool(stringValue(payload.tool) ?? 'tool'),
        detail: stringValue(payload.tool),
        tone: 'accent',
        icon: Wrench,
      }
    case 'tool.completed': {
      const ok = payload.ok !== false
      return {
        title: ok ? 'Tool finished' : 'Tool did not finish',
        detail: [stringValue(payload.tool), stringValue(payload.error)].filter(Boolean).join(' · '),
        tone: ok ? 'success' : 'danger',
        icon: ok ? CheckCircle2 : XCircle,
      }
    }
    case 'tool.retry':
      return {
        title: 'Retrying a tool',
        detail: [stringValue(payload.tool), stringValue(payload.error)].filter(Boolean).join(' · '),
        tone: 'warning',
        icon: AlertTriangle,
      }
    case 'task.completed':
      return { title: 'Task finished', detail: stringValue(payload.result), tone: 'success', icon: CheckCircle2 }
    case 'task.failed':
      return { title: 'Task needs attention', detail: stringValue(payload.error), tone: 'danger', icon: AlertTriangle }
    case 'task.cancelled':
      return { title: 'Task cancelled', tone: 'neutral', icon: CircleSlash }
    case 'approval.requested':
      return { title: 'Waiting for your approval', detail: stringValue(payload.tool_name), tone: 'warning', icon: ShieldQuestion }
    case 'approval.resolved':
      return { title: 'Approval resolved', detail: stringValue(payload.status), tone: 'success', icon: CheckCircle2 }
    case 'workflow.started':
      return { title: 'Workflow started', detail: stringValue(payload.name), tone: 'accent', icon: Sparkles }
    case 'workflow.completed':
      return { title: 'Workflow finished', detail: stringValue(payload.name), tone: 'success', icon: CheckCircle2 }
    case 'workflow.failed':
      return { title: 'Workflow failed', detail: stringValue(payload.error), tone: 'danger', icon: XCircle }
    case 'schedule.triggered':
      return { title: 'Schedule triggered', detail: stringValue(payload.name), tone: 'info', icon: CalendarClock }
    case 'schedule.failed':
      return { title: 'Schedule failed', detail: stringValue(payload.error), tone: 'danger', icon: AlertTriangle }
    case 'settings.updated':
      return { title: 'Settings updated', detail: stringValue(payload.key), tone: 'neutral', icon: Settings2 }
    case 'app.ready':
      return { title: 'Mochi backend ready', tone: 'success', icon: Sparkles }
    default:
      return { title: event.type.replace(/[._]/g, ' '), tone: 'neutral', icon: Activity }
  }
}

const EVENT_ICON_CLASS: Record<Tone, string> = {
  neutral: 'bg-surface-muted text-ink-faint',
  accent: 'bg-accent-soft text-accent-strong',
  success: 'bg-success-soft text-success',
  warning: 'bg-warning-soft text-warning',
  danger: 'bg-danger-soft text-danger',
  info: 'bg-info-soft text-info',
}

export function LiveActivityScreen() {
  const live = useLiveStore((state) => state.live)
  const events = useLiveStore((state) => state.events)
  const loading = useLiveStore((state) => state.loading)
  const error = useLiveStore((state) => state.error)
  const refresh = useLiveStore((state) => state.refresh)
  const capture = useLiveStore((state) => state.capture)
  const captureLoading = useLiveStore((state) => state.captureLoading)

  const health = useAppStore((state) => state.health)
  const stoppedLoading = useAppStore((state) => state.stoppedLoading)
  const stopEverything = useAppStore((state) => state.stopEverything)
  const resumeEverything = useAppStore((state) => state.resumeEverything)

  const [browserLoading, setBrowserLoading] = useState(false)

  const emergency = health?.services.emergency_stopped ?? live?.emergency_stopped ?? false
  const active = live?.active_task ?? null
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    void refresh()
    const interval = window.setInterval(() => void refresh(), 5000)
    const clock = window.setInterval(() => setNow(Date.now()), 1000)
    return () => {
      window.clearInterval(interval)
      window.clearInterval(clock)
    }
  }, [refresh])

  const activity = useMemo(
    () => events.filter((event) => event.type !== 'llm.delta').slice(-40).reverse(),
    [events],
  )

  const browser = live?.browser
  const plan = active?.plan ?? []
  const planSteps = Array.isArray(plan) ? plan.length : 0
  const currentStep = active?.current_step ?? 0
  const progress = planSteps > 0 ? Math.min(100, Math.round((currentStep / planSteps) * 100)) : active ? 25 : 0

  const openBrowser = async (url: string) => {
    setBrowserLoading(true)
    try {
      await api.browserOpen(url)
      await refresh()
      toast.success('Opened in the managed browser')
    } catch (err) {
      toast.error('Could not open that page', errorMessage(err))
    } finally {
      setBrowserLoading(false)
    }
  }

  const elapsed = (() => {
    if (!active) return null
    const task = live?.active_task
    if (!task) return null
    const event = events.find((item) => item.payload.task_id === task.id && item.type === 'task.status')
    if (!event) return null
    void now
    const start = new Date(event.ts).getTime()
    if (Number.isNaN(start)) return null
    const seconds = Math.max(0, Math.round((now - start) / 1000))
    return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
  })()

  return (
    <div>
      <PageHeader
        eyebrow="Live Activity"
        title="Watch Mochi work"
        description="Every event on this page comes from the real backend task stream. Nothing here is simulated."
        icon={<Activity className="size-5" aria-hidden />}
        actions={
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void refresh()}
              loading={loading}
              icon={<Radio className="size-4" aria-hidden />}
            >
              Refresh
            </Button>
            {emergency ? (
              <Button variant="success" size="sm" loading={stoppedLoading} onClick={() => void resumeEverything()} icon={<Play className="size-4" aria-hidden />}>
                Resume Mochi
              </Button>
            ) : (
              <Button
                variant="danger"
                size="md"
                loading={stoppedLoading}
                onClick={() => void stopEverything()}
                icon={<Square className="size-4 fill-current" aria-hidden />}
                className="font-extrabold uppercase tracking-wide"
                title="Cancel every running task immediately"
              >
                Emergency stop
              </Button>
            )}
          </>
        }
      />

      {error && (
        <div className="mb-4 rounded-2xl border border-warning/25 bg-warning-soft px-4 py-3 text-sm text-warning">
          {error}
        </div>
      )}

      {emergency && (
        <div className="mb-4 flex flex-wrap items-center gap-2 rounded-2xl border border-danger/25 bg-danger-soft px-4 py-3 text-danger">
          <Pause className="size-4" aria-hidden />
          <p className="text-sm font-bold">Mochi is paused. Every new computer action is blocked until you resume.</p>
          <Button size="sm" variant="danger" className="ml-auto bg-surface/70" onClick={() => void resumeEverything()} loading={stoppedLoading}>
            Resume
          </Button>
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
        <div className="space-y-4">
          <Card className="relative overflow-hidden p-5">
            <div className="pointer-events-none absolute -right-10 -top-12 size-44 rounded-full bg-aqua/30 blur-2xl" aria-hidden />
            <SectionHeading
              title="Current task"
              description={active ? 'Mochi is working through a real task.' : 'Nothing is running right now.'}
              action={active ? <Badge tone={taskTone(active.status)} dot>{statusLabel(active.status)}</Badge> : <Badge tone="success">Idle</Badge>}
            />
            {active ? (
              <>
                <p className="mt-3 text-balance text-lg font-extrabold leading-snug text-ink">{active.instruction}</p>
                <div className="mt-3 flex flex-wrap gap-2 text-[11px] font-semibold text-ink-faint">
                  <span className="rounded-full bg-surface-muted px-2.5 py-1">Task {active.id.slice(-8)}</span>
                  <span className="rounded-full bg-surface-muted px-2.5 py-1">Step {Math.min(currentStep + 1, Math.max(planSteps, 1))} of {Math.max(planSteps, 1)}</span>
                  {elapsed && <span className="rounded-full bg-surface-muted px-2.5 py-1">Running {elapsed}</span>}
                  <span className="rounded-full bg-surface-muted px-2.5 py-1">{String(live?.permission_mode ?? 'assisted')} mode</span>
                </div>
                <div className="mt-4">
                  <div className="mb-1.5 flex justify-between text-[11px] font-bold text-ink-faint">
                    <span>Plan progress</span>
                    <span>{progress}%</span>
                  </div>
                  <Progress value={progress} label="Task plan progress" />
                </div>
                <div className="mt-4 rounded-2xl border border-line bg-surface-muted/30 p-3.5">
                  <TaskTimeline steps={plan} currentStep={currentStep} status={active.status} />
                </div>
              </>
            ) : (
              <EmptyState
                mascot={<Mascot state={emergency ? 'error' : 'sleeping'} size={110} />}
                title={emergency ? 'Mochi is paused' : 'All quiet'}
                description={emergency ? 'Resume when you are ready for Mochi to use computer tools again.' : 'Start a chat or run a workflow and its live progress will appear here.'}
                className="mt-4 border-0 py-6"
                action={
                  <div className="flex gap-2">
                    <Link to="/chat">
                      <Button size="sm" variant="soft">Start a chat</Button>
                    </Link>
                    <Link to="/workflows">
                      <Button size="sm" variant="outline">Run a workflow</Button>
                    </Link>
                  </div>
                }
              />
            )}
          </Card>

          <Card className="p-5">
            <SectionHeading
              title="Screen preview"
              description="Captured by the real desktop screenshot tool."
              action={
                <Button
                  size="sm"
                  variant="soft"
                  loading={captureLoading}
                  onClick={() => void capture()}
                  icon={<Camera className="size-4" aria-hidden />}
                >
                  Capture screen
                </Button>
              }
            />
            <div className="mt-3 overflow-hidden rounded-2xl border border-line bg-surface-muted/50">
              {live?.latest_screenshot ? (
                <img
                  key={live.latest_screenshot}
                  src={api.screenshotUrl(live.latest_screenshot)}
                  alt="Latest desktop screenshot captured by Mochi"
                  className="max-h-[340px] w-full object-contain"
                />
              ) : (
                <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
                  <Camera className="size-6 text-ink-faint" aria-hidden />
                  <p className="text-xs font-semibold text-ink-faint">
                    No screenshot yet. Capture one to see what Mochi can see.
                  </p>
                </div>
              )}
            </div>
          </Card>

          <Card className="p-5">
            <SectionHeading
              title="Browser"
              description="Managed browser tabs and the current page."
              action={<Globe className="size-4 text-ink-faint" aria-hidden />}
            />
            {!browser?.running ? (
              <p className="mt-3 rounded-2xl border border-dashed border-line px-4 py-6 text-center text-xs text-ink-faint">
                The managed browser is not running. Mochi opens it only when a task needs it.
              </p>
            ) : (
              <div className="mt-3 space-y-2">
                {browser.active && (
                  <div className="rounded-2xl border border-line bg-info-soft/50 p-3">
                    <p className="text-[10px] font-extrabold uppercase tracking-[0.1em] text-info">Current page</p>
                    <p className="mt-0.5 truncate text-sm font-bold text-ink">{browser.active.title || 'Untitled page'}</p>
                    <div className="mt-1 flex items-center gap-2">
                      <p className="min-w-0 flex-1 truncate font-mono text-[11px] text-ink-soft">{browser.active.url}</p>
                      <Button
                        size="sm"
                        variant="ghost"
                        loading={browserLoading}
                        onClick={() => void openBrowser(browser.active?.url ?? 'https://example.com')}
                        aria-label="Reload current page"
                      >
                        <ExternalLink className="size-3.5" aria-hidden />
                      </Button>
                    </div>
                  </div>
                )}
                <div className="flex flex-wrap gap-1.5">
                  {browser.tabs.map((tab) => (
                    <span key={tab.tab_id} className="max-w-full truncate rounded-lg border border-line bg-surface-muted px-2 py-1 text-[11px] font-semibold text-ink-soft">
                      {tab.active ? '● ' : ''}
                      {tab.title || tab.url}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </Card>
        </div>

        <Card className="flex min-h-[400px] flex-col p-5">
          <SectionHeading
            title="Live timeline"
            description="Newest events first."
            action={<Badge tone="accent" dot>live</Badge>}
          />
          <div className="mochi-scroll mt-3 max-h-[calc(100vh-240px)] space-y-1 overflow-y-auto pr-1">
            {loading && activity.length === 0 ? (
              <>
                <Skeleton className="h-14" />
                <Skeleton className="h-14" />
                <Skeleton className="h-14" />
              </>
            ) : activity.length === 0 ? (
              <div className="flex flex-col items-center gap-2 py-14 text-center">
                <Mascot state="sleeping" size={90} />
                <p className="text-sm font-bold text-ink-soft">No events yet</p>
                <p className="max-w-xs text-xs text-ink-faint">Task events will stream here as soon as Mochi starts working.</p>
              </div>
            ) : (
              activity.map((event) => {
                const info = eventInfo(event)
                const Icon = info.icon
                return (
                  <div key={event.seq} className="flex gap-2.5 rounded-2xl px-2 py-2 transition-colors hover:bg-surface-muted/50">
                    <span className={cn('mt-0.5 grid size-7 shrink-0 place-items-center rounded-xl', EVENT_ICON_CLASS[info.tone])}>
                      <Icon className="size-3.5" aria-hidden />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline justify-between gap-2">
                        <p className="truncate text-xs font-bold text-ink">{info.title}</p>
                        <span className="shrink-0 text-[10px] text-ink-faint">{relativeTime(event.ts)}</span>
                      </div>
                      {info.detail && <p className="mt-0.5 line-clamp-2 text-[11px] leading-relaxed text-ink-faint">{info.detail}</p>}
                    </div>
                  </div>
                )
              })
            )}
          </div>
          <div className="mt-3 flex items-center justify-between border-t border-line pt-3 text-[10px] font-semibold text-ink-faint">
            <span>{activity.length} events visible</span>
            <span className="inline-flex items-center gap-1">
              <span className="size-1.5 rounded-full bg-success" aria-hidden />
              WebSocket reconnecting automatically
            </span>
          </div>
        </Card>
      </div>

      <p className="mt-4 flex items-center justify-end gap-1 text-[10px] text-ink-faint">
        <Loader2 className={cn('size-3', loading && 'animate-spin')} aria-hidden />
        Last refreshed {formatDateTime(new Date(now).toISOString())} · {health?.services.tools ?? 0} tools available
      </p>
    </div>
  )
}
