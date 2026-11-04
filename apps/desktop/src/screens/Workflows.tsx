import { useCallback, useEffect, useState } from 'react'
import {
  CheckCircle2,
  Clock,
  Copy,
  History,
  Loader2,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Trash2,
  Workflow as WorkflowIcon,
  XCircle,
} from 'lucide-react'
import { api, errorMessage } from '@/lib/api'
import type { Workflow, WorkflowRun } from '@/lib/types'
import { firstIssue, workflowSchema } from '@/lib/schemas'
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
  Select,
  Skeleton,
  Switch,
  Textarea,
} from '@/components/ui'
import { cn, formatDateTime, relativeTime, statusLabel } from '@/lib/utils'

interface StepForm {
  name: string
  kind: 'instruction' | 'tool'
  instruction: string
  tool: string
}

interface WorkflowForm {
  name: string
  description: string
  enabled: boolean
  steps: StepForm[]
}

const EMPTY_STEP: StepForm = { name: '', kind: 'instruction', instruction: '', tool: '' }
const EMPTY_FORM: WorkflowForm = { name: '', description: '', enabled: true, steps: [{ ...EMPTY_STEP }] }

export function WorkflowsScreen() {
  const [workflows, setWorkflows] = useState<Workflow[]>([])
  const [runs, setRuns] = useState<Record<string, WorkflowRun | null>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [runningId, setRunningId] = useState<string | null>(null)

  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<Workflow | null>(null)
  const [form, setForm] = useState<WorkflowForm>(EMPTY_FORM)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState<Workflow | null>(null)
  const [working, setWorking] = useState(false)
  const [historyFor, setHistoryFor] = useState<Workflow | null>(null)
  const [historyRuns, setHistoryRuns] = useState<WorkflowRun[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)

  const load = useCallback(async () => {
    try {
      const response = await api.workflows()
      setWorkflows(response.workflows)
      setError(null)
      const entries = await Promise.allSettled(
        response.workflows.map(async (workflow) => {
          const history = await api.workflowRuns(workflow.id, 1)
          return [workflow.id, history.runs[0] ?? null] as const
        }),
      )
      const next: Record<string, WorkflowRun | null> = {}
      for (const entry of entries) {
        if (entry.status === 'fulfilled') next[entry.value[0]] = entry.value[1]
      }
      setRuns(next)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
    const interval = window.setInterval(() => void load(), 12_000)
    return () => window.clearInterval(interval)
  }, [load])

  const openCreate = () => {
    setEditing(null)
    setForm({ ...EMPTY_FORM, steps: [{ ...EMPTY_STEP }] })
    setErrors({})
    setFormOpen(true)
  }

  const openEdit = (workflow: Workflow) => {
    setEditing(workflow)
    setForm({
      name: workflow.name,
      description: workflow.description,
      enabled: workflow.enabled,
      steps:
        workflow.steps.length > 0
          ? workflow.steps.map((step) => ({
              name: step.name,
              kind: step.kind === 'tool' ? 'tool' : 'instruction',
              instruction: typeof step.config?.instruction === 'string' ? step.config.instruction : '',
              tool: typeof step.config?.tool === 'string' ? step.config.tool : '',
            }))
          : [{ ...EMPTY_STEP }],
    })
    setErrors({})
    setFormOpen(true)
  }

  const payloadFor = (values: WorkflowForm) => ({
    name: values.name,
    description: values.description,
    enabled: values.enabled,
    steps: values.steps.map((step) => ({
      name: step.name,
      kind: step.kind,
      config:
        step.kind === 'instruction'
          ? { instruction: step.instruction }
          : { tool: step.tool, arguments: {} },
    })),
  })

  const submit = async () => {
    const parsed = workflowSchema.safeParse(form)
    if (!parsed.success) {
      setErrors(firstIssue(parsed.error))
      return
    }
    setSaving(true)
    setErrors({})
    try {
      if (editing) {
        await api.updateWorkflow(editing.id, payloadFor(parsed.data as WorkflowForm))
        toast.success('Workflow updated')
      } else {
        await api.createWorkflow(payloadFor(parsed.data as WorkflowForm))
        toast.success('Workflow created')
      }
      setFormOpen(false)
      await load()
    } catch (err) {
      toast.error('Could not save that workflow', errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  const duplicate = async (workflow: Workflow) => {
    try {
      await api.createWorkflow({
        name: `${workflow.name} copy`.slice(0, 200),
        description: workflow.description,
        enabled: false,
        steps: workflow.steps.map((step) => ({
          name: step.name,
          kind: step.kind === 'tool' ? 'tool' : 'instruction',
          config: step.config ?? {},
        })),
      })
      toast.success('Workflow duplicated', 'The copy starts paused so you can review it.')
      await load()
    } catch (err) {
      toast.error('Could not duplicate that workflow', errorMessage(err))
    }
  }

  const toggle = async (workflow: Workflow) => {
    try {
      await api.updateWorkflow(workflow.id, { enabled: !workflow.enabled })
      setWorkflows((current) =>
        current.map((item) => (item.id === workflow.id ? { ...item, enabled: !item.enabled } : item)),
      )
    } catch (err) {
      toast.error('Could not change that workflow', errorMessage(err))
    }
  }

  const run = async (workflow: Workflow) => {
    setRunningId(workflow.id)
    try {
      await api.runWorkflow(workflow.id)
      toast.success(`Started “${workflow.name}”`, 'Follow each step in Live Activity.')
      window.setTimeout(() => void load(), 1500)
    } catch (err) {
      toast.error('Could not start that workflow', errorMessage(err))
    } finally {
      setRunningId(null)
    }
  }

  const remove = async () => {
    if (!deleting) return
    setWorking(true)
    try {
      await api.deleteWorkflow(deleting.id)
      setDeleting(null)
      toast.success('Workflow deleted')
      await load()
    } catch (err) {
      toast.error('Could not delete that workflow', errorMessage(err))
    } finally {
      setWorking(false)
    }
  }

  const openHistory = async (workflow: Workflow) => {
    setHistoryFor(workflow)
    setHistoryLoading(true)
    try {
      const response = await api.workflowRuns(workflow.id, 20)
      setHistoryRuns(response.runs)
    } catch (err) {
      toast.error('Could not load execution history', errorMessage(err))
      setHistoryRuns([])
    } finally {
      setHistoryLoading(false)
    }
  }

  const updateStep = (index: number, patch: Partial<StepForm>) => {
    setForm((current) => ({
      ...current,
      steps: current.steps.map((step, stepIndex) => (stepIndex === index ? { ...step, ...patch } : step)),
    }))
  }

  return (
    <div>
      <PageHeader
        eyebrow="Workflows"
        title="Little routines for Mochi"
        description="Save a sequence of instructions or tool calls, then run it whenever you need it."
        icon={<WorkflowIcon className="size-5" aria-hidden />}
        actions={
          <>
            <Button variant="outline" size="sm" onClick={() => void load()} loading={loading} icon={<RefreshCw className="size-4" aria-hidden />}>
              Refresh
            </Button>
            <Button variant="primary" size="sm" onClick={openCreate} icon={<Plus className="size-4" aria-hidden />}>
              New workflow
            </Button>
          </>
        }
      />

      {error && <div className="mb-4 rounded-2xl border border-danger/25 bg-danger-soft px-4 py-3 text-sm text-danger">{error}</div>}

      {loading && workflows.length === 0 ? (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 3 }).map((_, index) => (
            <Skeleton key={index} className="h-52" />
          ))}
        </div>
      ) : workflows.length === 0 ? (
        <EmptyState
          mascot={<Mascot state="thinking" size={120} />}
          title="No workflows yet"
          description="Build a small routine — like “open my LMS and check assignments” — and Mochi can run it again later."
          action={
            <Button variant="soft" size="sm" onClick={openCreate} icon={<Plus className="size-4" aria-hidden />}>
              Create your first workflow
            </Button>
          }
        />
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {workflows.map((workflow) => {
            const lastRun = runs[workflow.id]
            const running = runningId === workflow.id
            return (
              <Card key={workflow.id} className={cn('flex flex-col p-4', !workflow.enabled && 'opacity-80')}>
                <div className="flex items-start justify-between gap-2">
                  <span className="grid size-10 shrink-0 place-items-center rounded-2xl bg-lavender/70 text-[#8468c6]">
                    <WorkflowIcon className="size-5" aria-hidden />
                  </span>
                  <div className="flex flex-wrap items-center justify-end gap-1">
                    <Badge tone={workflow.enabled ? 'accent' : 'neutral'} dot>
                      {workflow.enabled ? 'enabled' : 'paused'}
                    </Badge>
                    {lastRun && (
                      <Badge tone={lastRun.status === 'completed' ? 'success' : lastRun.status === 'running' ? 'info' : 'danger'}>
                        {statusLabel(lastRun.status)}
                      </Badge>
                    )}
                  </div>
                </div>
                <h3 className="mt-2.5 text-base font-extrabold text-ink">{workflow.name}</h3>
                <p className="mt-1 line-clamp-2 min-h-[2.2rem] text-xs leading-relaxed text-ink-faint">
                  {workflow.description || 'No description yet.'}
                </p>
                <div className="mt-2.5 flex flex-wrap gap-1">
                  {workflow.steps.slice(0, 4).map((step, index) => (
                    <span key={`${workflow.id}-${index}`} className="rounded-md bg-surface-muted px-1.5 py-0.5 text-[10px] font-semibold text-ink-faint">
                      {step.name}
                    </span>
                  ))}
                  {workflow.steps.length > 4 && <span className="text-[10px] font-bold text-ink-faint">+{workflow.steps.length - 4}</span>}
                </div>
                <p className="mt-2 flex items-center gap-1 text-[10px] text-ink-faint">
                  <Clock className="size-3" aria-hidden />
                  {workflow.steps.length} step{workflow.steps.length === 1 ? '' : 's'} ·{' '}
                  {lastRun ? `last run ${relativeTime(lastRun.started_at)}` : 'never run'} · created {relativeTime(workflow.created_at)}
                </p>

                <div className="mt-3 flex flex-wrap items-center gap-1.5 border-t border-line pt-2.5">
                  <Button
                    size="sm"
                    variant="primary"
                    onClick={() => void run(workflow)}
                    disabled={!workflow.enabled || running}
                    loading={running}
                    icon={<Play className="size-3.5 fill-current" aria-hidden />}
                  >
                    Run
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => openEdit(workflow)} aria-label={`Edit ${workflow.name}`} title="Edit">
                    <Pencil className="size-3.5" aria-hidden />
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => void duplicate(workflow)} aria-label={`Duplicate ${workflow.name}`} title="Duplicate">
                    <Copy className="size-3.5" aria-hidden />
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => void openHistory(workflow)} aria-label={`History for ${workflow.name}`} title="Execution history">
                    <History className="size-3.5" aria-hidden />
                  </Button>
                  <button
                    type="button"
                    onClick={() => void toggle(workflow)}
                    className="ring-focus ml-auto rounded-lg px-1.5 py-1 text-[10px] font-extrabold text-ink-faint hover:bg-surface-muted hover:text-ink"
                    aria-label={workflow.enabled ? `Pause ${workflow.name}` : `Enable ${workflow.name}`}
                  >
                    {workflow.enabled ? 'Pause' : 'Enable'}
                  </button>
                  <Button size="sm" variant="ghost" className="text-danger hover:bg-danger-soft" onClick={() => setDeleting(workflow)} aria-label={`Delete ${workflow.name}`} title="Delete">
                    <Trash2 className="size-3.5" aria-hidden />
                  </Button>
                </div>
              </Card>
            )
          })}
        </div>
      )}

      <Modal
        open={formOpen}
        onClose={() => setFormOpen(false)}
        title={editing ? 'Edit workflow' : 'Create a workflow'}
        description="Steps run in order. Instruction steps ask the AI model to do something; tool steps call a backend tool directly."
        size="xl"
        footer={
          <>
            <Button variant="ghost" onClick={() => setFormOpen(false)} disabled={saving}>
              Cancel
            </Button>
            <Button onClick={() => void submit()} loading={saving}>
              {editing ? 'Save changes' : 'Create workflow'}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
            <Field label="Workflow name" htmlFor="workflow-name" error={errors.name}>
              <Input
                id="workflow-name"
                value={form.name}
                onChange={(event) => setForm({ ...form, name: event.target.value })}
                placeholder="Morning check-in"
                maxLength={200}
              />
            </Field>
            <div className="pb-0.5">
              <Switch checked={form.enabled} onChange={(enabled) => setForm({ ...form, enabled })} label="Enabled" />
            </div>
          </div>
          <Field label="Description" htmlFor="workflow-description" error={errors.description}>
            <Textarea
              id="workflow-description"
              rows={2}
              value={form.description}
              onChange={(event) => setForm({ ...form, description: event.target.value })}
              placeholder="What is this routine for?"
              maxLength={1000}
            />
          </Field>

          <div>
            <div className="mb-2 flex items-center justify-between">
              <p className="text-xs font-extrabold uppercase tracking-[0.08em] text-ink-soft">Steps</p>
              <Button
                size="sm"
                variant="soft"
                onClick={() => setForm({ ...form, steps: [...form.steps, { ...EMPTY_STEP }] })}
                icon={<Plus className="size-3.5" aria-hidden />}
                disabled={form.steps.length >= 30}
              >
                Add step
              </Button>
            </div>
            {errors.steps && <p className="mb-2 text-xs font-semibold text-danger">{errors.steps}</p>}
            <div className="space-y-2">
              {form.steps.map((step, index) => (
                <div key={index} className="rounded-2xl border border-line bg-surface-muted/30 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="grid size-6 place-items-center rounded-full bg-accent-soft text-[10px] font-extrabold text-accent-strong">
                      {index + 1}
                    </span>
                    <button
                      type="button"
                      onClick={() => setForm({ ...form, steps: form.steps.filter((_, stepIndex) => stepIndex !== index) })}
                      className="ring-focus rounded-lg p-1 text-ink-faint hover:bg-danger-soft hover:text-danger"
                      aria-label={`Remove step ${index + 1}`}
                      disabled={form.steps.length === 1}
                    >
                      <Trash2 className="size-3.5" aria-hidden />
                    </button>
                  </div>
                  <div className="mt-2 grid gap-2 sm:grid-cols-[1fr_130px]">
                    <Field label={`Step ${index + 1} name`} htmlFor={`step-name-${index}`} error={errors[`steps.${index}.name`]}>
                      <Input
                        id={`step-name-${index}`}
                        value={step.name}
                        onChange={(event) => updateStep(index, { name: event.target.value })}
                        placeholder="Check assignments"
                      />
                    </Field>
                    <Field label="Kind" htmlFor={`step-kind-${index}`}>
                      <Select
                        id={`step-kind-${index}`}
                        value={step.kind}
                        onChange={(event) => updateStep(index, { kind: event.target.value as StepForm['kind'] })}
                      >
                        <option value="instruction">Instruction</option>
                        <option value="tool">Tool call</option>
                      </Select>
                    </Field>
                  </div>
                  {step.kind === 'instruction' ? (
                    <Field label="Instruction" htmlFor={`step-instruction-${index}`} error={errors[`steps.${index}.instruction`]} className="mt-2">
                      <Textarea
                        id={`step-instruction-${index}`}
                        rows={2}
                        value={step.instruction}
                        onChange={(event) => updateStep(index, { instruction: event.target.value })}
                        placeholder="Open my LMS and list assignments due this week."
                      />
                    </Field>
                  ) : (
                    <Field
                      label="Tool name"
                      htmlFor={`step-tool-${index}`}
                      error={errors[`steps.${index}.tool`]}
                      hint="Exact backend tool name, for example files.list_directory."
                      className="mt-2"
                    >
                      <Input
                        id={`step-tool-${index}`}
                        value={step.tool}
                        onChange={(event) => updateStep(index, { tool: event.target.value })}
                        placeholder="files.list_directory"
                        className="font-mono"
                      />
                    </Field>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      </Modal>

      <Modal
        open={Boolean(historyFor)}
        onClose={() => setHistoryFor(null)}
        title={historyFor ? `History · ${historyFor.name}` : 'Execution history'}
        description="Real workflow runs stored by the backend."
        size="lg"
        footer={
          <Button
            variant="soft"
            loading={historyLoading}
            onClick={() => historyFor && void openHistory(historyFor)}
            icon={<RefreshCw className="size-4" aria-hidden />}
          >
            Refresh history
          </Button>
        }
      >
        {historyLoading ? (
          <div className="flex items-center gap-2 py-8 text-sm text-ink-faint">
            <Loader2 className="size-4 animate-spin" aria-hidden /> Loading runs…
          </div>
        ) : historyRuns.length === 0 ? (
          <EmptyState
            mascot={<Mascot state="sleeping" size={90} />}
            title="No runs yet"
            description="Run this workflow and its execution history will appear here."
          />
        ) : (
          <div className="space-y-2">
            {historyRuns.map((run) => (
              <div key={run.id} className="rounded-2xl border border-line bg-surface-muted/30 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={run.status === 'completed' ? 'success' : run.status === 'running' ? 'info' : run.status === 'cancelled' ? 'neutral' : 'danger'} dot>
                    {statusLabel(run.status)}
                  </Badge>
                  <span className="text-[11px] text-ink-faint">{formatDateTime(run.started_at)}</span>
                  {run.finished_at && <span className="text-[10px] text-ink-faint">· finished {formatDateTime(run.finished_at)}</span>}
                  <span className="ml-auto font-mono text-[10px] text-ink-faint">{run.id.slice(-8)}</span>
                </div>
                {run.error && <p className="mt-1.5 rounded-lg bg-danger-soft px-2 py-1.5 text-[11px] text-danger">{run.error}</p>}
                <div className="mt-2 space-y-1">
                  {run.results.map((result) => (
                    <div key={`${run.id}-${result.index}`} className="flex items-start gap-1.5 text-[11px]">
                      {result.status === 'completed' ? (
                        <CheckCircle2 className="mt-0.5 size-3 shrink-0 text-success" aria-hidden />
                      ) : (
                        <XCircle className="mt-0.5 size-3 shrink-0 text-danger" aria-hidden />
                      )}
                      <span className="font-semibold text-ink-soft">{result.name}</span>
                      <span className="text-ink-faint">· {result.status}</span>
                      {result.error && <span className="text-danger">· {result.error}</span>}
                      {result.reason && <span className="text-ink-faint">· {result.reason}</span>}
                    </div>
                  ))}
                  {run.results.length === 0 && <p className="text-[11px] text-ink-faint">No step results were recorded.</p>}
                </div>
              </div>
            ))}
          </div>
        )}
      </Modal>

      <Modal
        open={Boolean(deleting)}
        onClose={() => setDeleting(null)}
        title="Delete this workflow?"
        description="It will be removed from the library. Past run history is kept by the backend where possible."
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => setDeleting(null)} disabled={working}>
              Cancel
            </Button>
            <Button variant="danger" onClick={() => void remove()} loading={working}>
              Delete workflow
            </Button>
          </>
        }
      >
        <div className="rounded-2xl border border-line bg-surface-muted/50 p-3">
          <p className="text-sm font-bold text-ink">{deleting?.name}</p>
          <p className="mt-1 text-xs text-ink-soft">{deleting?.description || 'No description.'}</p>
          <p className="mt-1 text-[11px] text-ink-faint">{deleting?.steps.length ?? 0} steps</p>
        </div>
      </Modal>
    </div>
  )
}
