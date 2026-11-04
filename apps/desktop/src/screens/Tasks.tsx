import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  ListChecks,
  MessageCircle,
  RotateCcw,
  ShieldQuestion,
  Square,
  XCircle,
} from 'lucide-react'
import { api, errorMessage } from '@/lib/api'
import type { Task, TaskDetail } from '@/lib/types'
import { useAppStore } from '@/stores/app'
import { useChatStore } from '@/stores/chat'
import { toast } from '@/stores/toast'
import { PageHeader } from '@/components/AppShell'
import { Mascot } from '@/components/Mascot'
import { TaskTimeline } from '@/components/TaskTimeline'
import { Badge, Button, Card, EmptyState, Modal, Progress, Skeleton, Tabs } from '@/components/ui'
import {
  formatDateTime,
  formatDuration,
  humanizeTool,
  isActiveStatus,
  pluralize,
  relativeTime,
  statusLabel,
  taskTone,
  truncate,
} from '@/lib/utils'

type TaskFilter = 'all' | 'running' | 'waiting' | 'queued' | 'completed' | 'failed' | 'cancelled'

const FILTERS: Array<{ value: TaskFilter; label: string }> = [
  { value: 'all', label: 'All' },
  { value: 'running', label: 'Running' },
  { value: 'waiting', label: 'Waiting approval' },
  { value: 'queued', label: 'Queued' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
  { value: 'cancelled', label: 'Cancelled' },
]

const FILTER_MATCH: Record<Exclude<TaskFilter, 'all'>, string[]> = {
  running: ['running', 'planning'],
  waiting: ['waiting_for_approval'],
  queued: ['queued'],
  completed: ['completed'],
  failed: ['failed'],
  cancelled: ['cancelled'],
}

function progressFor(task: Task): number {
  if (task.status === 'completed') return 100
  const plan = Array.isArray(task.plan) ? task.plan.length : 0
  if (!plan) return isActiveStatus(task.status) ? 20 : 0
  return Math.max(0, Math.min(100, Math.round((task.current_step / plan) * 100)))
}

export function TasksScreen() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const selectedId = searchParams.get('task')

  const [filter, setFilter] = useState<TaskFilter>('all')
  const [tasks, setTasks] = useState<Task[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [detail, setDetail] = useState<TaskDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [working, setWorking] = useState(false)

  const approvals = useAppStore((state) => state.approvals)
  const decideApproval = useAppStore((state) => state.decideApproval)
  const refreshApprovals = useAppStore((state) => state.refreshApprovals)
  const send = useChatStore((state) => state.send)
  const openConversation = useChatStore((state) => state.openConversation)

  const loadTasks = useCallback(async () => {
    try {
      const response = await api.tasks({ limit: 200 })
      setTasks(response.tasks)
      setError(null)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadTasks()
    void refreshApprovals()
    const interval = window.setInterval(() => void loadTasks(), 5000)
    return () => window.clearInterval(interval)
  }, [loadTasks, refreshApprovals])

  const loadDetail = useCallback(async (id: string) => {
    setDetailLoading(true)
    try {
      const response = await api.task(id)
      setDetail(response)
    } catch (err) {
      toast.error('Could not load that task', errorMessage(err))
      setDetail(null)
    } finally {
      setDetailLoading(false)
    }
  }, [])

  useEffect(() => {
    if (selectedId) void loadDetail(selectedId)
    else setDetail(null)
  }, [selectedId, loadDetail])

  useEffect(() => {
    if (!selectedId || !detail || !isActiveStatus(detail.task.status)) return
    const interval = window.setInterval(() => void loadDetail(selectedId), 4000)
    return () => window.clearInterval(interval)
  }, [selectedId, detail, loadDetail])

  const counts = useMemo(() => {
    const result: Record<TaskFilter, number> = {
      all: tasks.length,
      running: 0,
      waiting: 0,
      queued: 0,
      completed: 0,
      failed: 0,
      cancelled: 0,
    }
    for (const task of tasks) {
      for (const [key, statuses] of Object.entries(FILTER_MATCH)) {
        if (statuses.includes(task.status)) result[key as TaskFilter] += 1
      }
    }
    return result
  }, [tasks])

  const filtered = useMemo(() => {
    if (filter === 'all') return tasks
    return tasks.filter((task) => FILTER_MATCH[filter].includes(task.status))
  }, [tasks, filter])

  const cancelTask = async (task: Task) => {
    setWorking(true)
    try {
      await api.cancelTask(task.id)
      toast.success('Task cancelled')
      await loadTasks()
      if (selectedId === task.id) await loadDetail(task.id)
    } catch (err) {
      toast.error('Could not cancel that task', errorMessage(err))
    } finally {
      setWorking(false)
    }
  }

  const retryInChat = async (task: Task) => {
    closeDetail()
    navigate('/chat')
    await send(task.instruction)
  }

  const openRelatedConversation = (task: Task) => {
    if (!task.conversation_id) return
    void openConversation(task.conversation_id)
    navigate(`/chat/${task.conversation_id}`)
  }

  const closeDetail = () => {
    const next = new URLSearchParams(searchParams)
    next.delete('task')
    setSearchParams(next, { replace: true })
    setDetail(null)
  }

  const decide = async (approvalId: string, approved: boolean) => {
    setWorking(true)
    await decideApproval(approvalId, approved)
    if (selectedId) await loadDetail(selectedId)
    setWorking(false)
  }

  return (
    <div>
      <PageHeader
        eyebrow="My Tasks"
        title="Everything Mochi is doing"
        description="Running work, approvals, finished jobs and honest failures — all from the backend."
        icon={<ListChecks className="size-5" aria-hidden />}
        actions={
          <Button variant="outline" size="sm" onClick={() => void loadTasks()} loading={loading} icon={<RotateCcw className="size-4" aria-hidden />}>
            Refresh
          </Button>
        }
      />

      {approvals.length > 0 && (
        <Card className="mb-4 border-warning/30 bg-warning-soft/40 p-4">
          <div className="flex flex-wrap items-center gap-3">
            <span className="grid size-10 place-items-center rounded-2xl bg-warning-soft text-warning">
              <ShieldQuestion className="size-5" aria-hidden />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-extrabold text-ink">
                {approvals.length} {pluralize(approvals.length, 'action')} waiting for your approval
              </p>
              <p className="text-xs text-ink-soft">Mochi is paused on these until you approve or reject them.</p>
            </div>
            <Button size="sm" variant="soft" onClick={() => void refreshApprovals()}>
              Review
            </Button>
          </div>
        </Card>
      )}

      {error && <div className="mb-4 rounded-2xl border border-danger/25 bg-danger-soft px-4 py-3 text-sm text-danger">{error}</div>}

      <Tabs
        items={FILTERS.map((item) => ({ ...item, count: counts[item.value] }))}
        value={filter}
        onChange={setFilter}
        ariaLabel="Filter tasks by status"
        className="mb-4"
      />

      {loading && tasks.length === 0 ? (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          <Skeleton className="h-40" />
          <Skeleton className="h-40" />
          <Skeleton className="h-40" />
        </div>
      ) : filtered.length === 0 ? (
        <EmptyState
          mascot={<Mascot state="sleeping" size={110} />}
          title={filter === 'all' ? 'No tasks yet' : `No ${FILTERS.find((item) => item.value === filter)?.label.toLowerCase()} tasks`}
          description="Ask Mochi for something in Chat, or run a workflow. Real tasks will show up here."
          action={
            <Button variant="soft" size="sm" onClick={() => navigate('/chat')} icon={<MessageCircle className="size-4" aria-hidden />}>
              Open chat
            </Button>
          }
        />
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {filtered.map((task) => (
            <TaskCard
              key={task.id}
              task={task}
              onOpen={() => setSearchParams({ task: task.id })}
              onCancel={() => void cancelTask(task)}
              working={working}
            />
          ))}
        </div>
      )}

      <Modal
        open={Boolean(selectedId)}
        onClose={closeDetail}
        title="Task details"
        description={detail ? truncate(detail.task.instruction, 120) : 'Loading the real execution record…'}
        size="xl"
        footer={
          detail ? (
            <>
              {detail.task.conversation_id && (
                <Button variant="ghost" onClick={() => openRelatedConversation(detail.task)} icon={<MessageCircle className="size-4" aria-hidden />}>
                  Open conversation
                </Button>
              )}
              <Button
                variant="outline"
                onClick={() => void retryInChat(detail.task)}
                icon={<RotateCcw className="size-4" aria-hidden />}
                title="Starts a new chat request with the same instruction"
              >
                Ask again
              </Button>
              {isActiveStatus(detail.task.status) && (
                <Button variant="danger" loading={working} onClick={() => void cancelTask(detail.task)} icon={<Square className="size-3.5 fill-current" aria-hidden />}>
                  Cancel task
                </Button>
              )}
            </>
          ) : undefined
        }
      >
        {detailLoading && !detail ? (
          <div className="space-y-3">
            <Skeleton className="h-20" />
            <Skeleton className="h-40" />
          </div>
        ) : detail ? (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={taskTone(detail.task.status)} dot>
                {statusLabel(detail.task.status)}
              </Badge>
              <Badge tone="neutral">{detail.task.permission_mode}</Badge>
              <Badge tone="neutral">{detail.task.trigger}</Badge>
              <span className="text-[11px] text-ink-faint">Created {formatDateTime(detail.task.created_at)}</span>
            </div>

            {detail.task.objective && (
              <div className="rounded-2xl border border-line bg-surface-muted/40 p-3.5">
                <p className="text-[10px] font-extrabold uppercase tracking-[0.1em] text-ink-faint">Objective</p>
                <p className="mt-1 text-sm font-semibold text-ink">{detail.task.objective}</p>
              </div>
            )}

            <div>
              <p className="mb-2 text-[10px] font-extrabold uppercase tracking-[0.1em] text-ink-faint">Plan</p>
              <TaskTimeline
                steps={detail.task.plan}
                currentStep={detail.task.current_step}
                status={detail.task.status}
                emptyLabel="No plan was recorded for this task."
              />
            </div>

            <div className="grid gap-2 sm:grid-cols-3">
              <div className="rounded-2xl bg-surface-muted p-3">
                <p className="text-[10px] font-bold uppercase text-ink-faint">Tool calls</p>
                <p className="text-lg font-extrabold text-ink">{detail.executions.length}</p>
              </div>
              <div className="rounded-2xl bg-success-soft/60 p-3">
                <p className="text-[10px] font-bold uppercase text-ink-faint">Succeeded</p>
                <p className="text-lg font-extrabold text-success">{detail.executions.filter((item) => item.ok).length}</p>
              </div>
              <div className="rounded-2xl bg-danger-soft/60 p-3">
                <p className="text-[10px] font-bold uppercase text-ink-faint">Failed</p>
                <p className="text-lg font-extrabold text-danger">{detail.executions.filter((item) => !item.ok).length}</p>
              </div>
            </div>

            {detail.task.result && (
              <div className="rounded-2xl border border-success/20 bg-success-soft/40 p-3.5">
                <p className="text-[10px] font-extrabold uppercase tracking-[0.1em] text-success">Result</p>
                <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-ink">{detail.task.result}</p>
              </div>
            )}

            {detail.task.error && (
              <div className="rounded-2xl border border-danger/25 bg-danger-soft/50 p-3.5 text-danger">
                <p className="flex items-center gap-1.5 text-[10px] font-extrabold uppercase tracking-[0.1em]">
                  <AlertTriangle className="size-3.5" aria-hidden /> Error
                </p>
                <p className="mt-1 text-sm leading-relaxed">{detail.task.error}</p>
              </div>
            )}

            <div>
              <p className="mb-2 text-[10px] font-extrabold uppercase tracking-[0.1em] text-ink-faint">Execution history</p>
              {detail.executions.length === 0 ? (
                <p className="rounded-xl border border-dashed border-line px-3 py-4 text-xs text-ink-faint">
                  No tools have run for this task.
                </p>
              ) : (
                <div className="space-y-1.5">
                  {detail.executions.map((execution) => (
                    <details key={execution.id} className="rounded-xl border border-line bg-surface-strong/60 px-3 py-2">
                      <summary className="ring-focus cursor-pointer list-none">
                        <span className="flex flex-wrap items-center gap-2">
                          {execution.ok ? (
                            <CheckCircle2 className="size-3.5 text-success" aria-hidden />
                          ) : (
                            <XCircle className="size-3.5 text-danger" aria-hidden />
                          )}
                          <span className="text-xs font-bold text-ink">{humanizeTool(execution.tool_name)}</span>
                          <span className="font-mono text-[10px] text-ink-faint">{execution.tool_name}</span>
                          <span className="ml-auto text-[10px] text-ink-faint">{formatDuration(execution.duration_ms)}</span>
                        </span>
                      </summary>
                      <div className="mt-2 space-y-2 border-t border-line pt-2 text-[11px]">
                        <pre className="mochi-scroll max-h-40 overflow-auto rounded-lg bg-surface-muted p-2 font-mono text-[10px] text-ink-soft">
                          {JSON.stringify(execution.arguments, null, 2)}
                        </pre>
                        {execution.error && <p className="rounded-lg bg-danger-soft px-2 py-1.5 text-danger">{execution.error}</p>}
                        <p className="text-ink-faint">
                          Permission: {execution.permission} · {formatDateTime(execution.created_at)}
                        </p>
                      </div>
                    </details>
                  ))}
                </div>
              )}
            </div>

            <div>
              <p className="mb-2 text-[10px] font-extrabold uppercase tracking-[0.1em] text-ink-faint">Approvals</p>
              {detail.approvals.length === 0 ? (
                <p className="rounded-xl border border-dashed border-line px-3 py-4 text-xs text-ink-faint">
                  No approval was requested for this task.
                </p>
              ) : (
                <div className="space-y-1.5">
                  {detail.approvals.map((approval) => (
                    <div key={approval.id} className="rounded-xl border border-line bg-surface-strong/60 p-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge tone={approval.status === 'pending' ? 'warning' : approval.status === 'approved' ? 'success' : 'danger'}>
                          {approval.status}
                        </Badge>
                        <span className="text-xs font-bold text-ink">{approval.tool_name}</span>
                        <span className="ml-auto text-[10px] text-ink-faint">{relativeTime(approval.requested_at)}</span>
                      </div>
                      <p className="mt-1 text-[11px] text-ink-soft">{approval.reason}</p>
                      {approval.status === 'pending' && (
                        <div className="mt-2 flex gap-2">
                          <Button size="sm" variant="success" loading={working} onClick={() => void decide(approval.id, true)}>
                            Approve
                          </Button>
                          <Button size="sm" variant="danger" loading={working} onClick={() => void decide(approval.id, false)}>
                            Reject
                          </Button>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        ) : (
          <EmptyState mascot={<Mascot state="error" size={90} />} title="Task unavailable" description="The backend could not return this task." />
        )}
      </Modal>
    </div>
  )
}

function TaskCard({
  task,
  onOpen,
  onCancel,
  working,
}: {
  task: Task
  onOpen: () => void
  onCancel: () => void
  working: boolean
}) {
  const planLength = Array.isArray(task.plan) ? task.plan.length : 0
  const active = isActiveStatus(task.status)
  const statistics = task.metadata?.statistics

  return (
    <Card className="group flex flex-col p-4">
      <button type="button" onClick={onOpen} className="ring-focus flex flex-1 flex-col rounded-2xl text-left">
        <div className="flex items-start justify-between gap-2">
          <Badge tone={taskTone(task.status)} dot>
            {statusLabel(task.status)}
          </Badge>
          <span className="flex items-center gap-1 text-[10px] font-semibold text-ink-faint">
            <Clock className="size-3" aria-hidden /> {relativeTime(task.created_at)}
          </span>
        </div>
        <p className="mt-2 line-clamp-2 text-sm font-extrabold leading-snug text-ink">{task.instruction}</p>
        {task.objective && <p className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-ink-faint">{task.objective}</p>}
        <div className="mt-3">
          <div className="mb-1 flex justify-between text-[10px] font-bold text-ink-faint">
            <span>
              {active ? `Step ${Math.min(task.current_step + 1, Math.max(planLength, 1))} of ${Math.max(planLength, 1)}` : statusLabel(task.status)}
            </span>
            <span>{progressFor(task)}%</span>
          </div>
          <Progress value={progressFor(task)} tone={taskTone(task.status)} label="Task progress" />
        </div>
        <div className="mt-2.5 flex flex-wrap gap-1">
          <span className="rounded-md bg-surface-muted px-1.5 py-0.5 text-[10px] font-semibold text-ink-faint">{task.trigger}</span>
          <span className="rounded-md bg-surface-muted px-1.5 py-0.5 text-[10px] font-semibold text-ink-faint">{task.permission_mode}</span>
          {statistics && typeof statistics.tools === 'number' && (
            <span className="rounded-md bg-surface-muted px-1.5 py-0.5 text-[10px] font-semibold text-ink-faint">
              {statistics.tools} tools
            </span>
          )}
        </div>
      </button>
      <div className="mt-3 flex items-center justify-between gap-2 border-t border-line pt-2.5">
        <button
          type="button"
          onClick={onOpen}
          className="ring-focus rounded-lg text-[11px] font-extrabold text-accent-strong hover:underline"
        >
          Open details
        </button>
        {active ? (
          <Button
            size="sm"
            variant="ghost"
            className="text-danger hover:bg-danger-soft"
            loading={working}
            onClick={onCancel}
            icon={<Square className="size-3 fill-current" aria-hidden />}
          >
            Cancel
          </Button>
        ) : (
          <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-ink-faint">
            {task.status === 'completed' ? <CheckCircle2 className="size-3 text-success" aria-hidden /> : null}
            {task.status === 'failed' ? <AlertTriangle className="size-3 text-danger" aria-hidden /> : null}
            {task.status === 'failed' ? 'needs attention' : task.completed_at ? formatDateTime(task.completed_at) : ''}
          </span>
        )}
      </div>
    </Card>
  )
}
