import { create } from 'zustand'
import { api, errorMessage } from '@/lib/api'
import type {
  AppEvent,
  ChatMessage,
  Conversation,
  PlanStep,
  Task,
} from '@/lib/types'
import { toast } from '@/stores/toast'

export interface ChatToolEvent {
  key: string
  step: number
  tool: string
  arguments: Record<string, unknown>
  permission?: string
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
  const key = `${step}:${tool}`
  const index = events.findIndex((item) => item.key === key && item.status === 'running')

  const next: ChatToolEvent = {
    key,
    step,
    tool,
    arguments: asRecord((payload.arguments as Record<string, unknown>) ?? {}),
    permission: text(payload.permission),
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
    arguments: Object.keys(next.arguments).length ? next.arguments : previous.arguments,
  }
  return updated
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
      const response = await api.messages(id)
      set({ messages: response.messages })
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
      case 'task.completed': {
        const tools = get().toolEvents
        const activeId = get().activeId
        set({ sending: false, taskStatus: 'completed' })

        const finish = async () => {
          if (!activeId) return
          try {
            const response = await api.messages(activeId)
            const loaded: ChatMessageView[] = response.messages.map((message) => ({ ...message }))
            if (tools.length > 0) {
              let index = -1
              for (let i = loaded.length - 1; i >= 0; i -= 1) {
                if (loaded[i].role === 'assistant') {
                  index = i
                  break
                }
              }
              if (index >= 0) {
                loaded[index] = { ...loaded[index], tools }
              } else {
                loaded.push({
                  id: `tools-${activeId}`,
                  role: 'assistant',
                  content: '',
                  attachments: [],
                  created_at: new Date().toISOString(),
                  tools,
                })
              }
            }
            set({
              messages: loaded,
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
        set({
          sending: false,
          taskStatus: event.type === 'task.cancelled' ? 'cancelled' : 'failed',
          error:
            event.type === 'task.failed'
              ? text(payload.error) ?? 'Mochi could not finish this task.'
              : null,
          errorHint: event.type === 'task.failed' ? text(payload.hint) ?? null : null,
        })
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
