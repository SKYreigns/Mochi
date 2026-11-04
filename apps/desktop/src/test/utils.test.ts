import { describe, expect, it } from 'vitest'
import {
  approvalTone,
  baseName,
  fileGlyph,
  formatBytes,
  greeting,
  humanizeTool,
  initials,
  isActiveStatus,
  parentPath,
  percent,
  pluralize,
  relativeTime,
  statusLabel,
  taskTone,
  titleCase,
  truncate,
} from '@/lib/utils'

describe('formatBytes', () => {
  it('returns a dash for missing values', () => {
    expect(formatBytes(null)).toBe('—')
    expect(formatBytes(undefined)).toBe('—')
  })

  it('formats zero and byte values', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(512)).toBe('512 B')
  })

  it('scales into larger units', () => {
    expect(formatBytes(1024)).toBe('1.0 KB')
    expect(formatBytes(1024 * 1024)).toBe('1.0 MB')
    expect(formatBytes(15 * 1024 * 1024 * 1024)).toBe('15 GB')
  })
})

describe('date helpers', () => {
  it('handles invalid dates', () => {
    expect(relativeTime(null)).toBe('—')
    expect(relativeTime('not-a-date')).toBe('—')
  })

  it('labels very recent timestamps', () => {
    expect(relativeTime(new Date().toISOString())).toBe('just now')
  })

  it('uses relative wording in the future', () => {
    const future = new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString()
    expect(relativeTime(future)).toMatch(/hour/)
  })
})

describe('greeting', () => {
  it('changes across the day', () => {
    const at = (hour: number) => new Date(2026, 0, 1, hour, 0, 0)
    expect(greeting(at(3))).toBe('Still up?')
    expect(greeting(at(9))).toBe('Good morning')
    expect(greeting(at(14))).toBe('Good afternoon')
    expect(greeting(at(19))).toBe('Good evening')
    expect(greeting(at(23))).toBe('Hey there')
  })
})

describe('tool + status labels', () => {
  it('humanizes known and unknown tools', () => {
    expect(humanizeTool('browser.navigate')).toBe('Browsing to a page')
    expect(humanizeTool('custom.do_thing')).toBe('Custom do thing')
  })

  it('maps task statuses to friendly labels', () => {
    expect(statusLabel('running')).toBe('Working')
    expect(statusLabel('waiting_for_approval')).toBe('Waiting for you')
    expect(statusLabel('completed')).toBe('Finished')
    expect(statusLabel('mystery')).toBe('Mystery')
  })

  it('maps tones for tasks and approvals', () => {
    expect(taskTone('completed')).toBe('success')
    expect(taskTone('failed')).toBe('danger')
    expect(taskTone('waiting_for_approval')).toBe('warning')
    expect(approvalTone('pending')).toBe('warning')
    expect(approvalTone('denied')).toBe('danger')
  })

  it('recognises active statuses', () => {
    expect(isActiveStatus('queued')).toBe(true)
    expect(isActiveStatus('running')).toBe(true)
    expect(isActiveStatus('completed')).toBe(false)
  })
})

describe('string helpers', () => {
  it('builds initials', () => {
    expect(initials('')).toBe('M')
    expect(initials('Mochi')).toBe('MO')
    expect(initials('Mochi Companion')).toBe('MC')
  })

  it('truncates with an ellipsis', () => {
    expect(truncate('short', 10)).toBe('short')
    expect(truncate('a very long sentence', 10)).toBe('a very lo…')
  })

  it('title-cases dotted names', () => {
    expect(titleCase('files.read_file')).toBe('Files Read File')
  })

  it('pluralizes', () => {
    expect(pluralize(1, 'task')).toBe('task')
    expect(pluralize(2, 'task')).toBe('tasks')
    expect(pluralize(2, 'memory', 'memories')).toBe('memories')
  })

  it('formats percentages', () => {
    expect(percent(0)).toBe('0%')
    expect(percent(42.6)).toBe('43%')
  })
})

describe('path helpers', () => {
  it('normalizes Windows and POSIX separators', () => {
    expect(baseName('C:\\Users\\me\\notes.txt')).toBe('notes.txt')
    expect(baseName('/home/me/notes.txt')).toBe('notes.txt')
    expect(parentPath('C:\\Users\\me\\notes.txt')).toBe('C:/Users/me')
  })

  it('refuses to walk above the root', () => {
    expect(parentPath('/a')).toBeNull()
    expect(parentPath('C:/')).toBeNull()
  })
})

describe('fileGlyph', () => {
  it('classifies common extensions', () => {
    expect(fileGlyph('notes.txt').kind).toBe('document')
    expect(fileGlyph('photo.png').kind).toBe('image')
    expect(fileGlyph('report.pdf').kind).toBe('pdf')
    expect(fileGlyph('data.csv').kind).toBe('sheet')
    expect(fileGlyph('main.ts').kind).toBe('code')
    expect(fileGlyph('bundle.zip').kind).toBe('archive')
    expect(fileGlyph('folder').kind).toBe('folder')
    expect(fileGlyph('file.unknown').kind).toBe('file')
  })
})
