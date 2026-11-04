import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  CalendarClock,
  Clock,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Repeat,
  Sparkles,
  Trash2,
  Workflow as WorkflowIcon,
} from 'lucide-react'
import { api, errorMessage } from '@/lib/api'
import type { Schedule as ScheduleJob, Workflow } from '@/lib/types'
import { firstIssue, scheduleSchema, type ScheduleFormValues } from '@/lib/schemas'
import { toast } from '@/stores/toast'
import { PageHeader } from '@/components/AppShell'
import { Mascot } from '@/components/Mascot'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  Input,
  Modal,
  SectionHeading,
  Select,
  Skeleton,
  Switch,
  Textarea,
} from '@/components/ui'
import { cn, formatDateTime, formatTime, relativeTime } from '@/lib/utils'

type ScheduleType = 'once' | 'interval' | 'daily'

interface FormState {
  name: string
  instruction: string
  workflow_id: string
  schedule_type: ScheduleType
  run_at: string
  interval_minutes: string
  time_of_day: string
  enabled: boolean
}

const EMPTY_FORM: FormState = {
  name: '',
  instruction: '',
  workflow_id: '',
  schedule_type: 'once',
  run_at: '',
  interval_minutes: '60',
  time_of_day: '09:00',
  enabled: true,
}

const TYPE_META: Record<ScheduleType, { label: string; hint: string; icon: typeof Clock }> = {
  once: { label: 'One time', hint: 'Runs once at a specific moment.', icon: Clock },
  interval: { label: 'Repeating', hint: 'Runs again every few minutes.', icon: Repeat },
  daily: { label: 'Every day', hint: 'Runs once each day at a set time.', icon: CalendarClock },
}

function toLocalInputValue(iso: string | null): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const offset = date.getTimezoneOffset() * 60_000
  return new Date(date.getTime() - offset).toISOString().slice(0, 16)
}

function describeType(job: ScheduleJob): string {
  if (job.schedule_type === 'interval') {
    const minutes = job.interval_minutes ?? 0
    if (minutes >= 1440) return `Every ${Math.round(minutes / 1440)} day(s)`
    if (minutes >= 60) return `Every ${Math.round((minutes / 60) * 10) / 10} hour(s)`
    return `Every ${minutes} minute(s)`
  }
  if (job.schedule_type === 'daily') return `Every day at ${job.time_of_day ?? '—'}`
  return job.run_at ? `Once on ${formatDateTime(job.run_at)}` : 'One time'
}

