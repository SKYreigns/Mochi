import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}

const DATE_FALLBACK = '—'

export function parseDate(value: string | null | undefined): Date | null {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

export function formatDateTime(value: string | null | undefined): string {
  const date = parseDate(value)
  if (!date) return DATE_FALLBACK
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

export function formatDate(value: string | null | undefined): string {
  const date = parseDate(value)
  if (!date) return DATE_FALLBACK
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).format(date)
}

export function formatTime(value: string | null | undefined): string {
  const date = parseDate(value)
  if (!date) return DATE_FALLBACK
  return new Intl.DateTimeFormat(undefined, {
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

export function relativeTime(value: string | null | undefined): string {
  const date = parseDate(value)
  if (!date) return DATE_FALLBACK
  const diffSeconds = Math.round((date.getTime() - Date.now()) / 1000)
  const absolute = Math.abs(diffSeconds)
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })

  if (absolute < 45) return 'just now'
  if (absolute < 3600) return formatter.format(Math.round(diffSeconds / 60), 'minute')
  if (absolute < 86_400) return formatter.format(Math.round(diffSeconds / 3600), 'hour')
  if (absolute < 604_800) return formatter.format(Math.round(diffSeconds / 86_400), 'day')
  if (absolute < 2_592_000) return formatter.format(Math.round(diffSeconds / 604_800), 'week')
  return formatter.format(Math.round(diffSeconds / 2_592_000), 'month')
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return '—'
  if (bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  const value = bytes / 1024 ** index
  return `${value.toFixed(value >= 10 || index === 0 ? 0 : 1)} ${units[index]}`
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${Math.round(ms)} ms`
  return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`
}

export function greeting(date = new Date()): string {
  const hour = date.getHours()
  if (hour < 5) return 'Still up?'
  if (hour < 12) return 'Good morning'
  if (hour < 17) return 'Good afternoon'
  if (hour < 22) return 'Good evening'
  return 'Hey there'
}

const TOOL_LABELS: Record<string, string> = {
  'files.list_directory': 'Looking through your folders',
  'files.read_file': 'Reading a file',
  'files.get_metadata': 'Checking file details',
  'files.search_files': 'Searching your files',
  'files.move_file': 'Moving a file',
  'files.copy_file': 'Copying a file',
  'files.delete_file': 'Removing a file',
  'files.create_directory': 'Creating a folder',
  'files.write_file': 'Writing a file',
  'desktop.take_screenshot': 'Taking a screenshot',
  'desktop.get_active_window': 'Checking the active window',
  'desktop.list_windows': 'Looking at open windows',
  'browser.open': 'Opening the browser',
  'browser.navigate': 'Browsing to a page',
  'browser.get_page_text': 'Reading a web page',
  'browser.screenshot': 'Capturing the browser',
  'browser.click': 'Clicking on a page',
  'browser.type': 'Typing on a page',
  'system.get_cpu_usage': 'Checking the CPU',
  'system.get_ram_usage': 'Checking memory',
  'system.get_running_processes': 'Listing running apps',
  'system.get_disk_usage': 'Checking disk space',
  'system.get_battery_status': 'Checking the battery',
  'system.get_os_information': 'Checking your system',
  'documents.create_pdf': 'Creating a PDF',
  'documents.create_docx': 'Creating a document',
  'documents.convert': 'Converting a document',
  'memory.save': 'Saving to memory',
  'memory.search': 'Searching memories',
  'web.search': 'Searching the web',
  'web.fetch': 'Fetching a web page',
}

export function humanizeTool(name: string): string {
  if (TOOL_LABELS[name]) return TOOL_LABELS[name]
  const cleaned = name.replace(/[._]/g, ' ').trim()
  return cleaned.charAt(0).toUpperCase() + cleaned.slice(1)
}

export function titleCase(value: string): string {
  return value
    .replace(/[._]/g, ' ')
    .trim()
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
}

export function statusLabel(status: string): string {
  switch (status) {
    case 'queued':
      return 'Queued'
    case 'planning':
      return 'Planning'
    case 'running':
      return 'Working'
    case 'waiting_for_approval':
      return 'Waiting for you'
    case 'completed':
      return 'Finished'
    case 'failed':
      return 'Needs attention'
    case 'cancelled':
      return 'Cancelled'
    case 'blocked':
      return 'Blocked'
    default:
      return titleCase(status || 'unknown')
  }
}

export type Tone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger' | 'info'

export function taskTone(status: string): Tone {
  switch (status) {
    case 'completed':
      return 'success'
    case 'failed':
      return 'danger'
    case 'waiting_for_approval':
      return 'warning'
    case 'running':
    case 'planning':
      return 'accent'
    case 'cancelled':
      return 'neutral'
    default:
      return 'info'
  }
}

export function approvalTone(status: string): Tone {
  switch (status) {
    case 'pending':
      return 'warning'
    case 'approved':
      return 'success'
    case 'denied':
      return 'danger'
    case 'cancelled':
    case 'expired':
      return 'neutral'
    default:
      return 'info'
  }
}

export function isActiveStatus(status: string): boolean {
  return ['queued', 'planning', 'running', 'waiting_for_approval'].includes(status)
}

export function initials(value: string): string {
  const parts = value.trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return 'M'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
}

export function truncate(value: string, length = 60): string {
  if (value.length <= length) return value
  return `${value.slice(0, length - 1)}…`
}

export function safeFileUrl(path: string): string {
  return path.replace(/\\/g, '/')
}

export function pluralize(count: number, singular: string, plural?: string): string {
  return count === 1 ? singular : plural ?? `${singular}s`
}

export function percent(value: number): string {
  return `${Math.round(value)}%`
}

export function baseName(path: string): string {
  const normalized = path.replace(/\\/g, '/').replace(/\/+$/, '')
  return normalized.split('/').pop() || path
}

export function parentPath(path: string): string | null {
  const normalized = path.replace(/\\/g, '/').replace(/\/+$/, '')
  const index = normalized.lastIndexOf('/')
  if (index <= 2) return null
  return normalized.slice(0, index)
}

export function fileGlyph(name: string): {
  kind: 'folder' | 'image' | 'pdf' | 'document' | 'sheet' | 'code' | 'archive' | 'file'
  tone: Tone
} {
  if (!name.includes('.')) return { kind: 'folder', tone: 'accent' }
  const extension = name.split('.').pop()?.toLowerCase() ?? ''
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'ico'].includes(extension))
    return { kind: 'image', tone: 'info' }
  if (extension === 'pdf') return { kind: 'pdf', tone: 'danger' }
  if (['doc', 'docx', 'odt', 'rtf', 'txt', 'md'].includes(extension))
    return { kind: 'document', tone: 'accent' }
  if (['xls', 'xlsx', 'csv', 'ods'].includes(extension)) return { kind: 'sheet', tone: 'success' }
  if (['js', 'ts', 'tsx', 'jsx', 'py', 'json', 'html', 'css', 'rs', 'sql', 'sh'].includes(extension))
    return { kind: 'code', tone: 'warning' }
  if (['zip', 'rar', '7z', 'tar', 'gz'].includes(extension))
    return { kind: 'archive', tone: 'warning' }
  return { kind: 'file', tone: 'neutral' }
}
