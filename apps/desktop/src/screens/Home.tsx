import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { motion, useReducedMotion } from 'framer-motion'
import {
  Activity,
  ArrowRight,
  BookOpen,
  CalendarCheck,
  ChevronRight,
  Cpu,
  FolderSearch,
  Globe,
  ListChecks,
  Loader2,
  Monitor,
  Play,
  ShieldQuestion,
  Workflow as WorkflowIcon,
} from 'lucide-react'
import { api, errorMessage } from '@/lib/api'
import type { Approval, Conversation, Task, Workflow } from '@/lib/types'
import { useAppStore } from '@/stores/app'
import { useChatStore } from '@/stores/chat'
import { toast } from '@/stores/toast'
import { Composer } from '@/components/Composer'
import { Mascot } from '@/components/Mascot'
import { Badge, Button, Card, EmptyState, SectionHeading, Skeleton } from '@/components/ui'
import { PageHeader } from '@/components/AppShell'
import {
  cn,
  greeting,
  isActiveStatus,
  pluralize,
  relativeTime,
  statusLabel,
  taskTone,
  truncate,
} from '@/lib/utils'

const QUICK_ACTIONS = [
  {
    label: 'Explore my computer',
    description: 'Mochi takes a real screenshot and reports what is open.',
    icon: Monitor,
    tone: 'bg-aqua/70 text-[#3d7f8c]',
    prompt: 'Take a screenshot of my desktop and tell me what windows and apps are currently open.',
  },
  {
    label: 'Check my assignments',
    description: 'Review what is due from your connected LMS.',
    icon: CalendarCheck,
    tone: 'bg-blush/70 text-[#b06f8c]',
    prompt: 'Check my LMS assignments and summarise what is due soon.',
  },
  {
    label: 'Organize my files',
    description: 'Preview a tidy-up of a folder before anything moves.',
    icon: FolderSearch,
    tone: 'bg-lavender/80 text-[#8468c6]',
    prompt: 'Preview how you would organise my Downloads folder by category. Do not move anything yet.',
  },
  {
    label: 'Read a document',
    description: 'Browse permitted folders and preview a file.',
    icon: BookOpen,
    tone: 'bg-cream text-[#a97428]',
    route: '/files',
  },
  {
    label: 'Search the web',
    description: 'Ask Mochi to look something up in the browser.',
    icon: Globe,
    tone: 'bg-aqua/70 text-[#3d7f8c]',
    prompt: 'Search the web for the latest stable Tauri release and summarise the key changes.',
  },
  {
    label: 'Start a workflow',
    description: 'Run a saved sequence of steps.',
    icon: WorkflowIcon,
    tone: 'bg-lavender/80 text-[#8468c6]',
    route: '/workflows',
  },
] as const

