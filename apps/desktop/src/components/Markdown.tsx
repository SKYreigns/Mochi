import { isValidElement, useState, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import { Check, Copy } from 'lucide-react'
import { cn } from '@/lib/utils'

function extractText(node: ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(extractText).join('')
  if (isValidElement<{ children?: ReactNode }>(node)) return extractText(node.props.children)
  return ''
}

function CodeBlock({ children }: { children?: ReactNode }) {
  const [copied, setCopied] = useState(false)
  const text = extractText(children)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text.replace(/\n$/, ''))
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1800)
    } catch {
      setCopied(false)
    }
  }

  return (
    <div className="group relative">
      <button
        type="button"
        onClick={() => void copy()}
        className="ring-focus absolute right-2.5 top-2.5 z-10 inline-flex items-center gap-1 rounded-lg border border-line bg-surface-strong/90 px-2 py-1 text-[10px] font-bold text-ink-faint opacity-0 shadow-sm transition-all hover:text-ink focus-visible:opacity-100 group-hover:opacity-100"
        aria-label="Copy code"
      >
        {copied ? <Check className="size-3 text-success" aria-hidden /> : <Copy className="size-3" aria-hidden />}
        {copied ? 'Copied' : 'Copy'}
      </button>
      <pre>{children}</pre>
    </div>
  )
}

export function Markdown({ content, className }: { content: string; className?: string }) {
  return (
    <div className={cn('mochi-prose', className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
        components={{
          pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noreferrer noopener">
              {children}
            </a>
          ),
          input: ({ checked, ...props }) => (
            <input
              {...props}
              checked={checked}
              readOnly
              className="mr-1.5 size-3.5 accent-[var(--accent)] align-middle"
            />
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}

export function StreamingText({ text }: { text: string }) {
  return (
    <div className="relative">
      <Markdown content={text} />
      <span
        className="ml-0.5 inline-block h-4 w-[3px] animate-pulse rounded-full bg-accent align-text-bottom"
        aria-hidden
      />
      <span className="sr-only">Mochi is still writing</span>
    </div>
  )
}
