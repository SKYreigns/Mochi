import { describe, expect, it } from 'vitest'
import {
  firstIssue,
  memorySchema,
  modelSettingsSchema,
  scheduleSchema,
  workflowSchema,
} from '@/lib/schemas'

describe('memorySchema', () => {
  it('accepts a valid memory and applies defaults', () => {
    const result = memorySchema.safeParse({
      key: '  Favourite tea  ',
      value: '  Chamomile  ',
      kind: 'preference',
    })
    expect(result.success).toBe(true)
    if (result.success) {
      expect(result.data.key).toBe('Favourite tea')
      expect(result.data.value).toBe('Chamomile')
      expect(result.data.tags).toBe('')
      expect(result.data.pinned).toBe(false)
    }
  })

  it('rejects empty keys and unknown kinds', () => {
    expect(memorySchema.safeParse({ key: '   ', value: 'x', kind: 'fact' }).success).toBe(false)
    expect(memorySchema.safeParse({ key: 'x', value: 'x', kind: 'not-a-kind' }).success).toBe(false)
  })
})

describe('workflowSchema', () => {
  it('requires at least one step', () => {
    expect(workflowSchema.safeParse({ name: 'Morning', steps: [] }).success).toBe(false)
  })

  it('requires instruction text for instruction steps', () => {
    const result = workflowSchema.safeParse({
      name: 'Morning',
      steps: [{ name: 'Check', kind: 'instruction', instruction: '' }],
    })
    expect(result.success).toBe(false)
  })

  it('requires a tool name for tool steps', () => {
    const result = workflowSchema.safeParse({
      name: 'Morning',
      steps: [{ name: 'Check', kind: 'tool', tool: '' }],
    })
    expect(result.success).toBe(false)
  })

  it('accepts a complete workflow', () => {
    const result = workflowSchema.safeParse({
      name: 'Morning',
      steps: [
        { name: 'Read', kind: 'instruction', instruction: 'Summarise my inbox' },
        { name: 'CPU', kind: 'tool', tool: 'system.get_cpu_usage' },
      ],
    })
    expect(result.success).toBe(true)
  })
})

describe('scheduleSchema', () => {
  const base = {
    name: 'Check in',
    schedule_type: 'once',
    run_at: new Date(Date.now() + 60_000).toISOString(),
  }

  it('accepts a one-time schedule with an instruction', () => {
    expect(scheduleSchema.safeParse({ ...base, instruction: 'Say hello' }).success).toBe(true)
  })

  it('rejects an empty instruction without a workflow', () => {
    expect(scheduleSchema.safeParse({ ...base, instruction: '  ' }).success).toBe(false)
  })

  it('allows a workflow instead of an instruction', () => {
    expect(
      scheduleSchema.safeParse({ ...base, instruction: '', workflow_id: 'wf-1' }).success,
    ).toBe(true)
  })

  it('requires a valid run_at for one-time schedules', () => {
    expect(
      scheduleSchema.safeParse({ ...base, instruction: 'x', run_at: '' }).success,
    ).toBe(false)
    expect(
      scheduleSchema.safeParse({ ...base, instruction: 'x', run_at: 'nonsense' }).success,
    ).toBe(false)
  })

  it('requires an interval in range', () => {
    expect(
      scheduleSchema.safeParse({
        name: 'Repeat',
        instruction: 'x',
        schedule_type: 'interval',
        interval_minutes: 0,
      }).success,
    ).toBe(false)
    expect(
      scheduleSchema.safeParse({
        name: 'Repeat',
        instruction: 'x',
        schedule_type: 'interval',
        interval_minutes: 10_081,
      }).success,
    ).toBe(false)
    expect(
      scheduleSchema.safeParse({
        name: 'Repeat',
        instruction: 'x',
        schedule_type: 'interval',
        interval_minutes: 30,
      }).success,
    ).toBe(true)
  })

  it('requires a time of day for daily schedules', () => {
    expect(
      scheduleSchema.safeParse({
        name: 'Daily',
        instruction: 'x',
        schedule_type: 'daily',
        time_of_day: '',
      }).success,
    ).toBe(false)
    expect(
      scheduleSchema.safeParse({
        name: 'Daily',
        instruction: 'x',
        schedule_type: 'daily',
        time_of_day: '09:00',
      }).success,
    ).toBe(true)
  })
})

describe('modelSettingsSchema', () => {
  it('accepts supported providers', () => {
    expect(
      modelSettingsSchema.safeParse({
        provider: 'ollama',
        model: 'llama3.2:3b',
        base_url: '',
        temperature: 0.2,
      }).success,
    ).toBe(true)
  })

  it('rejects an unknown provider', () => {
    expect(
      modelSettingsSchema.safeParse({
        provider: 'not-real',
        model: 'x',
        temperature: 0.2,
      }).success,
    ).toBe(false)
  })

  it('requires an absolute base URL', () => {
    expect(
      modelSettingsSchema.safeParse({
        provider: 'openai',
        model: 'gpt',
        base_url: 'localhost:1234',
        temperature: 0.2,
      }).success,
    ).toBe(false)
  })

  it('bounds temperature to 0..2', () => {
    const at = (temperature: number) =>
      modelSettingsSchema.safeParse({ provider: 'ollama', model: 'x', temperature }).success
    expect(at(-0.1)).toBe(false)
    expect(at(2.1)).toBe(false)
    expect(at(1)).toBe(true)
  })
})

describe('firstIssue', () => {
  it('returns the first message per field', () => {
    const result = memorySchema.safeParse({ key: '', value: '', kind: 'fact' })
    expect(result.success).toBe(false)
    if (!result.success) {
      const issues = firstIssue(result.error)
      expect(issues.key).toBeTruthy()
      expect(issues.value).toBeTruthy()
    }
  })
})
