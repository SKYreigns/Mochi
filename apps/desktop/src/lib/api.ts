import type {
  Approval,
  AppEvent,
  Assignment,
  BrowserState,
  ChatMessage,
  ChatSendResult,
  ComputerEngineState,
  ComputerUseObservation,
  Conversation,
  DesktopWindows,
  DirectoryListing,
  ErrorDetail,
  FileRoots,
  Health,
  LiveState,
  MemoryRecord,
  ModelInfo,
  ProviderStatus,
  Schedule,
  SettingsSnapshot,
  SystemMetrics,
  SystemProcess,
  Task,
  TaskDetail,
  ToolExecutionRecord,
  ToolInfo,
  VoiceStatus,
  Workflow,
  WorkflowRun,
} from './types'

export const API_BASE = (
  import.meta.env.VITE_MOCHI_API_URL?.trim() || 'http://127.0.0.1:8765'
).replace(/\/$/, '')

export const WS_URL = API_BASE.replace(/^http/, 'ws') + '/ws/events'

export class ApiError extends Error {
  readonly status: number
  readonly detail: ErrorDetail | string | null
  readonly hint?: string
  readonly retryable: boolean

  constructor(
    message: string,
    status: number,
    detail: ErrorDetail | string | null = null,
    hint?: string,
    retryable = false,
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.hint = hint
    this.retryable = retryable
  }
}

function messageFromDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === 'object' && item && 'msg' in item) {
          return String((item as { msg: unknown }).msg)
        }
        return 'Invalid request'
      })
      .join('; ')
  }
  if (detail && typeof detail === 'object') {
    const record = detail as ErrorDetail
    if (typeof record.message === 'string' && record.message) return record.message
  }
  return 'The backend returned an error.'
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error) {
    if (error.name === 'AbortError') return 'Request cancelled.'
    if (error.message.includes('fetch')) {
      return 'Mochi cannot reach the backend. Is it running on 127.0.0.1:8765?'
    }
    return error.message
  }
  return 'Something went wrong.'
}

interface RequestOptions {
  method?: string
  body?: unknown
  signal?: AbortSignal
  timeoutMs?: number
  headers?: Record<string, string>
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal, timeoutMs = 30_000, headers = {} } = options
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)
  const onAbort = () => controller.abort()
  signal?.addEventListener('abort', onAbort, { once: true })

  try {
    const response = await fetch(`${API_BASE}${path}`, {
      method,
      headers:
        body instanceof FormData
          ? headers
          : { 'Content-Type': 'application/json', ...headers },
      body:
        body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
      signal: controller.signal,
    })

    if (!response.ok) {
      let parsed: unknown = null
      try {
        parsed = await response.json()
      } catch {
        parsed = await response.text().catch(() => null)
      }
      const detail =
        parsed && typeof parsed === 'object' && 'detail' in parsed
          ? (parsed as { detail: unknown }).detail
          : parsed
      const record = (
        detail && typeof detail === 'object' ? detail : {}
      ) as ErrorDetail
      throw new ApiError(
        messageFromDetail(detail),
        response.status,
        detail as ErrorDetail | string | null,
        typeof record.hint === 'string' ? record.hint : undefined,
        Boolean(record.retryable),
      )
    }

    if (response.status === 204) return undefined as T
    return (await response.json()) as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError('The request timed out or was cancelled.', 0)
    }
    throw new ApiError(errorMessage(error), 0)
  } finally {
    window.clearTimeout(timer)
    signal?.removeEventListener('abort', onAbort)
  }
}

function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') {
      search.set(key, String(value))
    }
  }
  const value = search.toString()
  return value ? `?${value}` : ''
}

