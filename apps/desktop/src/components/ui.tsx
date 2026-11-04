import {
  useEffect,
  useId,
  useRef,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { Check, ChevronDown, Loader2, X } from 'lucide-react'
import { cn, type Tone } from '@/lib/utils'

/* -------------------------------------------------------------------------- */
/* Button                                                                     */
/* -------------------------------------------------------------------------- */

type ButtonVariant = 'primary' | 'soft' | 'outline' | 'ghost' | 'danger' | 'success'
type ButtonSize = 'sm' | 'md' | 'lg' | 'icon'

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary:
    'bg-accent text-accent-contrast shadow-[0_10px_24px_-12px_var(--accent)] hover:bg-accent-strong active:translate-y-px',
  soft: 'bg-accent-soft text-accent-strong hover:brightness-[0.97] active:translate-y-px',
  outline:
    'border border-line-strong bg-surface text-ink hover:border-accent/50 hover:bg-surface-strong active:translate-y-px',
  ghost: 'text-ink-soft hover:bg-surface-muted hover:text-ink active:translate-y-px',
  danger: 'bg-danger-soft text-danger hover:brightness-[0.97] active:translate-y-px',
  success: 'bg-success-soft text-success hover:brightness-[0.97] active:translate-y-px',
}

const BUTTON_SIZES: Record<ButtonSize, string> = {
  sm: 'h-8 gap-1.5 px-3 text-[13px] rounded-xl',
  md: 'h-10 gap-2 px-4 text-sm rounded-2xl',
  lg: 'h-12 gap-2.5 px-6 text-[15px] rounded-2xl',
  icon: 'h-9 w-9 rounded-xl',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
  icon?: ReactNode
  iconRight?: ReactNode
}

export function Button({
  variant = 'primary',
  size = 'md',
  loading = false,
  icon,
  iconRight,
  className,
  children,
  disabled,
  type = 'button',
  ...props
}: ButtonProps) {
  return (
    <button
      type={type}
      disabled={disabled || loading}
      className={cn(
        'ring-focus inline-flex select-none items-center justify-center font-bold transition-all duration-200 disabled:pointer-events-none disabled:opacity-45',
        BUTTON_VARIANTS[variant],
        BUTTON_SIZES[size],
        className,
      )}
      {...props}
    >
      {loading ? <Loader2 className="size-4 animate-spin" aria-hidden /> : icon}
      {children}
      {!loading && iconRight}
    </button>
  )
}

/* -------------------------------------------------------------------------- */
/* Card                                                                       */
/* -------------------------------------------------------------------------- */

export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  tone?: 'default' | 'plain' | 'strong'
  interactive?: boolean
}

export function Card({ tone = 'default', interactive = false, className, ...props }: CardProps) {
  return (
    <div
      className={cn(
        'rounded-3xl border border-line transition-all duration-300',
        tone === 'default' && 'bg-surface shadow-[var(--shadow-card)] backdrop-blur-xl',
        tone === 'plain' && 'bg-surface-muted',
        tone === 'strong' && 'bg-surface-strong shadow-[var(--shadow-card)]',
        interactive &&
          'cursor-pointer hover:-translate-y-0.5 hover:border-accent/30 hover:shadow-[var(--shadow-soft)]',
        className,
      )}
      {...props}
    />
  )
}

/* -------------------------------------------------------------------------- */
/* Badge                                                                      */
/* -------------------------------------------------------------------------- */

const TONE_CLASSES: Record<Tone, string> = {
  neutral: 'bg-surface-muted text-ink-soft border-line',
  accent: 'bg-accent-soft text-accent-strong border-accent/20',
  success: 'bg-success-soft text-success border-success/20',
  warning: 'bg-warning-soft text-warning border-warning/20',
  danger: 'bg-danger-soft text-danger border-danger/20',
  info: 'bg-info-soft text-info border-info/20',
}

export function Badge({
  tone = 'neutral',
  className,
  children,
  dot = false,
}: {
  tone?: Tone
  className?: string
  children: ReactNode
  dot?: boolean
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-bold tracking-wide',
        TONE_CLASSES[tone],
        className,
      )}
    >
      {dot && <span className="size-1.5 rounded-full bg-current" aria-hidden />}
      {children}
    </span>
  )
}

/* -------------------------------------------------------------------------- */
/* Form controls                                                              */
/* -------------------------------------------------------------------------- */

const FIELD_BASE =
  'w-full rounded-2xl border border-line bg-surface-strong/80 px-3.5 py-2.5 text-sm text-ink placeholder:text-ink-faint transition-colors focus:border-accent/60 focus:outline-none focus:ring-2 focus:ring-accent/25 disabled:opacity-50'

