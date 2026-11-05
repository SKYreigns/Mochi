import { useState } from 'react'
import { AlertCircle, CheckCircle2, ChevronDown, Loader2, ShieldCheck, Wrench, XCircle } from 'lucide-react'
import type { ChatToolEvent } from '@/stores/chat'
import { Badge } from '@/components/ui'
import { cn, formatDuration, humanizeTool } from '@/lib/utils'

export function ToolCard({ event }: { event: ChatToolEvent }) {
  const [open, setOpen] = useState(false)
  const running = event.status === 'running'
  const failed = event.status === 'failed'

  const Icon = running ? Loader2 : failed ? XCircle : CheckCircle2
  const iconClass = running ? 'text-accent animate-spin' : failed ? 'text-danger' : 'text-success'

  const hasDetails =
    Object.keys(event.arguments).length > 0 ||
    Boolean(event.evidence) ||
    Boolean(event.error) ||
    Boolean(event.hint)

  return (
    <div
      className={cn(
        'overflow-hidden rounded-2xl border bg-surface-strong/70 transition-colors',
        failed ? 'border-danger/25' : running ? 'border-accent/30' : 'border-line',
      )}
    >
      <button
        type="button"
        disabled={!hasDetails}
        onClick={() => setOpen((value) => !value)}
        aria-expanded={hasDetails ? open : undefined}
        className={cn(
          'ring-focus flex w-full items-center gap-3 px-3.5 py-2.5 text-left',
          hasDetails && 'hover:bg-surface-muted/50',
        )}
      >
        <span
          className={cn(
            'grid size-8 shrink-0 place-items-center rounded-xl',
            failed ? 'bg-danger-soft' : running ? 'bg-accent-soft' : 'bg-success-soft',
          )}
        >
          <Icon className={cn('size-4', iconClass)} aria-hidden />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13px] font-bold text-ink">
            {humanizeTool(event.tool)}
            {running && '…'}
          </span>
          <span className="flex flex-wrap items-center gap-1.5 text-[11px] text-ink-faint">
            <span className="font-mono">{event.tool}</span>
            {event.durationMs !== undefined && <span>· {formatDuration(event.durationMs)}</span>}
            {event.verified && (
              <span className="inline-flex items-center gap-0.5 text-success">
                · <ShieldCheck className="size-3" aria-hidden /> verified
              </span>
            )}
          </span>
        </span>
        {event.approvalStatus && (
          <Badge
            tone={
              event.approvalStatus === 'approved'
                ? 'success'
                : event.approvalStatus === 'pending'
                  ? 'warning'
                  : event.approvalStatus === 'denied' || event.approvalStatus === 'expired'
                    ? 'danger'
                    : 'neutral'
            }
            className="hidden sm:inline-flex"
          >
            {event.approvalStatus === 'pending' ? 'needs approval' : `approval ${event.approvalStatus}`}
          </Badge>
        )}
        {event.permission && (
          <Badge tone={event.permission === 'destructive' ? 'danger' : 'neutral'} className="hidden sm:inline-flex">
            {event.permission}
          </Badge>
        )}
        {hasDetails && (
          <ChevronDown
            className={cn('size-4 shrink-0 text-ink-faint transition-transform', open && 'rotate-180')}
            aria-hidden
          />
        )}
      </button>

      {open && hasDetails && (
        <div className="space-y-3 border-t border-line bg-surface-muted/30 px-3.5 py-3 text-xs">
          {Object.keys(event.arguments).length > 0 && (
            <div>
              <p className="mb-1 flex items-center gap-1 text-[10px] font-extrabold uppercase tracking-[0.1em] text-ink-faint">
                <Wrench className="size-3" aria-hidden /> Arguments
              </p>
              <pre className="mochi-scroll max-h-48 overflow-auto rounded-xl border border-line bg-surface-strong p-2.5 font-mono text-[11px] leading-relaxed text-ink-soft">
                {JSON.stringify(event.arguments, null, 2)}
              </pre>
            </div>
          )}
          {event.evidence && (
            <div>
              <p className="mb-1 text-[10px] font-extrabold uppercase tracking-[0.1em] text-ink-faint">Evidence</p>
              <p className="rounded-xl bg-success-soft/60 px-2.5 py-2 text-success">{event.evidence}</p>
            </div>
          )}
          {(event.error || event.hint) && (
            <div className="rounded-xl bg-danger-soft/60 px-2.5 py-2 text-danger">
              <p className="flex items-start gap-1.5 font-semibold">
                <AlertCircle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
                {event.error ?? 'This step did not finish.'}
              </p>
              {event.hint && <p className="mt-1 pl-5 text-[11px] opacity-80">{event.hint}</p>}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
