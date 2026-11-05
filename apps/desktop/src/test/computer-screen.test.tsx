import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ComputerScreen } from '@/screens/Computer'
import { api } from '@/lib/api'

vi.mock('@/stores/live', () => {
  const state = {
    live: {
      browser: { running: false, tabs: [], active: null },
      active_task: null,
      latest_screenshot: null,
      permission_mode: 'assisted',
      emergency_stopped: false,
      running_tasks: [],
      events: [],
      voice: {},
    },
    refresh: vi.fn(async () => undefined),
    capture: vi.fn(async () => undefined),
    captureLoading: false,
  }
  return { useLiveStore: (selector: (value: typeof state) => unknown) => selector(state) }
})

vi.mock('@/stores/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

vi.mock('@/lib/api', () => ({
  api: {
    metrics: vi.fn(),
    processes: vi.fn(),
    desktopWindows: vi.fn(),
    liveState: vi.fn(),
    computerState: vi.fn(),
    computerObserve: vi.fn(),
    screenshotUrl: (path: string) => path,
  },
  errorMessage: (error: unknown) => (error instanceof Error ? error.message : String(error)),
}))

const baseEngineState = {
  timestamp: '2026-01-01T00:00:00Z',
  engine: 'windows-uia',
  capabilities: { screenshot: true, uia: true, win32: true, ocr: false, clipboard: true, input: true },
  permission_mode: 'assisted' as const,
  emergency: { stopped: false, stopped_at: null, reason: '', generation: 2 },
  active_window: { handle: 1, title: 'Untitled - Notepad', process: 'notepad.exe', pid: 10 },
  screen: { monitors: [], count: 1, primary: 1 },
  activity: {
    status: 'completed',
    action: 'type_text',
    task_id: null,
    conversation_id: null,
    started_at: '2026-01-01T00:00:00Z',
    finished_at: '2026-01-01T00:00:01Z',
    detail: 'Expected text found in a document control.',
  },
  last_observation: {
    timestamp: '2026-01-01T00:00:00Z',
    active_window: 'Untitled - Notepad',
    window_count: 5,
    element_count: 12,
    detection_sources: ['window_metadata', 'uia'],
    screenshot: null,
    warnings: [],
  },
  last_verification: {
    verified: true,
    method: 'uia',
    detail: 'Expected text found in a document control.',
    evidence: 'UIA document control contains the expected text.',
    timestamp: '2026-01-01T00:00:01Z',
  },
  last_error: null,
}

beforeEach(() => {
  vi.mocked(api.metrics).mockResolvedValue({
    cpu_percent: 10,
    cpu_cores: 8,
    ram_percent: 40,
    ram_used_gb: 6.4,
    ram_total_gb: 16,
    volumes: [],
    battery: null,
    uptime_seconds: 3600,
  })
  vi.mocked(api.processes).mockResolvedValue({ processes: [] })
  vi.mocked(api.desktopWindows).mockResolvedValue({
    active: { ...baseEngineState.active_window, class_name: 'Notepad' },
    windows: [],
    count: 0,
  })
  vi.mocked(api.liveState).mockResolvedValue({} as never)
  vi.mocked(api.computerState).mockResolvedValue(baseEngineState)
})

function renderScreen() {
  return render(
    <MemoryRouter>
      <ComputerScreen />
    </MemoryRouter>,
  )
}

describe('Computer screen engine card', () => {
  it('shows readiness, verification evidence and observation counts', async () => {
    renderScreen()

    expect(await screen.findByText('Computer use engine')).toBeInTheDocument()
    expect(await screen.findByText('Ready')).toBeInTheDocument()
    expect(await screen.findByText('Last action verified')).toBeInTheDocument()
    expect(await screen.findByText(/5 windows · 12 UI elements/)).toBeInTheDocument()
    expect(await screen.findByText(/UIA document control contains the expected text/)).toBeInTheDocument()
  })

  it('surfaces a persistent STOP and explains that resuming is explicit', async () => {
    vi.mocked(api.computerState).mockResolvedValue({
      ...baseEngineState,
      emergency: { stopped: true, stopped_at: '2026-01-01T00:00:00Z', reason: 'test', generation: 3 },
    })
    renderScreen()

    expect(await screen.findByText('STOP engaged')).toBeInTheDocument()
    expect(await screen.findByText('Computer actions are paused')).toBeInTheDocument()
    expect(await screen.findByText(/explicitly resume from Settings or Live Activity/)).toBeInTheDocument()
  })
})