export function Field({
  label,
  hint,
  error,
  children,
  htmlFor,
  className,
}: {
  label: string
  hint?: string
  error?: string
  children: ReactNode
  htmlFor?: string
  className?: string
}) {
  return (
    <div className={cn('space-y-1.5', className)}>
      <label htmlFor={htmlFor} className="block text-xs font-bold uppercase tracking-[0.08em] text-ink-soft">
        {label}
      </label>
      {children}
      {error ? (
        <p className="text-xs font-semibold text-danger">{error}</p>
      ) : hint ? (
        <p className="text-xs text-ink-faint">{hint}</p>
      ) : null}
    </div>
  )
}

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(FIELD_BASE, className)} {...props} />
}

export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn(FIELD_BASE, 'resize-none leading-relaxed', className)} {...props} />
}

export function Select({ className, children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <div className="relative">
      <select className={cn(FIELD_BASE, 'appearance-none pr-9', className)} {...props}>
        {children}
      </select>
      <ChevronDown
        className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-ink-faint"
        aria-hidden
      />
    </div>
  )
}

export function Switch({
  checked,
  onChange,
  label,
  description,
  disabled = false,
}: {
  checked: boolean
  onChange: (next: boolean) => void
  label: string
  description?: string
  disabled?: boolean
}) {
  const id = useId()
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="min-w-0">
        <label htmlFor={id} className="block text-sm font-bold text-ink">
          {label}
        </label>
        {description && <p className="mt-0.5 text-xs leading-relaxed text-ink-faint">{description}</p>}
      </div>
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          'ring-focus relative mt-0.5 h-6 w-11 shrink-0 rounded-full border transition-colors duration-200 disabled:opacity-40',
          checked ? 'border-accent/40 bg-accent' : 'border-line-strong bg-surface-muted',
        )}
      >
        <span
          className={cn(
            'absolute top-1/2 size-4 -translate-y-1/2 rounded-full bg-white shadow-sm transition-all duration-200',
            checked ? 'left-[22px]' : 'left-1',
          )}
        />
      </button>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Progress                                                                   */
/* -------------------------------------------------------------------------- */

export function Progress({
  value,
  tone = 'accent',
  label,
  className,
}: {
  value: number
  tone?: Tone
  label?: string
  className?: string
}) {
  const clamped = Math.max(0, Math.min(100, value))
  const fill: Record<Tone, string> = {
    neutral: 'bg-ink-faint',
    accent: 'bg-accent',
    success: 'bg-success',
    warning: 'bg-warning',
    danger: 'bg-danger',
    info: 'bg-info',
  }
  return (
    <div
      className={cn('h-2 w-full overflow-hidden rounded-full bg-surface-muted', className)}
      role="progressbar"
      aria-valuenow={Math.round(clamped)}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label ?? 'Progress'}
    >
      <div
        className={cn('h-full rounded-full transition-[width] duration-500 ease-out', fill[tone])}
        style={{ width: `${clamped}%` }}
      />
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Empty state / skeleton                                                     */
/* -------------------------------------------------------------------------- */

export function EmptyState({
  icon,
  mascot,
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode
  mascot?: ReactNode
  title: string
  description?: string
  action?: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-3 rounded-3xl border border-dashed border-line-strong px-6 py-12 text-center',
        className,
      )}
    >
      {mascot}
      {icon && !mascot && (
        <div className="grid size-12 place-items-center rounded-2xl bg-accent-soft text-accent-strong">
          {icon}
        </div>
      )}
      <div className="space-y-1">
        <p className="text-base font-extrabold text-ink">{title}</p>
        {description && <p className="mx-auto max-w-sm text-sm leading-relaxed text-ink-faint">{description}</p>}
      </div>
      {action}
    </div>
  )
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('skeleton rounded-2xl', className)} aria-hidden />
}

/* -------------------------------------------------------------------------- */
/* Tabs                                                                       */
/* -------------------------------------------------------------------------- */

