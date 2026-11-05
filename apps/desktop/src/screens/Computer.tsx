import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Activity,
  AppWindow,
  BatteryCharging,
  Camera,
  Cpu,
  ExternalLink,
  Eye,
  Globe,
  HardDrive,
  MemoryStick,
  Monitor,
  MousePointerClick,
  RefreshCw,
  Server,
  ShieldAlert,
  ShieldCheck,
  X,
} from 'lucide-react'
import { api, errorMessage } from '@/lib/api'
import type {
  BrowserTab,
  ComputerEngineState,
  ComputerUseObservation,
  DesktopWindows,
  SystemMetrics,
  SystemProcess,
} from '@/lib/types'
import { useLiveStore } from '@/stores/live'
import { toast } from '@/stores/toast'
import { PageHeader } from '@/components/AppShell'
import { Badge, Button, Card, Input, Metric, Progress, SectionHeading, Skeleton } from '@/components/ui'
import { cn, formatBytes, percent, statusLabel } from '@/lib/utils'

export function ComputerScreen() {
  const [metrics, setMetrics] = useState<SystemMetrics | null>(null)
  const [processes, setProcesses] = useState<SystemProcess[]>([])
  const [windows, setWindows] = useState<DesktopWindows | null>(null)
  const [windowsError, setWindowsError] = useState<string | null>(null)
  const [browserShot, setBrowserShot] = useState<string | null>(null)
  const [browserBusy, setBrowserBusy] = useState(false)
  const [url, setUrl] = useState('')
  const [loading, setLoading] = useState(true)
  const [engineState, setEngineState] = useState<ComputerEngineState | null>(null)
  const [engineObservation, setEngineObservation] = useState<ComputerUseObservation | null>(null)
  const [engineBusy, setEngineBusy] = useState(false)

  const live = useLiveStore((state) => state.live)
  const refreshLive = useLiveStore((state) => state.refresh)
  const capture = useLiveStore((state) => state.capture)
  const captureLoading = useLiveStore((state) => state.captureLoading)

  const refresh = useCallback(async () => {
    // Update each card as soon as its own request lands. Process enumeration
    // can take a few seconds on Windows, and it must not hold the live metrics
    // hostage while it finishes.
    const metricsPromise = api
      .metrics()
      .then((value) => setMetrics(value))
      .catch(() => undefined)
    const processPromise = api
      .processes(14)
      .then((value) => setProcesses(value.processes))
      .catch(() => undefined)
    const windowsPromise = api
      .desktopWindows()
      .then((value) => {
        setWindows(value)
        setWindowsError(null)
      })
      .catch((error: unknown) => {
        setWindowsError(errorMessage(error))
      })
    const livePromise = api
      .liveState()
      .then(() => undefined)
      .catch(() => undefined)
    const enginePromise = api
      .computerState()
      .then((value) => setEngineState(value))
      .catch(() => undefined)

    await Promise.allSettled([
      metricsPromise,
      processPromise,
      windowsPromise,
      livePromise,
      enginePromise,
    ])
    setLoading(false)
  }, [])

  useEffect(() => {
    void refresh()
    void refreshLive()
    const interval = window.setInterval(() => void refresh(), 6000)
    return () => window.clearInterval(interval)
  }, [refresh, refreshLive])

  const browser = live?.browser
  const activeWindow = windows?.active

  const openUrl = async () => {
    const target = url.trim()
    if (!target) return
    const normalized = /^https?:\/\//i.test(target) ? target : `https://${target}`
    setBrowserBusy(true)
    try {
      await api.browserOpen(normalized)
      setUrl('')
      await refreshLive()
      toast.success('Page opened in Mochi’s managed browser')
    } catch (error) {
      toast.error('Could not open that page', errorMessage(error))
    } finally {
      setBrowserBusy(false)
    }
  }

  const closeTab = async (tab: BrowserTab) => {
    setBrowserBusy(true)
    try {
      await api.browserClose(tab.tab_id)
      await refreshLive()
      toast.info('Tab closed')
    } catch (error) {
      toast.error('Could not close that tab', errorMessage(error))
    } finally {
      setBrowserBusy(false)
    }
  }

  const captureBrowser = async () => {
    setBrowserBusy(true)
    try {
      const result = await api.browserScreenshot()
      setBrowserShot(result.data.url)
      await refreshLive()
      toast.success('Browser screenshot captured')
    } catch (error) {
      toast.error('Could not capture the browser', errorMessage(error))
    } finally {
      setBrowserBusy(false)
    }
  }

  const observeDesktop = async () => {
    setEngineBusy(true)
    try {
      const result = await api.computerObserve({
        includeElements: true,
        includeScreenshot: true,
      })
      setEngineObservation(result.observation)
      const state = await api.computerState()
      setEngineState(state)
      toast.success('Desktop observed with UI Automation')
    } catch (error) {
      toast.error('Could not observe the desktop', errorMessage(error))
    } finally {
      setEngineBusy(false)
    }
  }

  return (
    <div>
      <PageHeader
        eyebrow="Computer"
        title="Your computer, gently observed"
        description="Live windows, browser tabs, screenshots and system metrics — read directly from this PC."
        icon={<Monitor className="size-5" aria-hidden />}
        actions={
          <>
            <Button variant="outline" size="sm" onClick={() => void refresh()} loading={loading} icon={<RefreshCw className="size-4" aria-hidden />}>
              Refresh
            </Button>
            <Button variant="soft" size="sm" onClick={() => void capture()} loading={captureLoading} icon={<Camera className="size-4" aria-hidden />}>
              Capture screen
            </Button>
          </>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Metric
          label="CPU"
          value={metrics ? percent(metrics.cpu_percent) : '—'}
          hint={metrics ? `${metrics.cpu_cores} logical cores` : 'Reading…'}
          icon={<Cpu className="size-4" aria-hidden />}
        />
        <Metric
          label="Memory"
          value={metrics ? percent(metrics.ram_percent) : '—'}
          hint={metrics ? `${metrics.ram_used_gb.toFixed(1)} / ${metrics.ram_total_gb.toFixed(1)} GB` : 'Reading…'}
          icon={<MemoryStick className="size-4" aria-hidden />}
          tone="info"
        />
        <Metric
          label="Battery"
          value={metrics?.battery ? percent(metrics.battery.percent) : 'No battery'}
          hint={metrics?.battery ? (metrics.battery.plugged_in ? 'Plugged in' : 'On battery') : 'Desktop power'}
          icon={<BatteryCharging className="size-4" aria-hidden />}
          tone="success"
        />
        <Metric
          label="Uptime"
          value={metrics ? formatUptime(metrics.uptime_seconds) : '—'}
          hint={live?.running_tasks.length ? `${live.running_tasks.length} active task(s)` : 'System idle'}
          icon={<Activity className="size-4" aria-hidden />}
          tone="warning"
        />
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
        <div className="space-y-4">
          <Card className="p-5">
            <SectionHeading
              title="Active window"
              description="The real foreground window, read with the desktop tool."
              action={<AppWindow className="size-4 text-ink-faint" aria-hidden />}
            />
            {windowsError ? (
              <div className="mt-3 rounded-2xl border border-warning/25 bg-warning-soft/60 p-3.5 text-warning">
                <p className="text-xs font-bold">Window inspection is unavailable.</p>
                <p className="mt-0.5 text-[11px] leading-relaxed">{windowsError}</p>
                <p className="mt-1 text-[11px] opacity-80">
                  Install the optional <code className="font-mono">pywin32</code> dependency on the backend to enable this.
                </p>
              </div>
            ) : !activeWindow ? (
              <Skeleton className="mt-3 h-24" />
            ) : (
              <div className="mt-3 flex items-start gap-3 rounded-2xl border border-line bg-gradient-to-br from-surface-muted/60 to-accent-soft/30 p-4">
                <span className="grid size-12 shrink-0 place-items-center rounded-2xl bg-surface-strong text-accent-strong shadow-sm">
                  <MousePointerClick className="size-5" aria-hidden />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-base font-extrabold text-ink">{activeWindow.title || 'Untitled window'}</p>
                  <p className="mt-0.5 text-xs text-ink-soft">
                    {activeWindow.process || 'Unknown process'} · PID {activeWindow.pid}
                  </p>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {activeWindow.rect && (
                      <Badge tone="neutral">
                        {activeWindow.rect.right - activeWindow.rect.left} × {activeWindow.rect.bottom - activeWindow.rect.top}
                      </Badge>
                    )}
                    <Badge tone="accent">{activeWindow.class_name || 'window'}</Badge>
                  </div>
                </div>
              </div>
            )}

            {windows && windows.windows.length > 0 && (
              <div className="mt-3">
                <p className="mb-1.5 text-[10px] font-extrabold uppercase tracking-[0.1em] text-ink-faint">
                  {windows.count} visible {windows.count === 1 ? 'window' : 'windows'}
                </p>
                <div className="mochi-scroll max-h-44 space-y-1 overflow-y-auto pr-1">
                  {windows.windows.slice(0, 10).map((window) => (
                    <div
                      key={window.handle}
                      className={cn(
                        'flex items-center gap-2 rounded-xl border px-2.5 py-1.5',
                        window.handle === activeWindow?.handle
                          ? 'border-accent/30 bg-accent-soft/40'
                          : 'border-line bg-surface-muted/30',
                      )}
                    >
                      <AppWindow className="size-3.5 shrink-0 text-ink-faint" aria-hidden />
                      <p className="min-w-0 flex-1 truncate text-[11px] font-semibold text-ink-soft">{window.title}</p>
                      <span className="shrink-0 font-mono text-[10px] text-ink-faint">PID {window.pid}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </Card>

          <Card className="p-5">
            <SectionHeading
              title="Computer use engine"
              description="Observe → act → observe again → verify. Screen text is untrusted data, never instructions."
              action={
                <Badge tone={engineState?.emergency.stopped ? 'danger' : 'success'} dot>
                  {!engineState ? 'Checking…' : engineState.emergency.stopped ? 'STOP engaged' : 'Ready'}
                </Badge>
              }
            />
            {!engineState ? (
              <Skeleton className="mt-3 h-28" />
            ) : (
              <>
                <div className="mt-3 flex flex-wrap gap-1.5">
                  <Badge tone="neutral">{engineState.engine}</Badge>
                  <Badge tone="accent">{engineState.permission_mode}</Badge>
                  {Object.entries(engineState.capabilities).map(([name, available]) => (
                    <Badge key={name} tone={available ? 'success' : 'neutral'}>
                      {name}
                    </Badge>
                  ))}
                </div>

                {engineState.emergency.stopped && (
                  <div className="mt-3 rounded-2xl border border-danger/25 bg-danger-soft/50 p-3 text-danger">
                    <p className="flex items-center gap-1.5 text-xs font-extrabold">
                      <ShieldAlert className="size-3.5" aria-hidden /> Computer actions are paused
                    </p>
                    <p className="mt-1 text-[11px] leading-relaxed">
                      Every new input action is blocked until you explicitly resume from Settings or Live Activity.
                      {engineState.emergency.reason ? ` Reason: ${engineState.emergency.reason}` : ''}
                    </p>
                  </div>
                )}

                <div className="mt-3 rounded-2xl border border-line bg-surface-muted/40 p-3">
                  <div className="flex items-center gap-2">
                    <Badge tone={engineState.activity.status === 'failed' ? 'danger' : 'neutral'}>
                      {statusLabel(engineState.activity.status)}
                    </Badge>
                    <p className="font-mono text-[10px] text-ink-faint">
                      {engineState.activity.action ?? 'idle'}
                    </p>
                  </div>
                  <p className="mt-1.5 text-xs text-ink-soft">
                    {engineState.activity.detail || 'No computer action recorded yet.'}
                  </p>
                  {engineState.last_error && (
                    <p className="mt-1 text-[11px] font-semibold text-danger">{engineState.last_error}</p>
                  )}
                </div>

                <div className="mt-2 rounded-2xl border border-line bg-surface-muted/40 p-3">
                  <div className="flex items-center gap-2">
                    {engineState.last_verification?.verified ? (
                      <ShieldCheck className="size-3.5 text-success" aria-hidden />
                    ) : (
                      <ShieldAlert className="size-3.5 text-warning" aria-hidden />
                    )}
                    <p className="text-xs font-extrabold text-ink">
                      {engineState.last_verification
                        ? engineState.last_verification.verified
                          ? 'Last action verified'
                          : 'Last action unverified'
                        : 'No verification yet'}
                    </p>
                    {engineState.last_verification?.method && (
                      <Badge tone="neutral">{engineState.last_verification.method}</Badge>
                    )}
                  </div>
                  <p className="mt-1 text-[11px] leading-relaxed text-ink-soft">
                    {engineState.last_verification?.detail ||
                      'Only a fresh observation can establish that an action worked.'}
                  </p>
                  {engineState.last_verification?.evidence && (
                    <p className="mt-1 break-words text-[10px] text-ink-faint">
                      {engineState.last_verification.evidence}
                    </p>
                  )}
                </div>

                <div className="mt-2 rounded-2xl border border-line bg-surface-muted/40 p-3">
                  <div className="flex items-center gap-2">
                    <Eye className="size-3.5 text-accent" aria-hidden />
                    <p className="text-xs font-extrabold text-ink">Desktop observation</p>
                  </div>
                  <p className="mt-1 text-[11px] text-ink-soft">
                    {(engineObservation?.active_window?.title ??
                      engineState.last_observation?.active_window ??
                      'No active window')}
                  </p>
                  <p className="mt-0.5 text-[10px] text-ink-faint">
                    {engineObservation
                      ? `${engineObservation.window_count ?? 0} windows · ${engineObservation.element_count ?? 0} UI elements`
                      : engineState.last_observation
                        ? `${engineState.last_observation.window_count ?? 0} windows · ${engineState.last_observation.element_count ?? 0} UI elements`
                        : 'Observe the desktop to capture fresh evidence.'}
                    {engineObservation?.detection_sources?.length
                      ? ` · ${engineObservation.detection_sources.join(', ')}`
                      : ''}
                  </p>
                  {engineObservation?.warnings && engineObservation.warnings.length > 0 && (
                    <p className="mt-1 text-[10px] text-warning">{engineObservation.warnings.join(' ')}</p>
                  )}
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <Button
                      size="sm"
                      variant="soft"
                      onClick={() => void observeDesktop()}
                      loading={engineBusy}
                      icon={<Eye className="size-3.5" aria-hidden />}
                    >
                      Observe desktop
                    </Button>
                    {engineState.activity.task_id && (
                      <Link
                        to="/activity"
                        className="text-[11px] font-extrabold text-accent-strong hover:underline"
                      >
                        Follow in Live Activity →
                      </Link>
                    )}
                  </div>
                </div>
              </>
            )}
          </Card>

          <Card className="p-5">
            <SectionHeading
              title="Desktop preview"
              description="The most recent screenshot Mochi can see."
              action={
                <Button size="sm" variant="soft" onClick={() => void capture()} loading={captureLoading} icon={<Camera className="size-3.5" aria-hidden />}>
                  New capture
                </Button>
              }
            />
            <div className="mt-3 overflow-hidden rounded-2xl border border-line bg-surface-muted/60">
              {live?.latest_screenshot ? (
                <img
                  key={live.latest_screenshot}
                  src={api.screenshotUrl(live.latest_screenshot)}
                  alt="Latest desktop screenshot"
                  className="max-h-[320px] w-full object-contain"
                />
              ) : (
                <div className="flex flex-col items-center gap-2 py-12 text-center">
                  <Camera className="size-6 text-ink-faint" aria-hidden />
                  <p className="text-xs font-semibold text-ink-faint">No desktop screenshot captured yet.</p>
                </div>
              )}
            </div>
          </Card>

          <Card className="p-5">
            <SectionHeading
              title="Disk volumes"
              description="Free space reported by Windows."
              action={<HardDrive className="size-4 text-ink-faint" aria-hidden />}
            />
            <div className="mt-3 space-y-3">
              {metrics && metrics.volumes.length > 0 ? (
                metrics.volumes.map((volume) => (
                  <div key={volume.mountpoint}>
                    <div className="flex items-baseline justify-between gap-2 text-xs">
                      <p className="font-bold text-ink">
                        {volume.mountpoint} <span className="font-mono text-[10px] text-ink-faint">{volume.device}</span>
                      </p>
                      <p className="text-[11px] text-ink-faint">
                        {volume.free_gb.toFixed(1)} GB free of {volume.total_gb.toFixed(1)} GB
                      </p>
                    </div>
                    <Progress value={volume.percent} tone={volume.percent > 90 ? 'danger' : volume.percent > 75 ? 'warning' : 'accent'} label={`Disk ${volume.mountpoint} usage`} className="mt-1.5" />
                  </div>
                ))
              ) : (
                <p className="text-xs text-ink-faint">No disk information returned.</p>
              )}
            </div>
          </Card>
        </div>

        <div className="space-y-4">
          <Card className="p-5">
            <SectionHeading
              title="Managed browser"
              description="The isolated browser Mochi uses for web tasks."
              action={<Badge tone={browser?.running ? 'success' : 'neutral'} dot>{browser?.running ? 'Running' : 'Closed'}</Badge>}
            />
            {browser?.running ? (
              <>
                {browser.active && (
                  <div className="mt-3 rounded-2xl border border-info/20 bg-info-soft/40 p-3">
                    <p className="truncate text-sm font-bold text-ink">{browser.active.title || 'Untitled page'}</p>
                    <p className="mt-0.5 break-all font-mono text-[11px] text-info">{browser.active.url}</p>
                  </div>
                )}
                <div className="mt-2.5 space-y-1.5">
                  {browser.tabs.map((tab) => (
                    <div
                      key={tab.tab_id}
                      className={cn(
                        'flex items-center gap-2 rounded-xl border px-2.5 py-1.5',
                        tab.active ? 'border-accent/30 bg-accent-soft/40' : 'border-line bg-surface-muted/30',
                      )}
                    >
                      <Globe className={cn('size-3.5 shrink-0', tab.active ? 'text-accent' : 'text-ink-faint')} aria-hidden />
                      <p className="min-w-0 flex-1 truncate text-[11px] font-semibold text-ink-soft">{tab.title || tab.url}</p>
                      <button
                        type="button"
                        onClick={() => void closeTab(tab)}
                        className="ring-focus rounded p-0.5 text-ink-faint hover:text-danger"
                        aria-label={`Close ${tab.title || tab.url}`}
                        disabled={browserBusy}
                      >
                        <X className="size-3" aria-hidden />
                      </button>
                    </div>
                  ))}
                </div>
                <div className="mt-3 flex gap-2">
                  <Button size="sm" variant="soft" onClick={() => void captureBrowser()} loading={browserBusy} icon={<Camera className="size-3.5" aria-hidden />}>
                    Capture page
                  </Button>
                  {browser.active && (
                    <a href={browser.active.url} target="_blank" rel="noreferrer" className="ring-focus inline-flex items-center gap-1 rounded-xl px-2 text-[11px] font-bold text-accent-strong hover:underline">
                      Open externally <ExternalLink className="size-3" aria-hidden />
                    </a>
                  )}
                </div>
                {browserShot && (
                  <div className="mt-3 overflow-hidden rounded-2xl border border-line">
                    <img src={api.screenshotUrl(browserShot)} alt="Managed browser screenshot" className="max-h-64 w-full object-contain" />
                  </div>
                )}
              </>
            ) : (
              <div className="mt-3 space-y-2">
                <p className="text-xs leading-relaxed text-ink-faint">
                  The browser is closed. Opening a page here starts Mochi’s isolated browser session.
                </p>
                <div className="flex gap-2">
                  <Input value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://example.com" aria-label="URL to open" onKeyDown={(event) => event.key === 'Enter' && void openUrl()} />
                  <Button size="md" onClick={() => void openUrl()} loading={browserBusy} aria-label="Open URL in managed browser">
                    Open
                  </Button>
                </div>
              </div>
            )}
          </Card>

          <Card className="p-5">
            <SectionHeading
              title="Active operations"
              description="Tasks currently using this computer."
              action={<Server className="size-4 text-ink-faint" aria-hidden />}
            />
            <div className="mt-3 space-y-2">
              {live?.active_task ? (
                <div className="rounded-2xl border border-accent/25 bg-accent-soft/40 p-3">
                  <div className="flex items-center gap-2">
                    <Badge tone="accent" dot>{statusLabel(live.active_task.status)}</Badge>
                    <span className="ml-auto text-[10px] text-ink-faint">{live.active_task.id.slice(-8)}</span>
                  </div>
                  <p className="mt-1.5 line-clamp-2 text-xs font-bold text-ink">{live.active_task.instruction}</p>
                  <Link to="/activity" className="mt-2 inline-block text-[11px] font-extrabold text-accent-strong hover:underline">
                    Follow in Live Activity →
                  </Link>
                </div>
              ) : (
                <p className="rounded-2xl border border-dashed border-line px-3 py-5 text-center text-xs text-ink-faint">
                  No active computer operations.
                </p>
              )}
              {live && live.running_tasks.length > 0 && (
                <p className="text-[11px] text-ink-faint">
                  Running task ids: <span className="font-mono">{live.running_tasks.join(', ')}</span>
                </p>
              )}
            </div>
          </Card>

          <Card className="p-5">
            <SectionHeading
              title="Top processes"
              description="Ordered by memory use, read live from Windows."
              action={<Activity className="size-4 text-ink-faint" aria-hidden />}
            />
            <div className="mochi-scroll mt-3 max-h-[300px] space-y-1 overflow-y-auto pr-1">
              {processes.length === 0 ? (
                <Skeleton className="h-32" />
              ) : (
                processes.map((process, index) => (
                  <div key={`${process.pid ?? index}-${process.name ?? ''}`} className="flex items-center gap-2 rounded-xl px-2 py-1.5 hover:bg-surface-muted/50">
                    <span className="w-5 text-right font-mono text-[10px] text-ink-faint">{process.pid}</span>
                    <p className="min-w-0 flex-1 truncate text-[11px] font-semibold text-ink-soft">{process.name || 'Unknown'}</p>
                    <span className="shrink-0 text-[10px] text-ink-faint">{formatBytes((process.memory_mb ?? 0) * 1024 * 1024)}</span>
                    <span className="w-12 shrink-0 text-right font-mono text-[10px] text-ink-faint">
                      {typeof process.cpu_percent === 'number' ? `${process.cpu_percent.toFixed(0)}%` : ''}
                    </span>
                  </div>
                ))
              )}
            </div>
          </Card>

          {live?.latest_screenshot && (
            <p className="break-all text-right text-[10px] text-ink-faint">
              Latest desktop capture · <span className="font-mono">{live.latest_screenshot.split('/').pop()}</span>
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

function formatUptime(seconds: number | undefined): string {
  if (!seconds || seconds < 0) return '—'
  const days = Math.floor(seconds / 86_400)
  const hours = Math.floor((seconds % 86_400) / 3600)
  if (days > 0) return `${days}d ${hours}h`
  const minutes = Math.floor((seconds % 3600) / 60)
  return `${hours}h ${minutes}m`
}
