export type PermissionMode = 'observe' | 'assisted' | 'autonomous'
export type TaskStatus =
  | 'queued'
  | 'planning'
  | 'running'
  | 'waiting_for_approval'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'blocked'
  | string

export interface ProviderStatus {
  provider: string
  model: string
  base_url: string
  configured: boolean
  has_api_key: boolean
}

export interface VoiceStatus {
  enabled: boolean
  push_to_talk: boolean
  speak_responses: boolean
  wake_word: string
  whisper_installed: boolean
  whisper_model: string
  whisper_loaded: boolean
  tts_installed: boolean
  browser_tts_available: boolean
  note: string
}

export interface Health {
  status: string
  services: {
    permission_mode: PermissionMode
    emergency_stopped: boolean
    running_tasks: string[]
    browser_running: boolean
    tools: number
    provider: ProviderStatus
    data_dir: string
    voice: VoiceStatus
  }
  version: string
}

export interface LlmSettings {
  provider: string
  model: string
  base_url: string
  temperature: number
  has_api_key?: boolean
}

export interface SettingsSnapshot {
  permission_mode: PermissionMode
  llm: LlmSettings
  allowed_directories: string[]
  browser: { headless: boolean }
  voice: VoiceStatus & { whisper_device?: string }
  lms: {
    base_url: string
    institution: string
    courses: string[]
    download_dir: string
    document_format: string
    selectors: Record<string, string>
  }
  safety: {
    auto_approve_tools: string[]
    confirm_destructive: boolean
    blocked_websites: string[]
  }
  provider_status?: ProviderStatus
  emergency_stopped?: boolean
  tools_count?: number
  runtime?: {
    data_dir: string
    log_dir: string
    host: string
    port: number
    version: string
    permission_mode: string
  }
  app?: {
    onboarded?: boolean
    version?: string
    data_dir?: string
    log_dir?: string
    environment?: string
  }
}

export interface ModelInfo {
  id: string
  name: string
  size?: number | null
}

export interface PlanStep {
  title: string
  tools?: string[]
}

export interface TaskStatistics {
  tools?: number
  succeeded?: number
  failed?: number
  unverified?: number
  [key: string]: number | undefined
}

export interface ExecutionRecord {
  step_index?: number
  tool_name?: string
  tool?: string
  arguments?: Record<string, unknown>
  ok?: boolean
  error?: string | null
  permission?: string
  duration_ms?: number
  attempts?: number
  evidence?: string
  verified?: boolean
  created_at?: string
}

export interface Task {
  id: string
  conversation_id: string | null
  instruction: string
  objective: string
  plan: PlanStep[] | string[]
  status: TaskStatus
  current_step: number
  permission_mode: PermissionMode
  trigger: string
  result: string | null
  error: string | null
  metadata: {
    statistics?: TaskStatistics
    executions?: ExecutionRecord[]
    workflow_id?: string
    workflow_run_id?: string
    [key: string]: unknown
  }
  created_at: string | null
  started_at: string | null
  completed_at: string | null
  cancelled_at: string | null
}

export interface TaskStep {
  id: string
  index: number
  kind: string
  title: string
  status: string
  detail: Record<string, unknown> | null
  started_at: string | null
  completed_at: string | null
}

export interface ToolExecutionRecord {
  id: string
  step_index: number
  tool_name: string
  arguments: Record<string, unknown>
  ok: boolean
  error: string | null
  permission: string
  duration_ms: number
  created_at: string
}

export interface Approval {
  id: string
  task_id: string
  tool_name: string
  arguments: Record<string, unknown>
  reason: string
  permission: string
  status: 'pending' | 'approved' | 'denied' | 'expired' | 'cancelled'
  decision_note: string | null
  requested_at: string | null
  decided_at: string | null
  instruction: string | null
}

export interface TaskDetail {
  task: Task
  steps: TaskStep[]
  executions: ToolExecutionRecord[]
  approvals: Approval[]
}

export interface Conversation {
  id: string
  title: string
  created_at: string
  updated_at: string
  message_count: number
  preview: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  attachments: Array<{ name: string; path: string; mime: string; size: number }>
  created_at: string
}

export interface ChatSendResult {
  task_id: string
  conversation_id: string
  message_id: string
  task: Task
}

export interface WorkflowStep {
  id?: string
  index?: number
  name: string
  kind: 'instruction' | 'tool' | 'approval' | string
  config: Record<string, unknown>
}

export interface Workflow {
  id: string
  name: string
  description: string
  enabled: boolean
  steps: WorkflowStep[]
  created_at: string
}

export interface WorkflowRun {
  id: string
  status: string
  results: Array<{
    index: number
    name: string
    status: string
    reason?: string
    error?: string
    result?: Record<string, unknown>
  }>
  error: string | null
  started_at: string | null
  finished_at: string | null
}

export interface Schedule {
  id: string
  name: string
  instruction: string
  workflow_id: string | null
  schedule_type: 'once' | 'interval' | 'daily'
  run_at: string | null
  interval_minutes: number | null
  time_of_day: string | null
  next_run_at: string | null
  enabled: boolean
  last_run_at: string | null
  last_task_id: string | null
}

export interface MemoryRecord {
  id: string
  kind: string
  key: string
  value: string
  tags: string
  source: string
  pinned: boolean
  created_at: string
  updated_at: string
}

export interface Assignment {
  id: string
  subject: string
  title: string
  instructions: string
  deadline: string | null
  lms_url: string
  source_url: string
  resources: Array<Record<string, unknown>>
  generated_files: Array<Record<string, unknown>>
  status: string
  submission_status: string
  created_at: string
  updated_at: string
}

export interface FileEntry {
  name: string
  path: string
  is_dir: boolean
  size: number | null
  modified: string
}

export interface DirectoryListing {
  path: string
  entries: FileEntry[]
  count: number
}

export interface FileRoots {
  roots: string[]
  workspace: string
  uploads: string
}

export interface BrowserTab {
  tab_id: string
  url: string
  title: string
  active?: boolean
}

export interface BrowserState {
  running: boolean
  tabs: BrowserTab[]
  active: BrowserTab | null
}

export interface DesktopWindow {
  handle: number
  title: string
  class_name: string
  pid: number
  process?: string
  rect?: { left: number; top: number; right: number; bottom: number }
}

export interface DesktopWindows {
  active: DesktopWindow
  windows: DesktopWindow[]
  count: number
}

export interface LiveState {
  browser: BrowserState
  active_task: {
    id: string
    instruction: string
    status: string
    current_step: number
    plan: PlanStep[] | string[]
  } | null
  latest_screenshot: string | null
  permission_mode: PermissionMode
  emergency_stopped: boolean
  running_tasks: string[]
  events: AppEvent[]
  voice: VoiceStatus
}

export interface SystemMetrics {
  cpu_percent: number
  cpu_cores: number
  ram_percent: number
  ram_used_gb: number
  ram_total_gb: number
  volumes: Array<{
    device: string
    mountpoint: string
    percent: number
    free_gb: number
    total_gb: number
  }>
  battery: { percent: number; plugged_in: boolean } | null
  uptime_seconds: number
}

export interface SystemProcess {
  pid?: number
  name?: string
  memory_mb?: number
  [key: string]: unknown
}

export interface AppEvent {
  seq: number
  type: string
  ts: string
  payload: Record<string, unknown>
}

export interface ToolInfo {
  name: string
  description: string
  permission: string
  requires_approval?: boolean
  schema?: Record<string, unknown>
}

export interface ErrorDetail {
  message?: string
  hint?: string
  retryable?: boolean
  requires_approval?: boolean
  tool?: string
  [key: string]: unknown
}
