import { create } from 'zustand'
import { api, errorMessage } from '@/lib/api'
import type {
  AppEvent,
  ChatMessage,
  Conversation,
  PlanStep,
  Task,
  ToolExecutionRecord,
} from '@/lib/types'
import { toast } from '@/stores/toast'

export interface ChatToolEvent {
  key: string
  /** Stable backend execution id; lets live events and persisted records reconcile. */
  id?: string
  taskId?: string
  step: number
  tool: string
  arguments: Record<string, unknown>
  permission?: string
  approvalStatus?: string
  status: 'running' | 'succeeded' | 'failed'
  evidence?: string
  error?: string
  hint?: string
  durationMs?: number
  verified?: boolean
}

export interface ChatMessageView extends ChatMessage {
  tools?: ChatToolEvent[]
}

interface ChatState {
  conversations: Conversation[]
  conversationsLoading: boolean
  activeId: string | null
  messages: ChatMessageView[]
  messagesLoading: boolean

  sending: boolean
  activeTaskId: string | null
  taskStatus: string | null
  plan: PlanStep[]
  objective: string
  streamingText: string
  toolEvents: ChatToolEvent[]
  error: string | null
  errorHint: string | null
  lastPrompt: string

  loadConversations: () => Promise<void>
  openConversation: (id: string | null) => Promise<void>
  newChat: () => void
  send: (text: string, attachments?: ChatMessage['attachments']) => Promise<void>
  stop: () => Promise<void>
  retry: () => Promise<void>
  clearError: () => void
  archiveConversation: (id: string) => Promise<void>
  ingestEvent: (event: AppEvent) => void
}

const asRecord = (payload: Record<string, unknown>): Record<string, unknown> => payload

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined
}

