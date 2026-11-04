import { useEffect, useState } from 'react'
import { AlertTriangle, ArrowRight, FileWarning, ShieldQuestion, Sparkles, XCircle } from 'lucide-react'
import { Modal, Button, Badge, Field, Textarea } from '@/components/ui'
import { Mascot } from '@/components/Mascot'
import { useAppStore } from '@/stores/app'
import { humanizeTool, titleCase } from '@/lib/utils'

function findTarget(argumentsValue: Record<string, unknown>): string | null {
  const keys = ['path', 'target', 'url', 'destination', 'file', 'filename', 'command', 'query', 'app', 'name']
  for (const key of keys) {
    const value = argumentsValue[key]
    if (typeof value === 'string' && value.trim()) return value
  }
  return null
}

function humanValue(value: unknown): string {
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}

export function ApprovalDialog() {
  const approvals = useAppStore((state) => state.approvals)
  const decideApproval = useAppStore((state) => state.decideApproval)
  const refreshApprovals = useAppStore((state) => state.refreshApprovals)
  const [dismissedId, setDismissedId] = useState<string | null>(null)
  const [note, setNote] = useState('')
  const [working, setWorking] = useState<'approve' | 'reject' | null>(null)

  const current = approvals.find((approval) => approval.id !== dismissedId) ?? null

  useEffect(() => {
    if (!current) setNote('')
  }, [current])

  useEffect(() => {
    if (approvals.length === 0) void refreshApprovals()
    // Refresh once on mount so an approval raised before the UI opened is shown.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (!current) return null

  const target = findTarget(current.arguments)
  const destructive = current.permission === 'destructive'
  const pendingCount = approvals.length

  const decide = async (approved: boolean) => {
    setWorking(approved ? 'approve' : 'reject')
    const ok = await decideApproval(current.id, approved, note.trim())
    setWorking(null)
    if (ok) {
      setNote('')
      setDismissedId(null)
    }
  }

  const dismiss = () => {
    setDismissedId(current.id)
    setNote('')
  }

  return (
    <Modal
      open
      onClose={dismiss}
      title="Mochi needs your permission"
      description={
        pendingCount > 1
          ? `1 of ${pendingCount} actions waiting. Approving only covers this request.`
          : 'Mochi paused before doing something that could change your computer.'
      }
      size="lg"
      closeLabel="Cancel this approval (does not approve)"
      footer={
        <>
          <Button variant="ghost" onClick={dismiss} disabled={working !== null}>
            Cancel
          </Button>
          <Button
            variant="danger"
            onClick={() => void decide(false)}
            loading={working === 'reject'}
            disabled={working !== null}
            icon={<XCircle className="size-4" aria-hidden />}
          >
            Reject
          </Button>
          <Button
            variant="primary"
            onClick={() => void decide(true)}
            loading={working === 'approve'}
            disabled={working !== null}
            icon={<ShieldQuestion className="size-4" aria-hidden />}
          >
            Approve action
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="flex items-start gap-3 rounded-2xl border border-line bg-surface-muted/60 p-3.5">
          <Mascot state="listening" size={64} className="shrink-0" />
          <div className="min-w-0">
            <p className="text-[11px] font-extrabold uppercase tracking-[0.1em] text-ink-faint">Requested by Mochi</p>
            <p className="mt-0.5 text-[15px] font-extrabold leading-snug text-ink">{humanizeTool(current.tool_name)}</p>
            <p className="mt-1 text-xs leading-relaxed text-ink-soft">
              {current.instruction ? `While working on: “${current.instruction}”` : 'During the current task.'}
            </p>
            <p className="mt-1 font-mono text-[11px] text-ink-faint">{current.tool_name}</p>
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="rounded-2xl border border-line p-3.5">
            <p className="text-[10px] font-extrabold uppercase tracking-[0.1em] text-ink-faint">Exact action</p>
            <p className="mt-1 text-sm font-bold text-ink">{titleCase(current.tool_name)}</p>
            <div className="mt-1.5 flex flex-wrap gap-1">
              <Badge tone={destructive ? 'danger' : 'neutral'}>{current.permission}</Badge>
              <Badge tone={current.status === 'pending' ? 'warning' : 'neutral'}>{current.status}</Badge>
            </div>
          </div>
          <div className="rounded-2xl border border-line p-3.5">
            <p className="text-[10px] font-extrabold uppercase tracking-[0.1em] text-ink-faint">Target</p>
            <p className="mt-1 break-all font-mono text-xs font-semibold text-ink">{target ?? 'No file or URL target'}</p>
          </div>
        </div>

        <div className="rounded-2xl border border-line p-3.5">
          <p className="text-[10px] font-extrabold uppercase tracking-[0.1em] text-ink-faint">Why Mochi asks</p>
          <p className="mt-1 text-sm leading-relaxed text-ink-soft">{current.reason || 'This tool requires your confirmation.'}</p>
        </div>

        {Object.keys(current.arguments).length > 0 && (
          <details className="group rounded-2xl border border-line p-3.5">
            <summary className="ring-focus cursor-pointer list-none rounded-lg text-xs font-extrabold text-ink-soft transition-colors hover:text-ink">
              <span className="inline-flex items-center gap-1.5">
                <ArrowRight className="size-3.5 transition-transform group-open:rotate-90" aria-hidden />
                Show exact arguments
              </span>
            </summary>
            <pre className="mochi-scroll mt-2 max-h-56 overflow-auto rounded-xl bg-surface-strong p-3 font-mono text-[11px] leading-relaxed text-ink-soft">
              {JSON.stringify(current.arguments, null, 2)}
            </pre>
          </details>
        )}

        <div className="rounded-2xl border border-warning/25 bg-warning-soft/70 p-3.5 text-warning">
          <p className="flex items-center gap-1.5 text-xs font-extrabold uppercase tracking-[0.08em]">
            <AlertTriangle className="size-3.5" aria-hidden /> Possible consequences
          </p>
          <ul className="mt-1.5 space-y-1 text-xs leading-relaxed">
            <li className="flex gap-1.5">
              <Sparkles className="mt-0.5 size-3 shrink-0" aria-hidden />
              This action runs on your computer with the current {current.permission} permission.
            </li>
            {destructive && (
              <li className="flex gap-1.5">
                <FileWarning className="mt-0.5 size-3 shrink-0" aria-hidden />
                Destructive tools can overwrite or remove files. Some changes cannot be undone.
              </li>
            )}
            <li className="flex gap-1.5">
              <ArrowRight className="mt-0.5 size-3 shrink-0" aria-hidden />
              Nothing happens until you approve. Closing this dialog leaves the request waiting.
            </li>
          </ul>
          {Object.keys(current.arguments).length > 0 && (
            <p className="mt-2 text-[11px] leading-relaxed opacity-80">
              Values: {Object.entries(current.arguments).slice(0, 4).map(([key, value]) => `${key}=${humanValue(value)}`).join(' · ')}
            </p>
          )}
        </div>

        <Field
          label="Note to Mochi (optional)"
          hint="Explains your decision in the task history."
          htmlFor="approval-note"
        >
          <Textarea
            id="approval-note"
            rows={2}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="For example: go ahead, but only this once."
          />
        </Field>
      </div>
    </Modal>
  )
}