export const api = {
  health: () => request<Health>('/api/health'),
  eventHistory: (limit = 100) =>
    request<{ events: AppEvent[] }>(`/api/events/history${query({ limit })}`),
  tools: () => request<{ tools: ToolInfo[]; count: number }>('/api/tools'),

  settings: () => request<SettingsSnapshot>('/api/settings'),
  updateSettings: (updates: Record<string, unknown>) =>
    request<{ updated: string[]; settings: SettingsSnapshot }>('/api/settings', {
      method: 'PATCH',
      body: { updates },
    }),
  provider: () => request<{ provider: ProviderStatus }>('/api/settings/provider'),
  models: (params: { provider?: string; base_url?: string } = {}) =>
    request<{ models: ModelInfo[] }>(`/api/settings/models${query(params)}`, {
      timeoutMs: 45_000,
    }),
  saveApiKey: (provider: string, apiKey: string) =>
    request<{ saved: boolean; provider: string }>('/api/settings/api-key', {
      method: 'POST',
      body: { provider, api_key: apiKey },
    }),
  deleteApiKey: (provider: string) =>
    request<{ deleted: boolean }>(`/api/settings/api-key/${encodeURIComponent(provider)}`, {
      method: 'DELETE',
    }),

  conversations: () => request<{ conversations: Conversation[] }>('/api/chat/conversations'),
  createConversation: (title = 'New conversation') =>
    request<{ id: string; title: string }>('/api/chat/conversations', {
      method: 'POST',
      body: { title },
    }),
  messages: (conversationId: string) =>
    request<{ conversation: { id: string; title: string; created_at: string }; messages: ChatMessage[] }>(
      `/api/chat/conversations/${encodeURIComponent(conversationId)}/messages`,
    ),
  executions: (conversationId: string, params: { limit?: number; offset?: number } = {}) =>
    request<{
      conversation_id: string
      total: number
      limit: number
      offset: number
      executions: ToolExecutionRecord[]
    }>(`/api/chat/conversations/${encodeURIComponent(conversationId)}/executions${query(params)}`),
  archiveConversation: (conversationId: string) =>
    request<{ archived: string }>(
      `/api/chat/conversations/${encodeURIComponent(conversationId)}`,
      { method: 'DELETE' },
    ),
  sendChat: (
    message: string,
    conversationId: string | null,
    attachments: ChatMessage['attachments'] = [],
    signal?: AbortSignal,
  ) =>
    request<ChatSendResult>('/api/chat', {
      method: 'POST',
      body: { message, conversation_id: conversationId, attachments },
      signal,
    }),
  chatTask: (taskId: string) =>
    request<{ task: Task }>(`/api/chat/tasks/${encodeURIComponent(taskId)}`),

  tasks: (params: { limit?: number; status?: string } = {}) =>
    request<{ tasks: Task[] }>(`/api/tasks${query(params)}`),
  task: (taskId: string) => request<TaskDetail>(`/api/tasks/${encodeURIComponent(taskId)}`),
  cancelTask: (taskId: string) =>
    request<{ cancelled: boolean; task_id: string }>(
      `/api/tasks/${encodeURIComponent(taskId)}/cancel`,
      { method: 'POST' },
    ),
  emergencyStop: () =>
    request<{ stopped: boolean; cancelled_tasks: number }>('/api/tasks/stop', { method: 'POST' }),
  emergencyResume: () =>
    request<{ stopped: boolean }>('/api/tasks/resume', { method: 'POST' }),

  approvals: (status: string | null = 'pending') =>
    request<{ approvals: Approval[] }>(`/api/approvals${query({ status })}`),
  decideApproval: (approvalId: string, approved: boolean, note = '') =>
    request<{ id: string; status: string; decided_at: string | null }>(
      `/api/approvals/${encodeURIComponent(approvalId)}/decide`,
      { method: 'POST', body: { approved, note } },
    ),

  fileRoots: () => request<FileRoots>('/api/files/roots'),
  listDirectory: (path: string) =>
    request<{ data: DirectoryListing; evidence: string }>(
      `/api/files/list${query({ path })}`,
    ),
  readFile: (path: string, maxChars = 20000) =>
    request<{ data: { path: string; content: string; chars: number; truncated: boolean } }>(
      `/api/files/read${query({ path, max_chars: maxChars })}`,
    ),
  fileMetadata: (path: string) =>
    request<{ data: Record<string, unknown> }>(`/api/files/metadata${query({ path })}`),
  searchFiles: (queryText: string, directory: string) =>
    request<{ data: { results: FileEntryLike[]; count: number; scanned: number }; evidence: string }>(
      `/api/files/search${query({ query: queryText, directory })}`,
    ),
  organizePreview: (directory: string, mode = 'by_category', includeSubfolders = false) =>
    request<{ data: Record<string, unknown>; evidence: string }>('/api/files/organize/preview', {
      method: 'POST',
      body: { directory, mode, include_subfolders: includeSubfolders },
    }),
  uploadFile: async (file: File, signal?: AbortSignal) => {
    const form = new FormData()
    form.append('file', file)
    return request<{ name: string; path: string; mime: string; size: number }>(
      '/api/files/upload',
      { method: 'POST', body: form, signal, timeoutMs: 120_000 },
    )
  },
  downloadUrl: (path: string) => `${API_BASE}/api/files/download${query({ path })}`,

  memory: (params: { query?: string; kind?: string; limit?: number } = {}) =>
    request<{ memories: MemoryRecord[] }>(`/api/memory${query(params)}`),
  createMemory: (payload: {
    key: string
    value: string
    kind?: string
    tags?: string
    pinned?: boolean
  }) => request<{ memory: MemoryRecord }>('/api/memory', { method: 'POST', body: payload }),
  updateMemory: (id: string, payload: Partial<Pick<MemoryRecord, 'key' | 'value' | 'kind' | 'tags' | 'pinned'>>) =>
    request<{ memory: MemoryRecord }>(`/api/memory/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: payload,
    }),
  deleteMemory: (id: string) =>
    request<{ deleted: string }>(`/api/memory/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  searchHistory: (text: string, limit = 10) =>
    request<{ results: Array<Record<string, unknown>> }>(
      `/api/memory/history/search${query({ query: text, limit })}`,
    ),

  workflows: () => request<{ workflows: Workflow[] }>('/api/workflows'),
  createWorkflow: (payload: {
    name: string
    description?: string
    steps?: Array<{ name: string; kind: string; config: Record<string, unknown> }>
    enabled?: boolean
  }) => request<{ id: string; name: string }>('/api/workflows', { method: 'POST', body: payload }),
  updateWorkflow: (
    id: string,
    payload: Partial<{
      name: string
      description: string
      enabled: boolean
      steps: Array<{ name: string; kind: string; config: Record<string, unknown> }>
    }>,
  ) =>
    request<{ id: string; enabled: boolean }>(`/api/workflows/${encodeURIComponent(id)}`, {
      method: 'PUT',
      body: payload,
    }),
  deleteWorkflow: (id: string) =>
    request<{ deleted: string }>(`/api/workflows/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    }),
  runWorkflow: (id: string) =>
    request<{ started: boolean; workflow_id: string }>(
      `/api/workflows/${encodeURIComponent(id)}/run`,
      { method: 'POST' },
    ),
  workflowRuns: (id: string, limit = 20) =>
    request<{ runs: WorkflowRun[] }>(
      `/api/workflows/${encodeURIComponent(id)}/runs${query({ limit })}`,
    ),

  schedules: () => request<{ schedules: Schedule[] }>('/api/schedules'),
  createSchedule: (payload: {
    name: string
    instruction?: string
    workflow_id?: string | null
    schedule_type: 'once' | 'interval' | 'daily'
    run_at?: string | null
    interval_minutes?: number | null
    time_of_day?: string | null
    enabled?: boolean
  }) => request<{ id: string; next_run_at: string | null }>('/api/schedules', {
    method: 'POST',
    body: payload,
  }),
  updateSchedule: (id: string, payload: Record<string, unknown>) =>
    request<{ id: string; next_run_at: string | null }>(
      `/api/schedules/${encodeURIComponent(id)}`,
      { method: 'PATCH', body: payload },
    ),
  deleteSchedule: (id: string) =>
    request<{ deleted: string }>(`/api/schedules/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    }),
  runSchedule: (id: string) =>
    request<{ started: boolean; task_id?: string; workflow_id?: string }>(
      `/api/schedules/${encodeURIComponent(id)}/run`,
      { method: 'POST' },
    ),

  assignments: () => request<{ assignments: Assignment[] }>('/api/assignments'),
  updateAssignment: (id: string, payload: Record<string, unknown>) =>
    request<{ assignment: Assignment }>(`/api/assignments/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: payload,
    }),
  draftAssignment: (id: string, format = 'pdf', notes = '') =>
    request<{ task_id: string; assignment_id: string }>(
      `/api/assignments/${encodeURIComponent(id)}/draft`,
      { method: 'POST', body: { format, notes } },
    ),

  liveState: () => request<LiveState>('/api/live/state'),
  computerState: () => request<ComputerEngineState>('/api/computer/state'),
  computerObserve: (options: { includeElements?: boolean; includeOcr?: boolean; includeScreenshot?: boolean } = {}) =>
    request<{ observation: ComputerUseObservation }>(
      `/api/computer/observe${query({
        include_elements: options.includeElements ?? true,
        include_ocr: options.includeOcr ?? false,
        include_screenshot: options.includeScreenshot ?? true,
      })}`,
      { timeoutMs: 30_000 },
    ),
  desktopWindows: () => request<DesktopWindows>('/api/system/windows', { timeoutMs: 20_000 }),
  captureScreenshot: () =>
    request<{ data: Record<string, unknown>; evidence: string }>('/api/live/screenshot', {
      method: 'POST',
      timeoutMs: 45_000,
    }),
  metrics: () => request<SystemMetrics>('/api/system/metrics'),
  processes: (limit = 30) =>
    request<{ processes: SystemProcess[] }>(`/api/system/processes${query({ limit })}`),
  osInfo: () => request<{ data: Record<string, unknown> }>('/api/system/os'),
  applications: () => request<{ data: Record<string, unknown> }>('/api/system/applications'),

  browserState: () => request<BrowserState>('/api/browser/state'),
  browserOpen: (url: string) =>
    request<{ data: Record<string, unknown>; evidence: string }>('/api/browser/open', {
      method: 'POST',
      body: { url },
      timeoutMs: 60_000,
    }),
  browserClose: (tabId: string) =>
    request<{ data: Record<string, unknown> }>('/api/browser/close', {
      method: 'POST',
      body: { tab_id: tabId },
    }),
  browserScreenshot: (tabId?: string) =>
    request<{ data: { path: string; url: string; title: string; size: number } }>(
      '/api/browser/screenshot',
      { method: 'POST', body: { tab_id: tabId ?? null, full_page: false }, timeoutMs: 45_000 },
    ),

  voiceStatus: () => request<VoiceStatus>('/api/voice/status'),
  transcribe: async (blob: Blob, language?: string) => {
    const form = new FormData()
    form.append('file', blob, 'dictation.webm')
    return request<{ text: string; language?: string; duration?: number }>(
      `/api/voice/transcribe${query({ language })}`,
      { method: 'POST', body: form, timeoutMs: 120_000 },
    )
  },
  speak: (text: string) =>
    request<{ path?: string; url?: string }>('/api/voice/speak', {
      method: 'POST',
      body: { text },
      timeoutMs: 60_000,
    }),
  audioUrl: (filename: string) =>
    `${API_BASE}/api/voice/audio/${encodeURIComponent(filename)}`,
  screenshotUrl: (path: string) =>
    path.startsWith('http') ? path : `${API_BASE}${path}`,
}

export interface FileEntryLike {
  name: string
  path: string
  size?: number
  modified: string
  relative_path?: string
  category?: string
}
