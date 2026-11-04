import { z } from 'zod'

export const memorySchema = z.object({
  key: z.string().trim().min(1, 'Give this memory a short name.').max(200, 'Keep the name under 200 characters.'),
  value: z
    .string()
    .trim()
    .min(1, 'Add the detail Mochi should remember.')
    .max(8000, 'Memory values are limited to 8000 characters.'),
  kind: z.enum(['preference', 'fact', 'website', 'setting', 'note', 'workflow']),
  tags: z.string().trim().max(300, 'Keep tags under 300 characters.').optional().default(''),
  pinned: z.boolean().optional().default(false),
})

export type MemoryFormValues = z.infer<typeof memorySchema>

export const workflowStepSchema = z
  .object({
    name: z.string().trim().min(1, 'Every step needs a name.').max(200),
    kind: z.enum(['instruction', 'tool']),
    instruction: z.string().trim().optional().default(''),
    tool: z.string().trim().optional().default(''),
  })
  .superRefine((step, ctx) => {
    if (step.kind === 'instruction' && !step.instruction) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['instruction'],
        message: 'Describe what Mochi should do in this step.',
      })
    }
    if (step.kind === 'tool' && !step.tool) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['tool'],
        message: 'Enter the backend tool name for this step.',
      })
    }
  })

export const workflowSchema = z.object({
  name: z.string().trim().min(1, 'Name your workflow.').max(200),
  description: z.string().trim().max(1000).optional().default(''),
  enabled: z.boolean().optional().default(true),
  steps: z.array(workflowStepSchema).min(1, 'Add at least one step.').max(30),
})

export type WorkflowFormValues = z.infer<typeof workflowSchema>

export const scheduleSchema = z
  .object({
    name: z.string().trim().min(1, 'Name this schedule.').max(200),
    instruction: z.string().trim().max(5000).optional().default(''),
    workflow_id: z.string().optional().nullable(),
    schedule_type: z.enum(['once', 'interval', 'daily']),
    run_at: z.string().optional().default(''),
    interval_minutes: z.number().int().min(1, 'At least one minute.').max(10_080, 'Keep intervals under 7 days.').optional(),
    time_of_day: z.string().optional().default(''),
  })
  .superRefine((value, ctx) => {
    if (!value.workflow_id && !value.instruction.trim()) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['instruction'],
        message: 'Add an instruction or choose a workflow.',
      })
    }
    if (value.schedule_type === 'once') {
      if (!value.run_at) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['run_at'],
          message: 'Choose when this should run.',
        })
      } else if (Number.isNaN(new Date(value.run_at).getTime())) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['run_at'],
          message: 'That date could not be understood.',
        })
      }
    }
    if (value.schedule_type === 'interval' && (!value.interval_minutes || value.interval_minutes < 1)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['interval_minutes'],
        message: 'Set an interval in minutes.',
      })
    }
    if (value.schedule_type === 'daily' && !value.time_of_day) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['time_of_day'],
        message: 'Choose a time of day.',
      })
    }
  })

export type ScheduleFormValues = z.infer<typeof scheduleSchema>

export const modelSettingsSchema = z.object({
  provider: z.enum(['ollama', 'openai', 'openai_compatible', 'openrouter', 'groq', 'lmstudio', 'mock']),
  model: z.string().trim().min(1, 'Choose or type a model name.'),
  base_url: z
    .string()
    .trim()
    .refine(
      (value) => value === '' || /^https?:\/\/.+/i.test(value),
      'Base URLs must start with http:// or https://',
    )
    .optional()
    .default(''),
  temperature: z.number().min(0, 'Temperature must be 0 or more.').max(2, 'Temperature must be 2 or less.'),
})

export type ModelSettingsValues = z.infer<typeof modelSettingsSchema>

export function firstIssue(error: z.ZodError): Record<string, string> {
  const result: Record<string, string> = {}
  for (const issue of error.issues) {
    const key = issue.path.join('.') || 'form'
    if (!result[key]) result[key] = issue.message
  }
  return result
}
