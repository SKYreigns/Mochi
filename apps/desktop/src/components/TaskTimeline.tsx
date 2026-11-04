import { Check, Circle, Loader2, X } from 'lucide-react'
import type { PlanStep } from '@/lib/types'
import { cn } from '@/lib/utils'

export interface TimelineStep {
  title: string
  tools?: string[]
}

export function normalizePlan(plan: PlanStep[] | string[] | null | undefined): TimelineStep[] {
  if (!Array.isArray(plan)) return []
  return plan
    .map((step) => {
      if (typeof step === 'string') return { title: step }
      if (step && typeof step === 'object') {
        return {
          title: typeof step.title === 'string' ? step.title : 'Step',
          tools: Array.isArray(step.tools) ? step.tools.map(String) : undefined,
        }
      }
      return null
    })
    .filter((step): step is TimelineStep => step !== null)
}

export function TaskTimeline({
  steps,
  currentStep = 0,
  status,
  className,
  emptyLabel = 'Mochi has not shared a plan for this task yet.',
}: {
  steps: PlanStep[] | string[] | null | undefined
  currentStep?: number
  status?: string
  className?: string
  emptyLabel?: string
}) {
  const items = normalizePlan(steps)

  if (items.length === 0) {
    return <p className="rounded-2xl border border-dashed border-line px-4 py-5 text-sm text-ink-faint">{emptyLabel}</p>
  }

  const finished = status === 'completed'
  const failed = status === 'failed'
  const cancelled = status === 'cancelled'

  return (
    <ol className={cn('relative space-y-1', className)}>
      {items.map((step, index) => {
        const complete = finished || index < currentStep
        const active = !complete && !failed && !cancelled && index === currentStep
        const errored = failed && index === currentStep
        const isLast = index === items.length - 1

        return (
          <li key={`${index}-${step.title}`} className="relative flex gap-3 pb-3 last:pb-0">
            {!isLast && (
              <span
                className={cn(
                  'absolute left-[13px] top-7 h-[calc(100%-1.25rem)] w-px',
                  complete ? 'bg-success/40' : 'bg-line-strong',
                )}
                aria-hidden
              />
            )}
            <span
              className={cn(
                'relative z-10 mt-0.5 grid size-7 shrink-0 place-items-center rounded-full border text-[11px] font-extrabold',
                complete && 'border-success/30 bg-success-soft text-success',
                active && 'border-accent/40 bg-accent-soft text-accent-strong',
                errored && 'border-danger/30 bg-danger-soft text-danger',
                !complete && !active && !errored && 'border-line bg-surface-strong text-ink-faint',
              )}
              aria-hidden
            >
              {complete ? (
                <Check className="size-3.5" />
              ) : errored ? (
                <X className="size-3.5" />
              ) : active ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <span>{index + 1}</span>
              )}
            </span>
            <div className="min-w-0 flex-1 pt-0.5">
              <p
                className={cn(
                  'text-sm font-bold',
                  complete && 'text-ink-soft',
                  active && 'text-ink',
                  errored && 'text-danger',
                  !complete && !active && !errored && 'text-ink-faint',
                )}
              >
                {step.title}
              </p>
              {step.tools && step.tools.length > 0 && (
                <p className="mt-0.5 flex flex-wrap gap-1">
                  {step.tools.map((tool) => (
                    <span
                      key={tool}
                      className="rounded-md bg-surface-muted px-1.5 py-0.5 font-mono text-[10px] text-ink-faint"
                    >
                      {tool}
                    </span>
                  ))}
                </p>
              )}
              {active && (
                <p className="mt-0.5 inline-flex items-center gap-1 text-[11px] font-semibold text-accent-strong">
                  <Circle className="size-1.5 fill-current" aria-hidden /> In progress
                </p>
              )}
            </div>
          </li>
        )
      })}
    </ol>
  )
}