export function HomeScreen() {
  const navigate = useNavigate()
  const reduced = useReducedMotion()
  const health = useAppStore((state) => state.health)
  const backendStatus = useAppStore((state) => state.backendStatus)
  const approvals = useAppStore((state) => state.approvals)
  const send = useChatStore((state) => state.send)
  const [draft, setDraft] = useState('')
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [workflows, setWorkflows] = useState<Workflow[]>([])
  const [loading, setLoading] = useState(true)
  const [runningWorkflow, setRunningWorkflow] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const [conversationResponse, taskResponse, workflowResponse] = await Promise.all([
          api.conversations(),
          api.tasks({ limit: 5 }),
          api.workflows(),
        ])
        if (cancelled) return
        setConversations(conversationResponse.conversations.slice(0, 4))
        setTasks(taskResponse.tasks.slice(0, 5))
        setWorkflows(workflowResponse.workflows.slice(0, 3))
      } catch {
        // Individual sections show their empty states if the backend is offline.
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [])

  const provider = health?.services.provider
  const runningTasks = tasks.filter((task) => isActiveStatus(task.status))

  const startPrompt = async (prompt: string) => {
    navigate('/chat')
    await send(prompt)
  }

  const runWorkflow = async (workflow: Workflow) => {
    setRunningWorkflow(workflow.id)
    try {
      await api.runWorkflow(workflow.id)
      toast.success(`Started “${workflow.name}”`, 'Follow the steps in Live Activity.')
    } catch (error) {
      toast.error('Could not start that workflow', errorMessage(error))
    } finally {
      setRunningWorkflow(null)
    }
  }

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Home"
        title={`${greeting()}, welcome back`}
        description="Your little companion is ready to help with your computer, files and routines."
        actions={
          <div className="flex items-center gap-2">
            <Badge tone={backendStatus === 'online' ? 'success' : backendStatus === 'connecting' ? 'warning' : 'danger'} dot>
              {backendStatus === 'online' ? 'Local backend connected' : backendStatus === 'connecting' ? 'Waking up' : 'Backend offline'}
            </Badge>
            {runningTasks.length > 0 && <Badge tone="accent" dot>{runningTasks.length} working</Badge>}
          </div>
        }
      />

      <motion.section
        initial={{ opacity: 0, y: reduced ? 0 : 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: reduced ? 0 : 0.5, ease: [0.22, 1, 0.36, 1] }}
        className="relative overflow-hidden rounded-[32px] border border-line bg-gradient-to-br from-surface-strong/90 via-surface/80 to-accent-soft/60 p-5 shadow-[var(--shadow-soft)] backdrop-blur-2xl sm:p-7 lg:p-9"
      >
        <div className="pointer-events-none absolute -right-12 -top-16 size-56 rounded-full bg-aqua/40 blur-3xl" aria-hidden />
        <div className="pointer-events-none absolute -bottom-20 left-1/3 size-64 rounded-full bg-blush/30 blur-3xl" aria-hidden />

        <div className="relative grid items-center gap-5 lg:grid-cols-[1fr_auto]">
          <div className="min-w-0">
            <span className="inline-flex items-center gap-1.5 rounded-full border border-accent/20 bg-accent-soft/80 px-3 py-1 text-[11px] font-extrabold uppercase tracking-[0.12em] text-accent-strong">
              <span className="size-1.5 rounded-full bg-accent" aria-hidden />
              Mochi is here
            </span>
            <h2 className="mt-3 text-balance text-3xl font-extrabold leading-[1.1] tracking-tight text-ink sm:text-[40px]">
              What shall we do <span className="gradient-text">together</span> today?
            </h2>
            <p className="mt-2 max-w-xl text-sm leading-relaxed text-ink-soft sm:text-[15px]">
              {provider?.configured
                ? `Ask me anything. I can use real tools on this PC, and I will always pause before something consequential.`
                : 'I can browse files, run workflows and try tools right away — but I need an AI model configured before I can reason about a request.'}
            </p>
          </div>
          <div className="relative mx-auto hidden sm:block lg:mx-2">
            <Mascot state={health?.services.emergency_stopped ? 'error' : 'idle'} size={170} title="Mochi, your AI companion" />
            <div className="absolute -bottom-1 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-full border border-line bg-surface-strong/80 px-3 py-1 text-[10px] font-bold text-ink-faint shadow-sm backdrop-blur">
              {health?.services.emergency_stopped ? 'paused, keeping watch' : 'gently floating'}
            </div>
          </div>
        </div>

        <div className="relative mt-5 sm:mt-6">
          <Composer
            value={draft}
            onChange={setDraft}
            onSubmit={(text, attachments) => {
              navigate('/chat')
              return send(text, attachments)
            }}
            placeholder="Tell Mochi what you need help with…"
            className="mx-auto max-w-3xl"
          />
        </div>

        {!provider?.configured && (
          <div className="relative mt-4 flex flex-wrap items-center gap-2 rounded-2xl border border-warning/25 bg-warning-soft/70 px-3.5 py-2.5 text-warning">
            <Cpu className="size-4 shrink-0" aria-hidden />
            <p className="text-xs font-semibold">
              No AI model configured. Mochi can run tools, but reasoning will fail until you choose a model.
            </p>
            <Link to="/settings?section=models" className="ml-auto text-xs font-extrabold underline">
              Set up a model
            </Link>
          </div>
        )}
      </motion.section>

      <section aria-labelledby="quick-actions-heading">
        <SectionHeading
          title="Quick actions"
          description="Each card starts something real — no mock activity."
          action={<span className="text-xs font-semibold text-ink-faint">6 shortcuts</span>}
        />
        <h2 id="quick-actions-heading" className="sr-only">
          Quick actions
        </h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {QUICK_ACTIONS.map((action, index) => {
            const Icon = action.icon
            return (
              <motion.button
                key={action.label}
                type="button"
                initial={{ opacity: 0, y: reduced ? 0 : 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: reduced ? 0 : 0.35, delay: reduced ? 0 : index * 0.04 }}
                onClick={() => {
                  if ('route' in action && action.route) navigate(action.route)
                  else if ('prompt' in action && action.prompt) void startPrompt(action.prompt)
                }}
                className="ring-focus group flex items-start gap-3 rounded-3xl border border-line bg-surface/75 p-4 text-left shadow-[var(--shadow-card)] backdrop-blur-xl transition-all duration-300 hover:-translate-y-1 hover:border-accent/35 hover:shadow-[var(--shadow-soft)]"
              >
                <span className={cn('grid size-11 shrink-0 place-items-center rounded-2xl', action.tone)}>
                  <Icon className="size-5" aria-hidden />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-extrabold text-ink">{action.label}</span>
                  <span className="mt-0.5 block text-xs leading-relaxed text-ink-faint">{action.description}</span>
                </span>
                <ArrowRight
                  className="mt-1 size-4 shrink-0 text-ink-faint transition-transform group-hover:translate-x-1 group-hover:text-accent"
                  aria-hidden
                />
              </motion.button>
            )
          })}
        </div>
      </section>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card className="p-5">
          <SectionHeading
            title="Recent conversations"
            description="Pick up where you left off."
            action={
              <Link to="/chat" className="text-xs font-extrabold text-accent-strong hover:underline">
                Open chat
              </Link>
            }
          />
          <div className="mt-3 space-y-1.5">
            {loading ? (
              <>
                <Skeleton className="h-14" />
                <Skeleton className="h-14" />
                <Skeleton className="h-14" />
              </>
            ) : conversations.length === 0 ? (
              <EmptyState
                mascot={<Mascot state="sleeping" size={80} />}
                title="No conversations yet"
                description="Start a chat above and it will appear here."
                className="py-8"
              />
            ) : (
              conversations.map((conversation) => (
                <Link
                  key={conversation.id}
                  to={`/chat/${conversation.id}`}
                  className="ring-focus group flex items-center gap-3 rounded-2xl border border-transparent px-3 py-2.5 transition-colors hover:border-line hover:bg-surface-muted/60"
                >
                  <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-accent-soft text-accent-strong">
                    <Activity className="size-4" aria-hidden />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-bold text-ink">{conversation.title}</span>
                    <span className="block truncate text-[11px] text-ink-faint">
                      {conversation.message_count} {pluralize(conversation.message_count, 'message')} · {relativeTime(conversation.updated_at)}
                    </span>
                  </span>
                  <ChevronRight className="size-4 text-ink-faint transition-transform group-hover:translate-x-0.5" aria-hidden />
                </Link>
              ))
            )}
          </div>
        </Card>

        <Card className="p-5">
          <SectionHeading
            title="Recent tasks"
            description="Real work Mochi has queued or finished."
            action={
              <Link to="/tasks" className="text-xs font-extrabold text-accent-strong hover:underline">
                All tasks
              </Link>
            }
          />
          <div className="mt-3 space-y-1.5">
            {loading ? (
              <>
                <Skeleton className="h-14" />
                <Skeleton className="h-14" />
              </>
            ) : tasks.length === 0 ? (
              <EmptyState
                icon={<ListChecks className="size-5" aria-hidden />}
                title="No tasks yet"
                description="Tasks appear as soon as you ask Mochi to do something."
                className="py-8"
              />
            ) : (
              tasks.map((task) => (
                <Link
                  key={task.id}
                  to={`/tasks?task=${task.id}`}
                  className="ring-focus group flex items-center gap-3 rounded-2xl border border-transparent px-3 py-2.5 transition-colors hover:border-line hover:bg-surface-muted/60"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-bold text-ink">{task.instruction}</span>
                    <span className="block text-[11px] text-ink-faint">
                      {relativeTime(task.created_at)} · {task.trigger}
                    </span>
                  </span>
                  <Badge tone={taskTone(task.status)} dot>
                    {statusLabel(task.status)}
                  </Badge>
                </Link>
              ))
            )}
          </div>
        </Card>

        <Card className="p-5">
          <SectionHeading
            title="Workflows"
            description="Your saved routines."
            action={
              <Link to="/workflows" className="text-xs font-extrabold text-accent-strong hover:underline">
                Manage
              </Link>
            }
          />
          <div className="mt-3 space-y-1.5">
            {loading ? (
              <Skeleton className="h-16" />
            ) : workflows.length === 0 ? (
              <EmptyState
                icon={<WorkflowIcon className="size-5" aria-hidden />}
                title="No workflows saved"
                description="Create a workflow to repeat a helpful sequence."
                className="py-8"
              />
            ) : (
              workflows.map((workflow) => (
                <div
                  key={workflow.id}
                  className="flex items-center gap-3 rounded-2xl border border-line bg-surface-muted/35 px-3 py-2.5"
                >
                  <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-lavender/70 text-[#8468c6]">
                    <WorkflowIcon className="size-4" aria-hidden />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-bold text-ink">{workflow.name}</span>
                    <span className="block truncate text-[11px] text-ink-faint">
                      {workflow.steps.length} {pluralize(workflow.steps.length, 'step')} · {workflow.enabled ? 'enabled' : 'paused'}
                    </span>
                  </span>
                  <Button
                    size="sm"
                    variant="soft"
                    onClick={() => void runWorkflow(workflow)}
                    disabled={!workflow.enabled || runningWorkflow === workflow.id}
                    aria-label={`Run ${workflow.name}`}
                  >
                    {runningWorkflow === workflow.id ? (
                      <Loader2 className="size-3.5 animate-spin" aria-hidden />
                    ) : (
                      <Play className="size-3.5 fill-current" aria-hidden />
                    )}
                    Run
                  </Button>
                </div>
              ))
            )}
          </div>
        </Card>

        <Card className="p-5">
          <SectionHeading
            title="Waiting for you"
            description="Approvals keep consequential actions safe."
            action={
              approvals.length > 0 ? (
                <Badge tone="warning" dot>
                  {approvals.length} pending
                </Badge>
              ) : (
                <Badge tone="success">All clear</Badge>
              )
            }
          />
          <div className="mt-3 space-y-1.5">
            {approvals.length === 0 ? (
              <div className="flex items-center gap-3 rounded-2xl border border-dashed border-line px-3 py-4">
                <ShieldQuestion className="size-5 text-success" aria-hidden />
                <p className="text-xs leading-relaxed text-ink-faint">
                  Nothing needs approval right now. Mochi will pause here when a tool needs your consent.
                </p>
              </div>
            ) : (
              approvals.slice(0, 3).map((approval) => (
                <ApprovalRow key={approval.id} approval={approval} />
              ))
            )}
          </div>
        </Card>
      </div>
    </div>
  )
}

function ApprovalRow({ approval }: { approval: Approval }) {
  const navigate = useNavigate()
  return (
    <button
      type="button"
      onClick={() => navigate('/tasks')}
      className="ring-focus flex w-full items-center gap-3 rounded-2xl border border-warning/25 bg-warning-soft/50 px-3 py-2.5 text-left transition-colors hover:bg-warning-soft/80"
    >
      <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-warning-soft text-warning">
        <ShieldQuestion className="size-4" aria-hidden />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-bold text-ink">{approval.tool_name}</span>
        <span className="block truncate text-[11px] text-ink-soft">{truncate(approval.reason, 70)}</span>
      </span>
      <span className="shrink-0 text-[10px] font-extrabold uppercase tracking-wide text-warning">Review</span>
    </button>
  )
}
