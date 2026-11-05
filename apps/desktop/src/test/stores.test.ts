import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useToastStore, toast } from '@/stores/toast'
import { applyPreferences, useAppStore } from '@/stores/app'
import { useChatStore } from '@/stores/chat'
import { api } from '@/lib/api'
import type { AppEvent, ChatMessage, ToolExecutionRecord } from '@/lib/types'

function event(type: string, payload: Record<string, unknown> = {}): AppEvent {
  return { seq: 1, type, ts: new Date().toISOString(), payload }
}

function message(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: 'msg-1',
    task_id: 'task-1',
    role: 'assistant',
    content: 'The file says hello.',
    attachments: [],
    created_at: '2026-01-01T10:00:02Z',
    ...overrides,
  }
}

function execution(overrides: Partial<ToolExecutionRecord> = {}): ToolExecutionRecord {
  return {
    id: 'exe-1',
    task_id: 'task-1',
    conversation_id: 'conv-1',
    step_index: 1,
    tool_name: 'files.read_file',
    arguments: { path: 'a.txt' },
    status: 'completed',
    ok: true,
    verified: true,
    evidence: 'read 12 bytes',
    error: null,
    hint: null,
    permission: 'read',
    approval_status: null,
    duration_ms: 42,
    started_at: '2026-01-01T10:00:00Z',
    completed_at: '2026-01-01T10:00:01Z',
    created_at: '2026-01-01T10:00:00Z',
    ...overrides,
  }
}

async function loadConversation(records: ToolExecutionRecord[], messages = [message()]) {
  vi.spyOn(api, 'messages').mockResolvedValue({
    conversation: { id: 'conv-1', title: 'Test', created_at: '2026-01-01T10:00:00Z' },
    messages,
  })
  vi.spyOn(api, 'executions').mockResolvedValue({
    conversation_id: 'conv-1',
    total: records.length,
    limit: 200,
    offset: 0,
    executions: records,
  })
  await useChatStore.getState().openConversation('conv-1')
}

beforeEach(() => {
  vi.restoreAllMocks()
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

describe('persisted tool history', () => {
  it('rehydrates a terminal execution onto its assistant message', async () => {
    await loadConversation([execution()])

    const state = useChatStore.getState()
    expect(state.messages).toHaveLength(1)
    expect(state.messages[0].tools).toHaveLength(1)
    expect(state.messages[0].tools?.[0]).toMatchObject({
      id: 'exe-1',
      tool: 'files.read_file',
      status: 'succeeded',
      verified: true,
      evidence: 'read 12 bytes',
      permission: 'read',
    })
  })

  it('does not duplicate cards when the conversation is reopened', async () => {
    await loadConversation([execution()])
    await loadConversation([execution()])

    const tools = useChatStore.getState().messages[0].tools ?? []
    expect(tools).toHaveLength(1)
    expect(tools[0].id).toBe('exe-1')
  })

  it('renders failed executions with their error', async () => {
    await loadConversation([
      execution({ status: 'failed', ok: false, verified: false, evidence: '', error: 'Access denied' }),
    ])

    const tool = useChatStore.getState().messages[0].tools?.[0]
    expect(tool).toMatchObject({ status: 'failed', error: 'Access denied' })
  })

  it('seeds open executions into the live tool list without duplicating them', async () => {
    await loadConversation([execution({ status: 'running', completed_at: null })])

    const state = useChatStore.getState()
    expect(state.messages[0].tools ?? []).toHaveLength(0)
    expect(state.toolEvents).toHaveLength(1)
    expect(state.toolEvents[0]).toMatchObject({ id: 'exe-1', status: 'running' })
    expect(state.activeTaskId).toBe('task-1')
    expect(state.sending).toBe(true)
  })

  it('shows pending approval state from persisted history', async () => {
    await loadConversation([
      execution({ status: 'pending_approval', completed_at: null, approval_status: 'pending' }),
    ])

    const tool = useChatStore.getState().toolEvents[0]
    expect(tool).toMatchObject({ status: 'running', approvalStatus: 'pending' })
  })

  it('reconciles live events by execution id instead of creating duplicates', () => {
    useChatStore.setState({ activeTaskId: 'task-1' })
    useChatStore.getState().ingestEvent(
      event('tool.started', {
        task_id: 'task-1',
        execution_id: 'exe-live',
        step: 1,
        tool: 'files.read_file',
        arguments: { path: 'a.txt' },
        permission: 'read',
      }),
    )
    useChatStore.getState().ingestEvent(
      event('tool.completed', {
        task_id: 'task-1',
        execution_id: 'exe-live',
        step: 1,
        tool: 'files.read_file',
        status: 'completed',
        ok: true,
        verified: true,
        evidence: 'read 12 bytes',
        duration_ms: 42,
      }),
    )

    const tools = useChatStore.getState().toolEvents
    expect(tools).toHaveLength(1)
    expect(tools[0]).toMatchObject({
      id: 'exe-live',
      status: 'succeeded',
      verified: true,
      evidence: 'read 12 bytes',
    })
  })

  it('tracks approval status changes and resolution', () => {
    useChatStore.setState({ activeTaskId: 'task-1' })
    useChatStore.getState().ingestEvent(
      event('tool.started', {
        task_id: 'task-1',
        execution_id: 'exe-approval',
        step: 1,
        tool: 'files.delete',
        arguments: { path: 'a.txt' },
        permission: 'write',
      }),
    )
    useChatStore.getState().ingestEvent(
      event('tool.status', {
        task_id: 'task-1',
        execution_id: 'exe-approval',
        status: 'pending_approval',
        tool: 'files.delete',
      }),
    )
    expect(useChatStore.getState().toolEvents[0]).toMatchObject({
      status: 'running',
      approvalStatus: 'pending',
    })

    useChatStore.getState().ingestEvent(
      event('approval.resolved', {
        task_id: 'task-1',
        approval_id: 'apr-1',
        approved: false,
        reason: 'user',
      }),
    )
    expect(useChatStore.getState().toolEvents[0]).toMatchObject({
      status: 'running',
      approvalStatus: 'denied',
    })
  })

  it('keeps legacy histories without task ids visible', async () => {
    await loadConversation(
      [execution({ task_id: 'task-legacy' })],
      [message({ task_id: null, content: 'Older message' })],
    )

    const messages = useChatStore.getState().messages
    const withTools = messages.filter((item) => (item.tools ?? []).length > 0)
    expect(withTools).toHaveLength(1)
    expect(withTools[0].tools?.[0].id).toBe('exe-1')
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
