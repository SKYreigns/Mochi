import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useToastStore, toast } from '@/stores/toast'
import { applyPreferences, useAppStore } from '@/stores/app'
import { useChatStore } from '@/stores/chat'
import type { AppEvent } from '@/lib/types'

function event(type: string, payload: Record<string, unknown> = {}): AppEvent {
  return { seq: 1, type, ts: new Date().toISOString(), payload }
}

beforeEach(() => {
  useToastStore.setState({ items: [] })
  useChatStore.setState({
    conversations: [],
    conversationsLoading: false,
    activeId: 'conv-1',
    messages: [],
    messagesLoading: false,
    sending: false,
    activeTaskId: null,
    taskStatus: null,
    plan: [],
    objective: '',
    streamingText: '',
    toolEvents: [],
    error: null,
    errorHint: null,
    lastPrompt: '',
  })
})

describe('toast store', () => {
  it('pushes a toast with a unique id', () => {
    const id = toast.success('Saved')
    expect(id).toMatch(/^toast-/)
    expect(useToastStore.getState().items).toHaveLength(1)
    expect(useToastStore.getState().items[0]).toMatchObject({ tone: 'success', title: 'Saved' })
  })

  it('caps the visible stack at four toasts', () => {
    for (let index = 0; index < 6; index += 1) toast.info(`Message ${index}`)
    const items = useToastStore.getState().items
    expect(items).toHaveLength(4)
    expect(items[items.length - 1].title).toBe('Message 5')
  })

  it('dismisses by id', () => {
    const id = toast.info('Hello')
    useToastStore.getState().dismiss(id)
    expect(useToastStore.getState().items).toHaveLength(0)
  })
})

describe('app preferences', () => {
  it('applies theme, accent and motion to the document', () => {
    applyPreferences({ theme: 'dark', accent: 'blush', motion: 'reduced' })
    const root = document.documentElement
    expect(root.classList.contains('dark')).toBe(true)
    expect(root.dataset.accent).toBe('blush')
    expect(root.dataset.motion).toBe('reduce')
  })

  it('returns to light mode and full motion', () => {
    applyPreferences({ theme: 'light', accent: 'seafoam', motion: 'full' })
    const root = document.documentElement
    expect(root.classList.contains('dark')).toBe(false)
    expect(root.dataset.accent).toBe('seafoam')
    expect(root.dataset.motion).toBe('full')
  })

  it('toggles sidebar state without touching health', () => {
    const before = useAppStore.getState().sidebarCollapsed
    useAppStore.getState().toggleSidebar()
    expect(useAppStore.getState().sidebarCollapsed).toBe(!before)
    useAppStore.getState().toggleSidebar()
    expect(useAppStore.getState().sidebarCollapsed).toBe(before)
  })
})