export function Tabs<T extends string>({
  items,
  value,
  onChange,
  className,
  ariaLabel = 'Sections',
}: {
  items: Array<{ value: T; label: string; count?: number }>
  value: T
  onChange: (value: T) => void
  className?: string
  ariaLabel?: string
}) {
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className={cn('no-scrollbar flex gap-1 overflow-x-auto rounded-2xl bg-surface-muted p-1', className)}
    >
      {items.map((item) => {
        const selected = item.value === value
        return (
          <button
            key={item.value}
            role="tab"
            type="button"
            aria-selected={selected}
            onClick={() => onChange(item.value)}
            className={cn(
              'ring-focus relative shrink-0 rounded-xl px-3.5 py-1.5 text-[13px] font-bold transition-colors',
              selected
                ? 'bg-surface-strong text-ink shadow-sm'
                : 'text-ink-soft hover:bg-surface/60 hover:text-ink',
            )}
          >
            {item.label}
            {typeof item.count === 'number' && (
              <span
                className={cn(
                  'ml-1.5 rounded-full px-1.5 py-0.5 text-[10px] font-extrabold',
                  selected ? 'bg-accent-soft text-accent-strong' : 'bg-surface/70 text-ink-faint',
                )}
              >
                {item.count}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Modal                                                                      */
/* -------------------------------------------------------------------------- */

const MODAL_SIZES = {
  sm: 'max-w-md',
  md: 'max-w-lg',
  lg: 'max-w-2xl',
  xl: 'max-w-4xl',
} as const

export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = 'md',
  closeLabel = 'Close dialog',
  dismissible = true,
}: {
  open: boolean
  onClose: () => void
  title: string
  description?: string
  children: ReactNode
  footer?: ReactNode
  size?: keyof typeof MODAL_SIZES
  closeLabel?: string
  dismissible?: boolean
}) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const titleId = useId()
  const descriptionId = useId()
  const reduced = useReducedMotion()

  useEffect(() => {
    if (!open) return
    const previous = document.activeElement as HTMLElement | null
    const timer = window.setTimeout(() => {
      const focusable = dialogRef.current?.querySelector<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      )
      focusable?.focus()
    }, 30)

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && dismissible) {
        event.stopPropagation()
        onClose()
      }
      if (event.key === 'Tab' && dialogRef.current) {
        const nodes = Array.from(
          dialogRef.current.querySelectorAll<HTMLElement>(
            'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
          ),
        ).filter((node) => node.offsetParent !== null)
        if (nodes.length === 0) return
        const first = nodes[0]
        const last = nodes[nodes.length - 1]
        if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault()
          first.focus()
        } else if (event.shiftKey && document.activeElement === first) {
          event.preventDefault()
          last.focus()
        }
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      window.clearTimeout(timer)
      document.removeEventListener('keydown', onKeyDown)
      previous?.focus?.()
    }
  }, [open, onClose, dismissible])

  return createPortal(
    <AnimatePresence>
      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: reduced ? 0 : 0.2 }}
            className="absolute inset-0 bg-[#2a2338]/35 backdrop-blur-[3px]"
            onClick={dismissible ? onClose : undefined}
            aria-hidden
          />
          <motion.div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            aria-describedby={description ? descriptionId : undefined}
            initial={{ opacity: 0, y: reduced ? 0 : 16, scale: reduced ? 1 : 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: reduced ? 0 : 10, scale: reduced ? 1 : 0.99 }}
            transition={{ duration: reduced ? 0 : 0.22, ease: [0.22, 1, 0.36, 1] }}
            className={cn(
              'glass-strong relative flex max-h-[88vh] w-full flex-col overflow-hidden rounded-[28px]',
              MODAL_SIZES[size],
            )}
          >
            <div className="flex items-start justify-between gap-4 border-b border-line px-6 py-5">
              <div>
                <h2 id={titleId} className="text-lg font-extrabold text-ink">
                  {title}
                </h2>
                {description && (
                  <p id={descriptionId} className="mt-1 text-sm leading-relaxed text-ink-soft">
                    {description}
                  </p>
                )}
              </div>
              {dismissible && (
                <Button variant="ghost" size="icon" onClick={onClose} aria-label={closeLabel} className="-mr-1 -mt-1">
                  <X className="size-4" aria-hidden />
                </Button>
              )}
            </div>
            <div className="mochi-scroll flex-1 overflow-y-auto px-6 py-5">{children}</div>
            {footer && (
              <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line bg-surface-muted/40 px-6 py-4">
                {footer}
              </div>
            )}
          </motion.div>
        </div>
      )}
    </AnimatePresence>,
    document.body,
  )
}

/* -------------------------------------------------------------------------- */
/* Misc                                                                       */
/* -------------------------------------------------------------------------- */

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn('size-4 animate-spin text-accent', className)} aria-hidden />
}

export function Checkmark({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-bold text-success">
      <Check className="size-3.5" aria-hidden />
      {label}
    </span>
  )
}

export function SectionHeading({
  title,
  description,
  action,
  className,
}: {
  title: string
  description?: string
  action?: ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex flex-wrap items-end justify-between gap-3', className)}>
      <div className="min-w-0">
        <h2 className="text-lg font-extrabold text-ink">{title}</h2>
        {description && <p className="mt-0.5 text-sm text-ink-faint">{description}</p>}
      </div>
      {action}
    </div>
  )
}

export function Metric({
  label,
  value,
  hint,
  tone = 'accent',
  icon,
}: {
  label: string
  value: ReactNode
  hint?: string
  tone?: Tone
  icon?: ReactNode
}) {
  return (
    <div className="rounded-2xl border border-line bg-surface p-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[11px] font-bold uppercase tracking-[0.08em] text-ink-faint">{label}</p>
        {icon && <span className={cn('text-ink-faint', tone === 'accent' && 'text-accent')}>{icon}</span>}
      </div>
      <p className="mt-1.5 truncate text-xl font-extrabold text-ink">{value}</p>
      {hint && <p className="mt-0.5 text-[11px] text-ink-faint">{hint}</p>}
    </div>
  )
}
