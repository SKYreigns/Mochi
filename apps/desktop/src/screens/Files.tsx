import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowLeft,
  ChevronRight,
  Download,
  Eye,
  File,
  FileArchive,
  FileCode2,
  FileImage,
  FileSpreadsheet,
  FileText,
  Folder,
  FolderOpen,
  FolderTree,
  HardDriveDownload,
  Loader2,
  Search,
  Sparkles,
  Upload,
  X,
} from 'lucide-react'
import { api, errorMessage } from '@/lib/api'
import type { DirectoryListing, FileEntry, FileRoots } from '@/lib/types'
import { toast } from '@/stores/toast'
import { PageHeader } from '@/components/AppShell'
import { Mascot } from '@/components/Mascot'
import { Badge, Button, Card, EmptyState, Input, Modal, Skeleton } from '@/components/ui'
import {
  baseName,
  cn,
  fileGlyph,
  formatBytes,
  formatDateTime,
  parentPath,
  relativeTime,
  safeFileUrl,
} from '@/lib/utils'

type PreviewState = {
  entry: FileEntry
  metadata: Record<string, unknown> | null
  content: string | null
  truncated: boolean
  loading: boolean
  error: string | null
}

const TEXT_EXTENSIONS = new Set([
  'txt', 'md', 'markdown', 'json', 'csv', 'tsv', 'log', 'xml', 'yaml', 'yml', 'html', 'htm',
  'css', 'js', 'jsx', 'ts', 'tsx', 'py', 'rs', 'sql', 'ini', 'toml', 'cfg', 'sh', 'bat', 'ps1',
])

const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'ico'])

function extensionOf(path: string): string {
  const name = baseName(path)
  const index = name.lastIndexOf('.')
  return index === -1 ? '' : name.slice(index + 1).toLowerCase()
}

function Glyph({ entry, className }: { entry: FileEntry; className?: string }) {
  if (entry.is_dir) return <Folder className={cn('size-4 fill-current', className)} aria-hidden />
  const { kind } = fileGlyph(entry.name)
  const Icon =
    kind === 'image'
      ? FileImage
      : kind === 'pdf' || kind === 'document'
        ? FileText
        : kind === 'sheet'
          ? FileSpreadsheet
          : kind === 'code'
            ? FileCode2
            : kind === 'archive'
              ? FileArchive
              : File
  return <Icon className={className} aria-hidden />
}