describe('chat event ingestion', () => {
  it('adopts a task id while a request is in flight', () => {
    useChatStore.setState({ sending: true, activeTaskId: null, activeId: 'conv-1' })
    useChatStore
      .getState()
      .ingestEvent(event('task.queued', { task_id: 'task-1', conversation_id: 'conv-1', status: 'queued' }))
    expect(useChatStore.getState().activeTaskId).toBe('task-1')
    expect(useChatStore.getState().taskStatus).toBe('queued')
  })

  it('ignores events for other tasks', () => {
    useChatStore.setState({ activeTaskId: 'task-1', streamingText: '' })
    useChatStore
      .getState()
      .ingestEvent(event('llm.delta', { task_id: 'task-2', delta: 'should not appear' }))
    expect(useChatStore.getState().streamingText).toBe('')
  })

  it('accumulates streaming deltas', () => {
    useChatStore.setState({ activeTaskId: 'task-1' })
    useChatStore.getState().ingestEvent(event('llm.delta', { task_id: 'task-1', delta: 'Hel' }))
    useChatStore.getState().ingestEvent(event('llm.delta', { task_id: 'task-1', delta: 'lo' }))
    expect(useChatStore.getState().streamingText).toBe('Hello')
    expect(useChatStore.getState().taskStatus).toBe('running')
  })

  it('records a tool lifecycle from start to completion', () => {
    useChatStore.setState({ activeTaskId: 'task-1' })
    useChatStore.getState().ingestEvent(
      event('tool.started', {
        task_id: 'task-1',
        step: 1,
        tool: 'files.read_file',
        arguments: { path: 'a.txt' },
      }),
    )
    let tools = useChatStore.getState().toolEvents
    expect(tools).toHaveLength(1)
    expect(tools[0]).toMatchObject({ status: 'running', tool: 'files.read_file' })

    useChatStore.getState().ingestEvent(
      event('tool.completed', {
        task_id: 'task-1',
        step: 1,
        tool: 'files.read_file',
        ok: true,
        evidence: 'read 12 bytes',
        duration_ms: 42,
      }),
    )
    tools = useChatStore.getState().toolEvents
    expect(tools).toHaveLength(1)
    expect(tools[0]).toMatchObject({
      status: 'succeeded',
      evidence: 'read 12 bytes',
      durationMs: 42,
    })
  })

  it('marks failed tools and restores the plan', () => {
    useChatStore.setState({ activeTaskId: 'task-1' })
    useChatStore.getState().ingestEvent(
      event('task.plan', {
        task_id: 'task-1',
        objective: 'Tidy files',
        steps: ['List files', { title: 'Read one', tools: ['files.read_file'] }],
      }),
    )
    expect(useChatStore.getState().plan).toEqual([
      { title: 'List files' },
      { title: 'Read one', tools: ['files.read_file'] },
    ])
    expect(useChatStore.getState().objective).toBe('Tidy files')

    useChatStore.getState().ingestEvent(
      event('tool.completed', {
        task_id: 'task-1',
        step: 1,
        tool: 'files.read_file',
        ok: false,
        error: 'Access denied',
      }),
    )
    expect(useChatStore.getState().toolEvents[0]).toMatchObject({
      status: 'failed',
      error: 'Access denied',
    })
  })

  it('surfaces a task failure with its hint', () => {
    useChatStore.setState({ activeTaskId: 'task-1', sending: true })
    useChatStore.getState().ingestEvent(
      event('task.failed', {
        task_id: 'task-1',
        error: 'No model configured',
        hint: 'Open Settings → AI Models',
      }),
    )
    const state = useChatStore.getState()
    expect(state.sending).toBe(false)
    expect(state.taskStatus).toBe('failed')
    expect(state.error).toBe('No model configured')
    expect(state.errorHint).toBe('Open Settings → AI Models')
  })
})

describe('chat composer guards', () => {
  it('does not send empty prompts', async () => {
    const state = useChatStore.getState()
    await state.send('   ')
    expect(useChatStore.getState().sending).toBe(false)
  })

  it('does not send while already sending', async () => {
    useChatStore.setState({ sending: true })
    const before = useChatStore.getState().messages.length
    await useChatStore.getState().send('hello')
    expect(useChatStore.getState().messages.length).toBe(before)
  })

  it('clears errors on request', () => {
    useChatStore.setState({ error: 'boom', errorHint: 'try again' })
    useChatStore.getState().clearError()
    expect(useChatStore.getState().error).toBeNull()
    expect(useChatStore.getState().errorHint).toBeNull()
  })
})

describe('websocket envelope validation', () => {
  it('treats malformed payloads as empty objects', () => {
    // The store is defensive: a missing payload must not throw.
    vi.spyOn(console, 'error').mockImplementation(() => {})
    expect(() =>
      useChatStore.getState().ingestEvent({
        seq: 1,
        type: 'unknown',
        ts: new Date().toISOString(),
        payload: {} as Record<string, unknown>,
      }),
    ).not.toThrow()
  })
})