export function ScheduleScreen() {
  const [jobs, setJobs] = useState<ScheduleJob[]>([])
  const [workflows, setWorkflows] = useState<Workflow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<ScheduleJob | null>(null)
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState<ScheduleJob | null>(null)
  const [removing, setRemoving] = useState(false)

  const load = useCallback(async () => {
    try {
      const [scheduleResponse, workflowResponse] = await Promise.all([
        api.schedules(),
        api.workflows(),
      ])
      setJobs(scheduleResponse.schedules)
      setWorkflows(workflowResponse.workflows)
      setError(null)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
    const interval = window.setInterval(() => void load(), 15_000)
    return () => window.clearInterval(interval)
  }, [load])

  const workflowName = useMemo(() => {
    const map = new Map(workflows.map((workflow) => [workflow.id, workflow.name]))
    return (id: string | null) => (id ? map.get(id) ?? 'Missing workflow' : null)
  }, [workflows])

  const sorted = useMemo(
    () =>
      [...jobs].sort((a, b) => {
        if (a.enabled !== b.enabled) return a.enabled ? -1 : 1
        const aNext = a.next_run_at ? new Date(a.next_run_at).getTime() : Number.MAX_SAFE_INTEGER
        const bNext = b.next_run_at ? new Date(b.next_run_at).getTime() : Number.MAX_SAFE_INTEGER
        return aNext - bNext
      }),
    [jobs],
  )

  const openCreate = () => {
    setEditing(null)
    setForm({
      ...EMPTY_FORM,
      workflow_id: workflows[0]?.id ?? '',
      run_at: toLocalInputValue(new Date(Date.now() + 60 * 60_000).toISOString()),
    })
    setErrors({})
    setFormOpen(true)
  }

  const openEdit = (job: ScheduleJob) => {
    setEditing(job)
    setForm({
      name: job.name,
      instruction: job.instruction ?? '',
      workflow_id: job.workflow_id ?? '',
      schedule_type: job.schedule_type,
      run_at: toLocalInputValue(job.run_at),
      interval_minutes: String(job.interval_minutes ?? 60),
      time_of_day: job.time_of_day ?? '09:00',
      enabled: job.enabled,
    })
    setErrors({})
    setFormOpen(true)
  }

  const submit = async () => {
    const candidate = {
      name: form.name.trim(),
      instruction: form.workflow_id ? '' : form.instruction.trim(),
      workflow_id: form.workflow_id || null,
      schedule_type: form.schedule_type,
      run_at: form.schedule_type === 'once' ? form.run_at : '',
      interval_minutes:
        form.schedule_type === 'interval' ? Number(form.interval_minutes) : undefined,
      time_of_day: form.schedule_type === 'daily' ? form.time_of_day : '',
    }
    const parsed = scheduleSchema.safeParse(candidate)
    if (!parsed.success) {
      setErrors(firstIssue(parsed.error))
      return
    }

    const values: ScheduleFormValues = parsed.data
    const payload = {
      name: values.name,
      instruction: values.workflow_id ? '' : values.instruction,
      workflow_id: values.workflow_id || null,
      schedule_type: values.schedule_type,
      run_at:
        values.schedule_type === 'once' && values.run_at
          ? new Date(values.run_at).toISOString()
          : null,
      interval_minutes:
        values.schedule_type === 'interval' ? values.interval_minutes ?? null : null,
      time_of_day: values.schedule_type === 'daily' ? values.time_of_day || null : null,
      enabled: form.enabled,
    }

    setSaving(true)
    setErrors({})
    try {
      if (editing) {
        await api.updateSchedule(editing.id, payload)
        toast.success('Schedule updated')
      } else {
        await api.createSchedule(payload)
        toast.success('Schedule created', 'Mochi will run it at the time you chose.')
      }
      setFormOpen(false)
      await load()
    } catch (err) {
      toast.error('Could not save that schedule', errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  const toggle = async (job: ScheduleJob) => {
    setBusyId(job.id)
    try {
      const response = await api.updateSchedule(job.id, { enabled: !job.enabled })
      setJobs((current) =>
        current.map((item) =>
          item.id === job.id
            ? { ...item, enabled: !job.enabled, next_run_at: response.next_run_at ?? item.next_run_at }
            : item,
        ),
      )
      toast.success(job.enabled ? 'Schedule paused' : 'Schedule resumed')
    } catch (err) {
      toast.error('Could not change that schedule', errorMessage(err))
    } finally {
      setBusyId(null)
    }
  }

  const runNow = async (job: ScheduleJob) => {
    setBusyId(job.id)
    try {
      const result = await api.runSchedule(job.id)
      toast.success(
        result.workflow_id ? 'Workflow started' : 'Task started',
        result.task_id ? `Task ${result.task_id.slice(-8)} is running.` : 'Follow it in Live Activity.',
      )
      await load()
    } catch (err) {
      toast.error('Could not run that schedule', errorMessage(err))
    } finally {
      setBusyId(null)
    }
  }

  const remove = async () => {
    if (!deleting) return
    setRemoving(true)
    try {
      await api.deleteSchedule(deleting.id)
      setJobs((current) => current.filter((item) => item.id !== deleting.id))
      setDeleting(null)
      toast.success('Schedule deleted')
    } catch (err) {
      toast.error('Could not delete that schedule', errorMessage(err))
    } finally {
      setRemoving(false)
    }
  }

  const enabledCount = jobs.filter((job) => job.enabled).length
  const nextJob = sorted.find((job) => job.enabled && job.next_run_at)

  return (
    <div>
      <PageHeader
        eyebrow="Schedule"
        title="Little plans for later"
        description="Ask Mochi to do something at a specific time, every day, or on a repeating rhythm."
        icon={<CalendarClock className="size-5" aria-hidden />}
        actions={
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void load()}
              loading={loading}
              icon={<RefreshCw className="size-4" aria-hidden />}
            >
              Refresh
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={openCreate}
              icon={<Plus className="size-4" aria-hidden />}
            >
              New schedule
            </Button>
          </>
        }
      />

      {error && (
        <div className="mb-4 rounded-2xl border border-danger/25 bg-danger-soft px-4 py-3 text-sm text-danger">
          {error}
        </div>
      )}

      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <Card className="flex items-center gap-3 p-4">
          <span className="grid size-10 place-items-center rounded-2xl bg-accent-soft text-accent-strong">
            <CalendarClock className="size-5" aria-hidden />
          </span>
          <div>
            <p className="text-[11px] font-bold uppercase tracking-[0.08em] text-ink-faint">Schedules</p>
            <p className="text-lg font-extrabold text-ink">{jobs.length}</p>
          </div>
        </Card>
        <Card className="flex items-center gap-3 p-4">
          <span className="grid size-10 place-items-center rounded-2xl bg-success-soft text-success">
            <Play className="size-5 fill-current" aria-hidden />
          </span>
          <div>
            <p className="text-[11px] font-bold uppercase tracking-[0.08em] text-ink-faint">Active</p>
            <p className="text-lg font-extrabold text-ink">{enabledCount}</p>
          </div>
        </Card>
        <Card className="flex items-center gap-3 p-4">
          <span className="grid size-10 place-items-center rounded-2xl bg-info-soft text-info">
            <Clock className="size-5" aria-hidden />
          </span>
          <div className="min-w-0">
            <p className="text-[11px] font-bold uppercase tracking-[0.08em] text-ink-faint">Next up</p>
            <p className="truncate text-sm font-extrabold text-ink">
              {nextJob
                ? `${nextJob.name} · ${nextJob.next_run_at ? relativeTime(nextJob.next_run_at) : ''}`
                : 'Nothing scheduled'}
            </p>
          </div>
        </Card>
      </div>

      {loading && jobs.length === 0 ? (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 3 }).map((_, index) => (
            <Skeleton key={index} className="h-44" />
          ))}
        </div>
      ) : sorted.length === 0 ? (
        <EmptyState
          mascot={<Mascot state="sleeping" size={120} />}
          title="Nothing planned yet"
          description="Create a schedule for a morning check-in, a weekly tidy-up, or a single reminder."
          action={
            <Button
              variant="soft"
              size="sm"
              onClick={openCreate}
              icon={<Plus className="size-4" aria-hidden />}
            >
              Create your first schedule
            </Button>
          }
        />
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {sorted.map((job) => {
            const meta = TYPE_META[job.schedule_type]
            const Icon = meta.icon
            const busy = busyId === job.id
            const linked = workflowName(job.workflow_id)
            return (
              <Card
                key={job.id}
                className={cn('flex flex-col p-4', !job.enabled && 'opacity-75')}
              >
                <div className="flex items-start justify-between gap-2">
                  <span
                    className={cn(
                      'grid size-10 shrink-0 place-items-center rounded-2xl',
                      job.enabled ? 'bg-accent-soft text-accent-strong' : 'bg-surface-muted text-ink-faint',
                    )}
                  >
                    <Icon className="size-5" aria-hidden />
                  </span>
                  <div className="flex flex-wrap items-center justify-end gap-1">
                    <Badge tone={job.enabled ? 'accent' : 'neutral'} dot>
                      {job.enabled ? 'active' : 'paused'}
                    </Badge>
                    <Badge tone="neutral">{meta.label}</Badge>
                  </div>
                </div>

                <h3 className="mt-2.5 truncate text-base font-extrabold text-ink">{job.name}</h3>
                <p className="mt-0.5 line-clamp-2 min-h-[2.2rem] text-xs leading-relaxed text-ink-faint">
                  {linked ? `Runs the “${linked}” workflow.` : job.instruction || 'No instruction recorded.'}
                </p>

                <div className="mt-2 space-y-1 text-[11px] text-ink-soft">
                  <p className="flex items-center gap-1.5">
                    <Repeat className="size-3 shrink-0 text-ink-faint" aria-hidden />
                    {describeType(job)}
                  </p>
                  <p className="flex items-center gap-1.5">
                    <Clock className="size-3 shrink-0 text-ink-faint" aria-hidden />
                    {job.next_run_at ? (
                      <>
                        Next {relativeTime(job.next_run_at)}
                        <span className="text-ink-faint">
                          {job.schedule_type === 'daily'
                            ? `· ${formatTime(job.next_run_at)}`
                            : `· ${formatDateTime(job.next_run_at)}`}
                        </span>
                      </>
                    ) : (
                      'No future run planned'
                    )}
                  </p>
                  <p className="text-[10px] text-ink-faint">
                    {job.last_run_at ? `Last ran ${relativeTime(job.last_run_at)}` : 'Never run yet'}
                  </p>
                </div>

                <div className="mt-3 flex flex-wrap items-center gap-1.5 border-t border-line pt-2.5">
                  <Button
                    size="sm"
                    variant="primary"
                    onClick={() => void runNow(job)}
                    loading={busy}
                    icon={<Play className="size-3.5 fill-current" aria-hidden />}
                    title="Runs this schedule now through the real backend"
                  >
                    Run now
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => void toggle(job)}
                    disabled={busy}
                    icon={job.enabled ? <Pause className="size-3.5" aria-hidden /> : <Play className="size-3.5" aria-hidden />}
                    aria-label={job.enabled ? `Pause ${job.name}` : `Resume ${job.name}`}
                  >
                    {job.enabled ? 'Pause' : 'Resume'}
                  </Button>
                  <button
                    type="button"
                    onClick={() => openEdit(job)}
                    className="ring-focus rounded-lg px-1.5 py-1 text-[10px] font-extrabold text-ink-faint hover:bg-surface-muted hover:text-ink"
                  >
                    Edit
                  </button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="ml-auto text-danger hover:bg-danger-soft"
                    onClick={() => setDeleting(job)}
                    aria-label={`Delete ${job.name}`}
                    title="Delete"
                  >
                    <Trash2 className="size-3.5" aria-hidden />
                  </Button>
                </div>
              </Card>
            )
          })}
        </div>
      )}

      <Card className="mt-4 p-5">
        <SectionHeading
          title="How schedules behave"
          description="Everything here is stored and executed by the local backend."
          action={<Sparkles className="size-4 text-accent" aria-hidden />}
        />
        <ul className="mt-3 grid gap-2 text-xs leading-relaxed text-ink-soft sm:grid-cols-3">
          <li className="rounded-2xl border border-line bg-surface-muted/40 p-3">
            <span className="font-extrabold text-ink">Paused means paused.</span> A paused schedule keeps its
            settings but will not start new work.
          </li>
          <li className="rounded-2xl border border-line bg-surface-muted/40 p-3">
            <span className="font-extrabold text-ink">Permissions still apply.</span> Scheduled tasks follow the
            same approval rules as chats.
          </li>
          <li className="rounded-2xl border border-line bg-surface-muted/40 p-3">
            <span className="font-extrabold text-ink">Run now is real.</span> It starts a genuine task or workflow
            immediately.
          </li>
        </ul>
      </Card>

      <Modal
        open={formOpen}
        onClose={() => setFormOpen(false)}
        title={editing ? 'Edit schedule' : 'New schedule'}
        description="Choose a workflow or give Mochi an instruction, then pick when it should run."
        size="lg"
        footer={
          <>
            <Button variant="ghost" onClick={() => setFormOpen(false)} disabled={saving}>
              Cancel
            </Button>
            <Button onClick={() => void submit()} loading={saving}>
              {editing ? 'Save changes' : 'Create schedule'}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="Name" htmlFor="schedule-name" error={errors.name}>
            <Input
              id="schedule-name"
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              placeholder="Morning check-in"
              maxLength={200}
            />
          </Field>

          <Field
            label="What should run?"
            htmlFor="schedule-workflow"
            hint="Pick a workflow, or leave it empty and write an instruction below."
          >
            <Select
              id="schedule-workflow"
              value={form.workflow_id}
              onChange={(event) => setForm({ ...form, workflow_id: event.target.value })}
            >
              <option value="">Use a direct instruction</option>
              {workflows.map((workflow) => (
                <option key={workflow.id} value={workflow.id}>
                  {workflow.name}
                  {workflow.enabled ? '' : ' (paused)'}
                </option>
              ))}
            </Select>
          </Field>

          {!form.workflow_id && (
            <Field
              label="Instruction"
              htmlFor="schedule-instruction"
              error={errors.instruction}
              hint="Mochi will start a task with exactly this request."
            >
              <Textarea
                id="schedule-instruction"
                rows={3}
                value={form.instruction}
                onChange={(event) => setForm({ ...form, instruction: event.target.value })}
                placeholder="Check my LMS and summarise anything due this week."
                maxLength={5000}
              />
            </Field>
          )}

          <div>
            <p className="mb-1.5 block text-xs font-bold uppercase tracking-[0.08em] text-ink-soft">
              When
            </p>
            <div className="grid gap-2 sm:grid-cols-3">
              {(Object.keys(TYPE_META) as ScheduleType[]).map((type) => {
                const meta = TYPE_META[type]
                const Icon = meta.icon
                const selected = form.schedule_type === type
                return (
                  <button
                    key={type}
                    type="button"
                    onClick={() =>
                      setForm({
                        ...form,
                        schedule_type: type,
                        run_at:
                          type === 'once' && !form.run_at
                            ? toLocalInputValue(new Date(Date.now() + 60 * 60_000).toISOString())
                            : form.run_at,
                      })
                    }
                    aria-pressed={selected}
                    className={cn(
                      'ring-focus flex items-start gap-2 rounded-2xl border p-3 text-left transition-colors',
                      selected
                        ? 'border-accent/45 bg-accent-soft/60'
                        : 'border-line bg-surface-muted/30 hover:border-accent/25',
                    )}
                  >
                    <Icon
                      className={cn('mt-0.5 size-4 shrink-0', selected ? 'text-accent-strong' : 'text-ink-faint')}
                      aria-hidden
                    />
                    <span className="min-w-0">
                      <span className="block text-xs font-extrabold text-ink">{meta.label}</span>
                      <span className="mt-0.5 block text-[10px] leading-snug text-ink-faint">{meta.hint}</span>
                    </span>
                  </button>
                )
              })}
            </div>
          </div>

          {form.schedule_type === 'once' && (
            <Field label="Run at" htmlFor="schedule-run-at" error={errors.run_at}>
              <Input
                id="schedule-run-at"
                type="datetime-local"
                value={form.run_at}
                onChange={(event) => setForm({ ...form, run_at: event.target.value })}
              />
            </Field>
          )}

          {form.schedule_type === 'interval' && (
            <Field
              label="Repeat every (minutes)"
              htmlFor="schedule-interval"
              error={errors.interval_minutes}
              hint="Minimum 1 minute, maximum 10,080 minutes (7 days)."
            >
              <Input
                id="schedule-interval"
                type="number"
                min={1}
                max={10080}
                value={form.interval_minutes}
                onChange={(event) => setForm({ ...form, interval_minutes: event.target.value })}
              />
            </Field>
          )}

          {form.schedule_type === 'daily' && (
            <Field label="Time of day" htmlFor="schedule-time" error={errors.time_of_day}>
              <Input
                id="schedule-time"
                type="time"
                value={form.time_of_day}
                onChange={(event) => setForm({ ...form, time_of_day: event.target.value })}
              />
            </Field>
          )}

          <Switch
            checked={form.enabled}
            onChange={(enabled) => setForm({ ...form, enabled })}
            label="Enabled"
            description="Paused schedules keep their settings but will not run."
          />
        </div>
      </Modal>

      <Modal
        open={Boolean(deleting)}
        onClose={() => setDeleting(null)}
        title="Delete this schedule?"
        description="Mochi will stop planning this run. Existing tasks are not affected."
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => setDeleting(null)} disabled={removing}>
              Cancel
            </Button>
            <Button variant="danger" onClick={() => void remove()} loading={removing}>
              Delete schedule
            </Button>
          </>
        }
      >
        <div className="rounded-2xl border border-line bg-surface-muted/50 p-3">
          <p className="text-sm font-bold text-ink">{deleting?.name}</p>
          <p className="mt-1 text-xs text-ink-soft">
            {deleting
              ? workflowName(deleting.workflow_id) ??
                (deleting.instruction || 'No instruction recorded.')
              : ''}
          </p>
          <p className="mt-1 flex items-center gap-1 text-[11px] text-ink-faint">
            <WorkflowIcon className="size-3" aria-hidden />
            {deleting ? describeType(deleting) : ''}
          </p>
        </div>
      </Modal>
    </div>
  )
}