export function FilesScreen() {
  const [roots, setRoots] = useState<FileRoots | null>(null)
  const [current, setCurrent] = useState<string>('')
  const [listing, setListing] = useState<DirectoryListing | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [searchResults, setSearchResults] = useState<FileEntry[] | null>(null)
  const [searchError, setSearchError] = useState<string | null>(null)
  const [preview, setPreview] = useState<PreviewState | null>(null)
  const [organize, setOrganize] = useState<{ data: Record<string, unknown>; evidence: string } | null>(null)
  const [organizing, setOrganizing] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [recent, setRecent] = useState<FileEntry[]>([])
  const uploadRef = useRef<HTMLInputElement>(null)

  const loadDirectory = useCallback(async (path: string) => {
    setLoading(true)
    setError(null)
    try {
      const response = await api.listDirectory(path)
      setListing(response.data)
      setCurrent(response.data.path)
      setSearchResults(null)
      setQuery('')
    } catch (err) {
      setError(errorMessage(err))
      setListing(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    const boot = async () => {
      try {
        const [rootsResponse] = await Promise.all([api.fileRoots()])
        if (cancelled) return
        setRoots(rootsResponse)
        await loadDirectory(rootsResponse.workspace)
        try {
          const workspace = await api.listDirectory(rootsResponse.workspace)
          if (!cancelled) {
            setRecent(
              workspace.data.entries
                .filter((entry) => !entry.is_dir)
                .sort((a, b) => new Date(b.modified).getTime() - new Date(a.modified).getTime())
                .slice(0, 5),
            )
          }
        } catch {
          // Recent documents are a convenience, not a requirement.
        }
      } catch (err) {
        if (!cancelled) {
          setError(errorMessage(err))
          setLoading(false)
        }
      }
    }
    void boot()
    return () => {
      cancelled = true
    }
  }, [loadDirectory])

  const rootEntries = useMemo(() => {
    if (!roots) return []
    const seen = new Set<string>()
    const list: Array<{ label: string; path: string; hint: string }> = []
    for (const root of roots.roots) {
      if (seen.has(root)) continue
      seen.add(root)
      list.push({ label: baseName(root) || root, path: root, hint: 'Permitted folder' })
    }
    return list.slice(0, 8)
  }, [roots])

  const canGoUp = useMemo(() => {
    if (!current) return false
    const parent = parentPath(current)
    if (!parent || parent === current) return false
    return Boolean(
      roots?.roots.some(
        (root) => parent === root || parent.startsWith(`${root}\\`) || parent.startsWith(`${root}/`),
      ),
    )
  }, [current, roots])

  const runSearch = async () => {
    const trimmed = query.trim()
    if (!trimmed) {
      setSearchResults(null)
      return
    }
    setSearching(true)
    setSearchError(null)
    try {
      const response = await api.searchFiles(trimmed, current)
      setSearchResults(response.data.results as FileEntry[])
    } catch (err) {
      setSearchError(errorMessage(err))
      setSearchResults([])
    } finally {
      setSearching(false)
    }
  }

  const openPreview = async (entry: FileEntry) => {
    const extension = extensionOf(entry.path)
    const state: PreviewState = {
      entry,
      metadata: null,
      content: null,
      truncated: false,
      loading: true,
      error: null,
    }
    setPreview(state)
    try {
      const meta = await api.fileMetadata(entry.path).catch(() => null)
      let content: string | null = null
      let truncated = false
      let readError: string | null = null
      if (TEXT_EXTENSIONS.has(extension) && !entry.is_dir) {
        try {
          const read = await api.readFile(entry.path)
          content = read.data.content
          truncated = read.data.truncated
        } catch (err) {
          readError = errorMessage(err)
        }
      }
      setPreview({
        ...state,
        metadata: meta?.data ?? null,
        content,
        truncated,
        loading: false,
        error: readError,
      })
    } catch (err) {
      setPreview({ ...state, loading: false, error: errorMessage(err) })
    }
  }

  const upload = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    setUploading(true)
    try {
      let lastPath = ''
      for (const file of Array.from(files).slice(0, 5)) {
        const result = await api.uploadFile(file)
        lastPath = result.path
      }
      toast.success('File uploaded', lastPath ? `Saved as ${baseName(lastPath)} in Mochi’s uploads folder.` : undefined)
      if (roots && current === roots.uploads) await loadDirectory(current)
    } catch (err) {
      toast.error('Could not upload that file', errorMessage(err))
    } finally {
      setUploading(false)
      if (uploadRef.current) uploadRef.current.value = ''
    }
  }

  const previewOrganize = async () => {
    setOrganizing(true)
    try {
      const result = await api.organizePreview(current)
      setOrganize({ data: result.data, evidence: result.evidence })
    } catch (err) {
      toast.error('Could not preview an organisation plan', errorMessage(err))
    } finally {
      setOrganizing(false)
    }
  }

  const entries = searchResults ?? listing?.entries ?? []
  const showingSearch = searchResults !== null

  return (
    <div>
      <PageHeader
        eyebrow="Files & Documents"
        title="A cozy corner for your files"
        description="Browse only the folders Mochi is allowed to see. The backend enforces the sandbox on every request."
        icon={<FolderOpen className="size-5" aria-hidden />}
        actions={
          <>
            <input
              ref={uploadRef}
              type="file"
              multiple
              className="sr-only"
              onChange={(event) => void upload(event.target.files)}
              aria-label="Upload file"
            />
            <Button variant="outline" size="sm" onClick={() => uploadRef.current?.click()} loading={uploading} icon={<Upload className="size-4" aria-hidden />}>
              Upload
            </Button>
            <Button variant="soft" size="sm" onClick={() => void previewOrganize()} loading={organizing} icon={<Sparkles className="size-4" aria-hidden />}>
              Preview tidy-up
            </Button>
          </>
        }
      />

      <div className="grid gap-4 xl:grid-cols-[240px_minmax(0,1fr)]">
        <div className="space-y-3">
          <Card className="p-3">
            <p className="px-1.5 pb-2 text-[10px] font-extrabold uppercase tracking-[0.12em] text-ink-faint">Permitted places</p>
            <div className="space-y-1">
              {loading && !roots ? (
                <>
                  <Skeleton className="h-10" />
                  <Skeleton className="h-10" />
                  <Skeleton className="h-10" />
                </>
              ) : (
                rootEntries.map((root) => {
                  const active = current === root.path
                  return (
                    <button
                      key={root.path}
                      type="button"
                      onClick={() => void loadDirectory(root.path)}
                      className={cn(
                        'ring-focus flex w-full items-center gap-2 rounded-xl px-2.5 py-2 text-left transition-colors',
                        active ? 'bg-accent-soft text-accent-strong' : 'text-ink-soft hover:bg-surface-muted/60',
                      )}
                    >
                      <Folder className={cn('size-4 shrink-0', active ? 'fill-current text-accent' : 'text-ink-faint')} aria-hidden />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[12px] font-bold">{root.label}</span>
                        <span className="block truncate font-mono text-[9px] text-ink-faint">{root.path}</span>
                      </span>
                      <ChevronRight className="size-3 text-ink-faint" aria-hidden />
                    </button>
                  )
                })
              )}
            </div>
            {roots && (
              <div className="mt-2 space-y-1 border-t border-line pt-2">
                <button
                  type="button"
                  onClick={() => void loadDirectory(roots.workspace)}
                  className="ring-focus flex w-full items-center gap-2 rounded-xl px-2.5 py-2 text-left text-[11px] font-semibold text-ink-soft hover:bg-surface-muted/60"
                >
                  <Sparkles className="size-3.5 text-accent" aria-hidden /> Workspace
                </button>
                <button
                  type="button"
                  onClick={() => void loadDirectory(roots.uploads)}
                  className="ring-focus flex w-full items-center gap-2 rounded-xl px-2.5 py-2 text-left text-[11px] font-semibold text-ink-soft hover:bg-surface-muted/60"
                >
                  <Upload className="size-3.5 text-accent" aria-hidden /> Uploads
                </button>
              </div>
            )}
          </Card>

          <Card className="p-3.5">
            <p className="flex items-center gap-1.5 text-[10px] font-extrabold uppercase tracking-[0.12em] text-ink-faint">
              <FileText className="size-3.5 text-accent" aria-hidden /> Recent documents
            </p>
            {recent.length === 0 ? (
              <p className="mt-2 text-[11px] leading-relaxed text-ink-faint">
                Files generated into your workspace will appear here.
              </p>
            ) : (
              <div className="mt-2 space-y-0.5">
                {recent.map((entry) => (
                  <button
                    key={entry.path}
                    type="button"
                    onClick={() => void openPreview(entry)}
                    className="ring-focus flex w-full items-center gap-2 rounded-lg px-1.5 py-1.5 text-left hover:bg-surface-muted/60"
                  >
                    <Glyph entry={entry} className="size-3.5 shrink-0 text-ink-faint" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[11px] font-semibold text-ink-soft">{entry.name}</span>
                      <span className="block text-[9px] text-ink-faint">{relativeTime(entry.modified)}</span>
                    </span>
                  </button>
                ))}
              </div>
            )}
          </Card>
        </div>

        <Card className="flex min-h-[520px] flex-col p-4 sm:p-5">
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="icon"
              variant="ghost"
              onClick={() => {
                const parent = parentPath(current)
                if (parent) void loadDirectory(parent)
              }}
              disabled={!canGoUp || loading}
              aria-label="Go to parent folder"
              title="Go up"
            >
              <ArrowLeft className="size-4" aria-hidden />
            </Button>
            <div className="min-w-0 flex-1 rounded-xl border border-line bg-surface-muted/50 px-3 py-2">
              <p className="truncate font-mono text-[11px] font-semibold text-ink-soft" title={current}>
                {current || 'Loading…'}
              </p>
            </div>
            <Button size="sm" variant="outline" onClick={() => void loadDirectory(current)} loading={loading} aria-label="Refresh folder">
              Refresh
            </Button>
          </div>

          <div className="mt-3 flex gap-2">
            <div className="relative flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-ink-faint" aria-hidden />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => event.key === 'Enter' && void runSearch()}
                placeholder="Search this folder…"
                aria-label="Search files"
                className="pl-9"
              />
            </div>
            <Button size="md" variant="soft" onClick={() => void runSearch()} loading={searching} aria-label="Run file search">
              Search
            </Button>
            {showingSearch && (
              <Button size="icon" variant="ghost" onClick={() => setSearchResults(null)} aria-label="Clear search">
                <X className="size-4" aria-hidden />
              </Button>
            )}
          </div>

          {showingSearch && (
            <p className="mt-2 text-[11px] font-semibold text-ink-faint">
              {entries.length} result{entries.length === 1 ? '' : 's'} for “{query}” under {current}
            </p>
          )}

          {error && !listing ? (
            <EmptyState
              mascot={<Mascot state="error" size={100} />}
              title="This folder is out of reach"
              description={error}
              action={
                roots ? (
                  <Button size="sm" variant="soft" onClick={() => void loadDirectory(roots.workspace)}>
                    Go to workspace
                  </Button>
                ) : undefined
              }
            />
          ) : loading && !listing ? (
            <div className="mt-3 space-y-1.5">
              {Array.from({ length: 6 }).map((_, index) => (
                <Skeleton key={index} className="h-12" />
              ))}
            </div>
          ) : searchError ? (
            <div className="mt-3 rounded-2xl border border-warning/25 bg-warning-soft/60 px-4 py-3 text-xs text-warning">{searchError}</div>
          ) : entries.length === 0 ? (
            <EmptyState
              mascot={<Mascot state={showingSearch ? 'thinking' : 'sleeping'} size={100} />}
              title={showingSearch ? 'No matching files' : 'This folder is empty'}
              description={showingSearch ? 'Try a different name or search a parent folder.' : 'Files you or Mochi create will appear here.'}
              className="mt-4 flex-1"
            />
          ) : (
            <div className="mochi-scroll mt-3 flex-1 overflow-y-auto pr-1">
              <div className="space-y-1">
                {entries.map((entry) => {
                  const { tone } = fileGlyph(entry.name)
                  return (
                    <div
                      key={entry.path}
                      className="group flex items-center gap-2.5 rounded-2xl border border-transparent px-2 py-2 transition-colors hover:border-line hover:bg-surface-muted/50"
                    >
                      <button
                        type="button"
                        onClick={() => (entry.is_dir ? void loadDirectory(entry.path) : void openPreview(entry))}
                        className="ring-focus flex min-w-0 flex-1 items-center gap-2.5 rounded-xl text-left"
                      >
                        <span
                          className={cn(
                            'grid size-9 shrink-0 place-items-center rounded-xl',
                            entry.is_dir
                              ? 'bg-accent-soft text-accent-strong'
                              : tone === 'danger'
                                ? 'bg-danger-soft text-danger'
                                : tone === 'success'
                                  ? 'bg-success-soft text-success'
                                  : tone === 'warning'
                                    ? 'bg-warning-soft text-warning'
                                    : tone === 'info'
                                      ? 'bg-info-soft text-info'
                                      : 'bg-surface-muted text-ink-faint',
                          )}
                        >
                          <Glyph entry={entry} className="size-4" />
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[13px] font-bold text-ink">{entry.name}</span>
                          <span className="block truncate text-[10px] text-ink-faint">
                            {entry.is_dir ? 'Folder' : formatBytes(entry.size)} · {formatDateTime(entry.modified)}
                          </span>
                        </span>
                      </button>
                      {!entry.is_dir && (
                        <a
                          href={api.downloadUrl(entry.path)}
                          className="ring-focus rounded-lg p-1.5 text-ink-faint opacity-0 transition-opacity hover:bg-surface-strong hover:text-accent-strong focus-visible:opacity-100 group-hover:opacity-100"
                          aria-label={`Download ${entry.name}`}
                          title="Download"
                        >
                          <Download className="size-3.5" aria-hidden />
                        </a>
                      )}
                      <button
                        type="button"
                        onClick={() => void openPreview(entry)}
                        className="ring-focus rounded-lg p-1.5 text-ink-faint opacity-0 transition-opacity hover:bg-surface-strong hover:text-accent-strong focus-visible:opacity-100 group-hover:opacity-100"
                        aria-label={`Preview ${entry.name}`}
                        title="Preview"
                      >
                        <Eye className="size-3.5" aria-hidden />
                      </button>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-line pt-3 text-[10px] font-semibold text-ink-faint">
            <span className="inline-flex items-center gap-1">
              <FolderTree className="size-3" aria-hidden />
              {listing?.count ?? 0} entries · sandboxed browsing
            </span>
            <span className="inline-flex items-center gap-1">
              <HardDriveDownload className="size-3" aria-hidden />
              Downloads go through the backend policy
            </span>
          </div>
        </Card>
      </div>

      {organize && (
        <Modal
          open
          onClose={() => setOrganize(null)}
          title="Tidy-up preview"
          description="Nothing has been moved. This is the backend's dry-run plan."
          size="lg"
          footer={
            <Button variant="soft" onClick={() => setOrganize(null)}>
              Close
            </Button>
          }
        >
          <p className="rounded-2xl border border-line bg-surface-muted/50 px-3 py-2 text-xs font-semibold text-ink-soft">{organize.evidence}</p>
          <pre className="mochi-scroll mt-3 max-h-[50vh] overflow-auto rounded-2xl border border-line bg-surface-strong p-3 font-mono text-[11px] leading-relaxed text-ink-soft">
            {JSON.stringify(organize.data, null, 2)}
          </pre>
        </Modal>
      )}

      <Modal
        open={Boolean(preview)}
        onClose={() => setPreview(null)}
        title={preview ? baseName(preview.entry.path) : 'Preview'}
        description={preview?.entry.path}
        size="xl"
        footer={
          preview ? (
            <>
              <a href={api.downloadUrl(preview.entry.path)} className="ring-focus inline-flex h-10 items-center gap-2 rounded-2xl border border-line-strong bg-surface px-4 text-sm font-bold text-ink hover:border-accent/40">
                <Download className="size-4" aria-hidden /> Download
              </a>
              <Button variant="soft" onClick={() => setPreview(null)}>
                Close
              </Button>
            </>
          ) : undefined
        }
      >
        {preview ? (
          preview.loading ? (
            <div className="flex items-center gap-2 py-10 text-sm text-ink-faint">
              <Loader2 className="size-4 animate-spin" aria-hidden /> Reading through the sandbox…
            </div>
          ) : (
            <div className="space-y-3">
              <div className="flex flex-wrap gap-1.5">
                <Badge tone="neutral">{preview.entry.is_dir ? 'folder' : extensionOf(preview.entry.path) || 'file'}</Badge>
                {!preview.entry.is_dir && <Badge tone="neutral">{formatBytes(preview.entry.size)}</Badge>}
                <Badge tone="neutral">{formatDateTime(preview.entry.modified)}</Badge>
              </div>

              {metadataRows(preview.metadata).length > 0 && (
                <div className="grid gap-x-4 gap-y-1 rounded-2xl border border-line bg-surface-muted/40 p-3 text-[11px] sm:grid-cols-2">
                  {metadataRows(preview.metadata).map(([key, value]) => (
                    <div key={key} className="flex min-w-0 gap-2">
                      <span className="w-20 shrink-0 font-bold text-ink-faint">{key}</span>
                      <span className="min-w-0 flex-1 break-all font-mono text-ink-soft">{value}</span>
                    </div>
                  ))}
                </div>
              )}

              {preview.error && (
                <p className="rounded-2xl border border-warning/25 bg-warning-soft/60 px-3 py-2 text-xs text-warning">
                  {preview.error}
                </p>
              )}

              {preview.entry.is_dir ? (
                <Button variant="soft" onClick={() => { void loadDirectory(preview.entry.path); setPreview(null) }}>
                  Open folder
                </Button>
              ) : IMAGE_EXTENSIONS.has(extensionOf(preview.entry.path)) ? (
                <div className="overflow-hidden rounded-2xl border border-line bg-surface-muted/40">
                  <img src={api.downloadUrl(preview.entry.path)} alt={baseName(preview.entry.path)} className="max-h-[60vh] w-full object-contain" />
                </div>
              ) : extensionOf(preview.entry.path) === 'pdf' ? (
                <iframe title={`Preview of ${baseName(preview.entry.path)}`} src={api.downloadUrl(preview.entry.path)} className="h-[60vh] w-full rounded-2xl border border-line" />
              ) : preview.content !== null ? (
                <div>
                  <pre className="mochi-scroll max-h-[55vh] overflow-auto whitespace-pre-wrap rounded-2xl border border-line bg-surface-strong p-3.5 font-mono text-[11px] leading-relaxed text-ink-soft">
                    {preview.content}
                  </pre>
                  {preview.truncated && <p className="mt-1.5 text-[10px] font-semibold text-warning">Preview truncated by the backend read limit.</p>}
                </div>
              ) : (
                <div className="rounded-2xl border border-dashed border-line px-4 py-8 text-center text-xs text-ink-faint">
                  No inline preview is available for this file type. Use Download to open it with a desktop app.
                </div>
              )}

              <p className="break-all font-mono text-[10px] text-ink-faint">{safeFileUrl(preview.entry.path)}</p>
            </div>
          )
        ) : null}
      </Modal>
    </div>
  )
}

function metadataRows(metadata: Record<string, unknown> | null): Array<[string, string]> {
  if (!metadata) return []
  const rows: Array<[string, string]> = []
  if (typeof metadata.is_dir === 'boolean') rows.push(['Kind', metadata.is_dir ? 'folder' : 'file'])
  if (typeof metadata.size === 'number') rows.push(['Size', formatBytes(metadata.size)])
  if (typeof metadata.extension === 'string' && metadata.extension) rows.push(['Extension', metadata.extension])
  if (typeof metadata.created === 'string') rows.push(['Created', formatDateTime(metadata.created)])
  if (typeof metadata.modified === 'string') rows.push(['Modified', formatDateTime(metadata.modified)])
  return rows
}