function num(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function upsertTool(
  events: ChatToolEvent[],
  payload: Record<string, unknown>,
  status: ChatToolEvent['status'],
): ChatToolEvent[] {
  const step = num(payload.step) ?? -1
  const tool = text(payload.tool) ?? 'unknown tool'
  const executionId = text(payload.execution_id)
  const key = executionId ?? `${step}:${tool}`
  const index = executionId
    ? events.findIndex((item) => item.id === executionId)
    : events.findIndex((item) => item.key === key && item.status === 'running')

  const next: ChatToolEvent = {
    key,
    id: executionId,
    taskId: text(payload.task_id),
    step,
    tool,
    arguments: asRecord((payload.arguments as Record<string, unknown>) ?? {}),
    permission: text(payload.permission),
    approvalStatus: text(payload.approval_status),
    status,
    evidence: text(payload.evidence),
    error: text(payload.error),
    hint: text(payload.hint),
    durationMs: num(payload.duration_ms),
    verified: payload.verified === true,
  }

  if (index === -1) {
    return [...events, next]
  }

  const updated = [...events]
  const previous = updated[index]
  updated[index] = {
    ...previous,
    ...next,
    id: next.id ?? previous.id,
    taskId: next.taskId ?? previous.taskId,
    arguments: Object.keys(next.arguments).length ? next.arguments : previous.arguments,
    approvalStatus: next.approvalStatus ?? previous.approvalStatus,
  }
  return updated
}

function executionStatus(record: ToolExecutionRecord): ChatToolEvent['status'] {
  if (record.status === 'running' || record.status === 'pending_approval') return 'running'
  return record.ok && record.status === 'completed' ? 'succeeded' : 'failed'
}

function persistedTool(record: ToolExecutionRecord): ChatToolEvent {
  return {
    key: record.id,
    id: record.id,
    taskId: record.task_id,
    step: record.step_index,
    tool: record.tool_name,
    arguments: record.arguments ?? {},
    permission: record.permission,
    approvalStatus: record.approval_status ?? undefined,
    status: executionStatus(record),
    evidence: record.evidence || undefined,
    error: record.error ?? undefined,
    hint: record.hint ?? undefined,
    durationMs: record.duration_ms,
    verified: record.verified,
  }
}

function attachTools(messages: ChatMessageView[], tools: ChatToolEvent[]): ChatMessageView[] {
  if (tools.length === 0) return messages
  const loaded = messages.map((message) => ({ ...message }))
  let index = -1
  for (let cursor = loaded.length - 1; cursor >= 0; cursor -= 1) {
    if (loaded[cursor].role === 'assistant') {
      index = cursor
      break
    }
  }
  if (index >= 0) {
    const existing = loaded[index].tools ?? []
    const seen = new Set(existing.map((item) => item.id ?? item.key))
    const merged = [...existing, ...tools.filter((item) => !seen.has(item.id ?? item.key))]
    loaded[index] = { ...loaded[index], tools: merged }
  } else {
    loaded.push({
      id: `tools-${tools[0].key}`,
      role: 'assistant',
      content: '',
      attachments: [],
      created_at: new Date().toISOString(),
      tools,
    })
  }
  return loaded
}

function attachExecutions(
  messages: ChatMessageView[],
  executions: ToolExecutionRecord[],
): ChatMessageView[] {
  // Open executions are driven by live/replayed WebSocket events instead, which
  // prevents a duplicate card while a task is still running.
  const terminal = executions.filter(
    (item) => item.status !== 'running' && item.status !== 'pending_approval',
  )
  if (terminal.length === 0) return messages

  const byTask = new Map<string, ChatToolEvent[]>()
  for (const record of terminal) {
    const list = byTask.get(record.task_id) ?? []
    list.push(persistedTool(record))
    byTask.set(record.task_id, list)
  }

  const loaded = messages.map((message) => ({ ...message }))
  const used = new Set<string>()
  for (let index = 0; index < loaded.length; index += 1) {
    const message = loaded[index]
    if (message.role !== 'assistant' || !message.task_id) continue
    const tools = byTask.get(message.task_id)
    if (!tools) continue
    loaded[index] = { ...message, tools }
    used.add(message.task_id)
  }

  // Legacy rows have no message.task_id. Keep their history visible by
  // appending the remaining executions in chronological order.
  const leftover: ChatToolEvent[] = []
  for (const [taskId, tools] of byTask) {
    if (!used.has(taskId)) leftover.push(...tools)
  }
  return attachTools(loaded, leftover)
}

async function fetchTimeline(
  conversationId: string,
  fallbackTools: ChatToolEvent[] = [],
): Promise<{ messages: ChatMessageView[]; openTaskId: string | null; openTools: ChatToolEvent[] }> {
  const [response, history] = await Promise.all([
    api.messages(conversationId),
    api.executions(conversationId).catch(() => null),
  ])
  const executions = history?.executions ?? []
  const messages = attachExecutions(response.messages, executions)
  const open = executions.filter(
    (item) => item.status === 'running' || item.status === 'pending_approval',
  )
  return {
    messages:
      executions.length === 0 && fallbackTools.length > 0
        ? attachTools(messages, fallbackTools)
        : messages,
    openTaskId: open.length > 0 ? open[open.length - 1].task_id : null,
    // Open records seed the live tool list. Later WebSocket events reconcile by
    // execution id, so a reload mid-task does not lose or duplicate the card.
    openTools: open.map(persistedTool),
  }
}

let optimisticCounter = 0

export const useChatStore = create<ChatState>()((set, get) => ({
  conversations: [],
  conversationsLoading: false,
  activeId: null,
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

  loadConversations: async () => {
    set({ conversationsLoading: true })
    try {
      const response = await api.conversations()
      set({ conversations: response.conversations })
    } catch {
      set({ conversations: [] })
    } finally {
      set({ conversationsLoading: false })
    }
  },

  openConversation: async (id) => {
    if (!id) {
      set({
        activeId: null,
        messages: [],
        activeTaskId: null,
        taskStatus: null,
        plan: [],
        objective: '',
        streamingText: '',
        toolEvents: [],
        error: null,
        errorHint: null,
      })
      return
    }
    set({
      activeId: id,
      messagesLoading: true,
      error: null,
      errorHint: null,
      streamingText: '',
      toolEvents: [],
      plan: [],
      objective: '',
      taskStatus: null,
      activeTaskId: null,
    })
    try {
      const timeline = await fetchTimeline(id)
      set({
        messages: timeline.messages,
        ...(timeline.openTaskId
          ? {
              activeTaskId: timeline.openTaskId,
              taskStatus: 'running',
              sending: true,
              toolEvents: timeline.openTools,
            }
          : {}),
      })
    } catch (error) {
      toast.error('Could not open this conversation', errorMessage(error))
      set({ messages: [] })
    } finally {
      set({ messagesLoading: false })
    }
  },

  newChat: () => {
    set({
      activeId: null,
      messages: [],
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
  },

  send: async (textValue, attachments = []) => {
    const prompt = textValue.trim()
    if (!prompt || get().sending) return

    const optimistic: ChatMessageView = {
      id: `optimistic-${++optimisticCounter}`,
      role: 'user',
      content: prompt,
      attachments,
      created_at: new Date().toISOString(),
    }

    set((state) => ({
      messages: [...state.messages, optimistic],
      sending: true,
      activeTaskId: null,
      taskStatus: 'queued',
      plan: [],
      objective: '',
      streamingText: '',
      toolEvents: [],
      error: null,
      errorHint: null,
      lastPrompt: prompt,
    }))

    try {
      const result = await api.sendChat(prompt, get().activeId, attachments)
      set({
        activeId: result.conversation_id,
        activeTaskId: result.task_id,
        taskStatus: result.task.status,
      })
      void get().loadConversations()
    } catch (error) {
      const message = errorMessage(error)
      set((state) => ({
        sending: false,
        activeTaskId: null,
        taskStatus: 'failed',
        error: message,
        messages: state.messages.map((item) =>
          item.id === optimistic.id ? { ...item } : item,
        ),
      }))
      toast.error('Mochi could not start that request', message)
    }
  },

  stop: async () => {
    const taskId = get().activeTaskId
    if (!taskId) return
    try {
      await api.cancelTask(taskId)
      set({ taskStatus: 'cancelled', sending: false })
      toast.info('Task cancelled')
      void get().loadConversations()
    } catch (error) {
      toast.error('Could not cancel the task', errorMessage(error))
    }
  },

  retry: async () => {
    const prompt = get().lastPrompt
    if (!prompt) return
    await get().send(prompt)
  },

  clearError: () => set({ error: null, errorHint: null }),

  archiveConversation: async (id) => {
    try {
      await api.archiveConversation(id)
      set((state) => ({
        conversations: state.conversations.filter((item) => item.id !== id),
        ...(state.activeId === id
          ? {
              activeId: null,
              messages: [],
              streamingText: '',
              toolEvents: [],
              activeTaskId: null,
            }
          : {}),
      }))
      toast.success('Conversation archived')
    } catch (error) {
      toast.error('Could not archive the conversation', errorMessage(error))
    }
  },

  ingestEvent: (event) => {
    const payload = event.payload
    const taskId = text(payload.task_id)
    const conversationId = text(payload.conversation_id)
    const state = get()

    // Adopt a task that belongs to the in-flight conversation even if the HTTP
    // response has not resolved yet.
    if (state.sending && !state.activeTaskId && taskId) {
      if (!conversationId || !state.activeId || conversationId === state.activeId) {
        set({
          activeTaskId: taskId,
          ...(conversationId && !state.activeId ? { activeId: conversationId } : {}),
        })
      }
    }

    if (!get().activeTaskId || taskId !== get().activeTaskId) return

    switch (event.type) {
      case 'task.queued':
      case 'task.status': {
        const status = text(payload.status)
        if (status) set({ taskStatus: status })
        break
      }
      case 'task.plan': {
        const steps = Array.isArray(payload.steps)
          ? (payload.steps as unknown[])
              .map((step) => {
                if (typeof step === 'string') return { title: step }
                if (step && typeof step === 'object') {
                  const record = step as Record<string, unknown>
                  return {
                    title: text(record.title) ?? 'Step',
                    tools: Array.isArray(record.tools) ? record.tools.map(String) : undefined,
                  }
                }
                return null
              })
              .filter((step): step is PlanStep => step !== null)
          : []
        set({
          plan: steps,
          objective: text(payload.objective) ?? get().objective,
          taskStatus: 'running',
        })
        break
      }
      case 'agent.thinking': {
        set({ taskStatus: 'running' })
        break
      }
      case 'llm.delta': {
        const delta = text(payload.delta)
        if (delta) {
          set((current) => ({
            streamingText: current.streamingText + delta,
            taskStatus: 'running',
          }))
        }
        break
      }
      case 'tool.started': {
        set((current) => ({ toolEvents: upsertTool(current.toolEvents, payload, 'running') }))
        break
      }
      case 'tool.completed': {
        set((current) => ({
          toolEvents: upsertTool(current.toolEvents, payload, payload.ok === false ? 'failed' : 'succeeded'),
        }))
        break
      }
      case 'tool.status': {
        const executionId = text(payload.execution_id)
        const status = text(payload.status)
        set((current) => ({
          toolEvents: current.toolEvents.map((item) =>
            item.id === executionId
              ? {
                  ...item,
                  status: status === 'pending_approval' ? 'running' : item.status,
                  approvalStatus:
                    status === 'pending_approval' ? 'pending' : item.approvalStatus,
                }
              : item,
          ),
        }))
        break
      }
      case 'tool.retry': {
        set((current) => ({
          toolEvents: current.toolEvents.map((item) =>
            item.status === 'running'
              ? { ...item, error: text(payload.error) ?? item.error }
              : item,
          ),
        }))
        break
      }
      case 'approval.resolved': {
        const approved = payload.approved === true
        const reason = text(payload.reason)
        const approvalTask = text(payload.task_id)
        const resolved = approved ? 'approved' : reason === 'expired' ? 'expired' : 'denied'
        set((current) => ({
          toolEvents: current.toolEvents.map((item) =>
            item.status === 'running' &&
            (!approvalTask || item.taskId === approvalTask) &&
            (!item.approvalStatus || item.approvalStatus === 'pending')
              ? { ...item, approvalStatus: resolved }
              : item,
          ),
        }))
        break
      }
      case 'task.completed': {
        const tools = get().toolEvents
        const activeId = get().activeId
        set({ sending: false, taskStatus: 'completed' })

        const finish = async () => {
          if (!activeId) {
            set({ streamingText: '', toolEvents: [] })
            return
          }
          try {
            const timeline = await fetchTimeline(activeId, tools)
            set({
              messages: timeline.messages,
              activeTaskId: timeline.openTaskId,
              sending: Boolean(timeline.openTaskId),
              taskStatus: timeline.openTaskId ? 'running' : 'completed',
              streamingText: '',
              toolEvents: [],
              plan: [],
              objective: '',
              error: null,
              errorHint: null,
            })
          } catch {
            set({ streamingText: '', toolEvents: [] })
          }
        }
        void finish()
        void get().loadConversations()
        break
      }
      case 'task.failed':
      case 'task.cancelled': {
        const activeId = get().activeId
        const tools = get().toolEvents
        set({
          sending: false,
          taskStatus: event.type === 'task.cancelled' ? 'cancelled' : 'failed',
          error:
            event.type === 'task.failed'
              ? text(payload.error) ?? 'Mochi could not finish this task.'
              : null,
          errorHint: event.type === 'task.failed' ? text(payload.hint) ?? null : null,
        })
        // Persisted cards must remain visible after a failure too.
        const finish = async () => {
          if (!activeId) {
            set({ streamingText: '', toolEvents: [] })
            return
          }
          try {
            const timeline = await fetchTimeline(activeId, tools)
            set({
              messages: timeline.messages,
              streamingText: '',
              toolEvents: [],
            })
          } catch {
            set({ streamingText: '', toolEvents: [] })
          }
        }
        void finish()
        void get().loadConversations()
        break
      }
      default:
        break
    }
  },
}))

// Re-exported for components that need to show a task snapshot.
export type { Task }
