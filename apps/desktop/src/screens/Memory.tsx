import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Brain,
  Pencil,
  Pin,
  PinOff,
  Plus,
  RefreshCw,
  Search,
  Tag,
  Trash2,
} from 'lucide-react'
import { api, errorMessage } from '@/lib/api'
import type { MemoryRecord } from '@/lib/types'
import { firstIssue, memorySchema, type MemoryFormValues } from '@/lib/schemas'
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
import { cn, formatDateTime, relativeTime } from '@/lib/utils'

const KINDS = [
  { value: 'preference', label: 'Preference' },
  { value: 'fact', label: 'Fact' },
  { value: 'website', label: 'Website' },
  { value: 'setting', label: 'Setting' },
  { value: 'note', label: 'Note' },
  { value: 'workflow', label: 'Workflow' },
]

type FormState = {
  key: string
  value: string
  kind: MemoryFormValues['kind']
  tags: string
  pinned: boolean
}

const EMPTY_FORM: FormState = { key: '', value: '', kind: 'preference', tags: '', pinned: false }

export function MemoryScreen() {
  const [memories, setMemories] = useState<MemoryRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [kind, setKind] = useState('')
  const [editing, setEditing] = useState<MemoryRecord | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [formErrors, setFormErrors] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState<MemoryRecord | null>(null)
  const [working, setWorking] = useState(false)

  const load = useCallback(async (text: string, kindValue: string) => {
    setLoading(true)
    try {
      const response = await api.memory({ query: text.trim() || undefined, kind: kindValue || undefined, limit: 300 })
      setMemories(response.memories)
      setError(null)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(query, kind), 280)
    return () => window.clearTimeout(timer)
  }, [query, kind, load])

  const ordered = useMemo(
    () =>
      [...memories].sort((a, b) => {
        if (a.pinned !== b.pinned) return a.pinned ? -1 : 1
        return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
      }),
    [memories],
  )

  const openCreate = () => {
    setEditing(null)
    setForm(EMPTY_FORM)
    setFormErrors({})
    setFormOpen(true)
  }

  const openEdit = (memory: MemoryRecord) => {
    setEditing(memory)
    setForm({
      key: memory.key,
      value: memory.value,
      kind: (KINDS.some((item) => item.value === memory.kind) ? memory.kind : 'note') as MemoryFormValues['kind'],
      tags: memory.tags,
      pinned: memory.pinned,
    })
    setFormErrors({})
    setFormOpen(true)
  }

  const submit = async () => {
    const parsed = memorySchema.safeParse(form)
    if (!parsed.success) {
      setFormErrors(firstIssue(parsed.error))
      return
    }
    setSaving(true)
    setFormErrors({})
    try {
      if (editing) {
        await api.updateMemory(editing.id, parsed.data)
        toast.success('Memory updated')
      } else {
        await api.createMemory(parsed.data)
        toast.success('Saved to memory', 'Mochi can now recall this during future tasks.')
      }
      setFormOpen(false)
      await load(query, kind)
    } catch (err) {
      toast.error('Could not save that memory', errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  const togglePin = async (memory: MemoryRecord) => {
    try {
      await api.updateMemory(memory.id, { pinned: !memory.pinned })
      setMemories((current) =>
        current.map((item) => (item.id === memory.id ? { ...item, pinned: !memory.pinned } : item)),
      )
      toast.success(memory.pinned ? 'Removed from pinned memories' : 'Pinned to the top')
    } catch (err) {
      toast.error('Could not update that memory', errorMessage(err))
    }
  }

  const remove = async () => {
    if (!deleting) return
    setWorking(true)
    try {
      await api.deleteMemory(deleting.id)
      setMemories((current) => current.filter((item) => item.id !== deleting.id))
      toast.success('Memory deleted')
      setDeleting(null)
    } catch (err) {
      toast.error('Could not delete that memory', errorMessage(err))
    } finally {
      setWorking(false)
    }
  }

  return (
    <div>
      <PageHeader
        eyebrow="Memory"
        title="What Mochi remembers"
        description="Saved preferences, facts and useful details. These are stored locally and only used to help with your tasks."
        icon={<Brain className="size-5" aria-hidden />}
        actions={
          <>
            <Button variant="outline" size="sm" onClick={() => void load(query, kind)} loading={loading} icon={<RefreshCw className="size-4" aria-hidden />}>
              Refresh
            </Button>
            <Button variant="primary" size="sm" onClick={openCreate} icon={<Plus className="size-4" aria-hidden />}>
              Add memory
            </Button>
          </>
        }
      />

      <div className="mb-4 flex flex-wrap gap-2">
        <div className="relative min-w-[220px] flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-ink-faint" aria-hidden />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search memories, tags or values…"
            aria-label="Search memories"
            className="pl-9"
          />
        </div>
        <div className="w-[180px]">
          <Select value={kind} onChange={(event) => setKind(event.target.value)} aria-label="Filter by memory kind">
            <option value="">All kinds</option>
            {KINDS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </Select>
        </div>
      </div>

      {error && (
        <div className="mb-4 rounded-2xl border border-danger/25 bg-danger-soft px-4 py-3 text-sm text-danger">{error}</div>
      )}

      {loading && memories.length === 0 ? (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, index) => (
            <Skeleton key={index} className="h-36" />
          ))}
        </div>
      ) : ordered.length === 0 ? (
        <EmptyState
          mascot={<Mascot state={query ? 'thinking' : 'sleeping'} size={120} />}
          title={query || kind ? 'No memories match' : 'Nothing saved yet'}
          description={
            query || kind
              ? 'Try a different search, or save something new.'
              : 'Ask Mochi to remember a preference, or add a memory yourself.'
          }
          action={
            <Button size="sm" variant="soft" onClick={openCreate} icon={<Plus className="size-4" aria-hidden />}>
              Add the first memory
            </Button>
          }
        />
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {ordered.map((memory) => (
            <Card
              key={memory.id}
              className={cn('flex flex-col p-4', memory.pinned && 'border-accent/35 bg-accent-soft/25')}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                  <Badge tone="accent">{memory.kind}</Badge>
                  {memory.pinned && (
                    <Badge tone="warning">
                      <Pin className="size-2.5" aria-hidden /> pinned
                    </Badge>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-0.5">
                  <button
                    type="button"
                    onClick={() => void togglePin(memory)}
                    className="ring-focus rounded-lg p-1.5 text-ink-faint hover:bg-surface-strong hover:text-warning"
                    aria-label={memory.pinned ? `Unpin ${memory.key}` : `Pin ${memory.key}`}
                    title={memory.pinned ? 'Unpin' : 'Pin'}
                  >
                    {memory.pinned ? <PinOff className="size-3.5" aria-hidden /> : <Pin className="size-3.5" aria-hidden />}
                  </button>
                  <button
                    type="button"
                    onClick={() => openEdit(memory)}
                    className="ring-focus rounded-lg p-1.5 text-ink-faint hover:bg-surface-strong hover:text-accent-strong"
                    aria-label={`Edit ${memory.key}`}
                    title="Edit"
                  >
                    <Pencil className="size-3.5" aria-hidden />
                  </button>
                  <button
                    type="button"
                    onClick={() => setDeleting(memory)}
                    className="ring-focus rounded-lg p-1.5 text-ink-faint hover:bg-danger-soft hover:text-danger"
                    aria-label={`Delete ${memory.key}`}
                    title="Delete"
                  >
                    <Trash2 className="size-3.5" aria-hidden />
                  </button>
                </div>
              </div>
              <h3 className="mt-2 text-sm font-extrabold text-ink">{memory.key}</h3>
              <p className="mt-1 line-clamp-4 flex-1 whitespace-pre-wrap text-xs leading-relaxed text-ink-soft">{memory.value}</p>
              {memory.tags && (
                <p className="mt-2 flex flex-wrap items-center gap-1 text-[10px] font-semibold text-ink-faint">
                  <Tag className="size-2.5" aria-hidden />
                  {memory.tags.split(',').map((tag) => tag.trim()).filter(Boolean).map((tag) => (
                    <span key={tag} className="rounded-md bg-surface-muted px-1.5 py-0.5">{tag}</span>
                  ))}
                </p>
              )}
              <p className="mt-2 border-t border-line pt-2 text-[10px] text-ink-faint" title={formatDateTime(memory.updated_at)}>
                {memory.source} · updated {relativeTime(memory.updated_at)}
              </p>
            </Card>
          ))}
        </div>
      )}

      <Modal
        open={formOpen}
        onClose={() => setFormOpen(false)}
        title={editing ? 'Edit memory' : 'Add a memory'}
        description="Mochi uses these details as background context. Secrets are never stored here."
        size="md"
        footer={
          <>
            <Button variant="ghost" onClick={() => setFormOpen(false)} disabled={saving}>
              Cancel
            </Button>
            <Button onClick={() => void submit()} loading={saving}>
              {editing ? 'Save changes' : 'Save memory'}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="Name" htmlFor="memory-key" error={formErrors.key} hint="A short label, like “Preferred download folder”.">
            <Input
              id="memory-key"
              value={form.key}
              onChange={(event) => setForm({ ...form, key: event.target.value })}
              placeholder="Preferred download folder"
              maxLength={200}
            />
          </Field>
          <Field label="What should Mochi remember?" htmlFor="memory-value" error={formErrors.value}>
            <Textarea
              id="memory-value"
              rows={4}
              value={form.value}
              onChange={(event) => setForm({ ...form, value: event.target.value })}
              placeholder="I like documents saved in D:\\University\\Notes."
              maxLength={8000}
            />
          </Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Kind" htmlFor="memory-kind" error={formErrors.kind}>
              <Select id="memory-kind" value={form.kind} onChange={(event) => setForm({ ...form, kind: event.target.value as FormState['kind'] })}>
                {KINDS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Tags" htmlFor="memory-tags" error={formErrors.tags} hint="Comma separated.">
              <Input
                id="memory-tags"
                value={form.tags}
                onChange={(event) => setForm({ ...form, tags: event.target.value })}
                placeholder="files, university"
              />
            </Field>
          </div>
          <Switch
            checked={form.pinned}
            onChange={(pinned) => setForm({ ...form, pinned })}
            label="Pin this memory"
            description="Pinned memories are shown first."
          />
        </div>
      </Modal>

      <Modal
        open={Boolean(deleting)}
        onClose={() => setDeleting(null)}
        title="Delete this memory?"
        description="This removes it from Mochi's saved context. It cannot be undone."
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => setDeleting(null)} disabled={working}>
              Cancel
            </Button>
            <Button variant="danger" onClick={() => void remove()} loading={working}>
              Delete memory
            </Button>
          </>
        }
      >
        <div className="rounded-2xl border border-line bg-surface-muted/50 p-3">
          <p className="text-sm font-bold text-ink">{deleting?.key}</p>
          <p className="mt-1 line-clamp-3 text-xs text-ink-soft">{deleting?.value}</p>
        </div>
      </Modal>
    </div>
  )
}
