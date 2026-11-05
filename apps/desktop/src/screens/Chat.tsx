import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  AlertCircle,
  Archive,
  History,
  MessageCirclePlus,
  RotateCcw,
  Sparkles,
  Square,
  Wrench,
} from 'lucide-react'
import { useAppStore } from '@/stores/app'
import { useChatStore, type ChatMessageView } from '@/stores/chat'
import { Composer } from '@/components/Composer'
import { Mascot, MascotAvatar } from '@/components/Mascot'
import { Markdown, StreamingText } from '@/components/Markdown'
import { ToolCard } from '@/components/ToolCard'
import { TaskTimeline } from '@/components/TaskTimeline'
import { Badge, Button, Card, Modal, Skeleton } from '@/components/ui'
import { PageHeader } from '@/components/AppShell'
import { cn, formatTime, relativeTime, statusLabel, taskTone, truncate } from '@/lib/utils'

const SUGGESTIONS = [
  'Take a screenshot and tell me what is open on my desktop.',
  'Summarise the most recent file in my workspace.',
  'What can you help me automate on this computer?',
]

export function ChatScreen() {
  const { conversationId } = useParams<{ conversationId?: string }>()
  const navigate = useNavigate()
  const [draft, setDraft] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)

  const conversations = useChatStore((state) => state.conversations)
  const conversationsLoading = useChatStore((state) => state.conversationsLoading)
  const activeId = useChatStore((state) => state.activeId)
  const messages = useChatStore((state) => state.messages)
  const messagesLoading = useChatStore((state) => state.messagesLoading)
  const sending = useChatStore((state) => state.sending)
  const activeTaskId = useChatStore((state) => state.activeTaskId)
  const taskStatus = useChatStore((state) => state.taskStatus)
  const plan = useChatStore((state) => state.plan)
  const objective = useChatStore((state) => state.objective)
  const streamingText = useChatStore((state) => state.streamingText)
  const toolEvents = useChatStore((state) => state.toolEvents)
  const error = useChatStore((state) => state.error)
  const errorHint = useChatStore((state) => state.errorHint)
  const loadConversations = useChatStore((state) => state.loadConversations)
  const openConversation = useChatStore((state) => state.openConversation)
  const newChat = useChatStore((state) => state.newChat)
  const send = useChatStore((state) => state.send)
  const stop = useChatStore((state) => state.stop)
  const retry = useChatStore((state) => state.retry)
  const clearError = useChatStore((state) => state.clearError)
  const archiveConversation = useChatStore((state) => state.archiveConversation)

  const health = useAppStore((state) => state.health)
  const provider = health?.services.provider
  const emergencyStopped = health?.services.emergency_stopped ?? false

  useEffect(() => {
    void loadConversations()
  }, [loadConversations])

  useEffect(() => {
    if (conversationId && conversationId !== activeId) {
      void openConversation(conversationId)
    }
  }, [conversationId, activeId, openConversation])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages.length, streamingText, toolEvents.length, sending])

  const activeConversation = conversations.find((item) => item.id === activeId)
  const hasLive = sending || Boolean(streamingText) || toolEvents.length > 0 || Boolean(error)
  const showThinking = sending && !streamingText && toolEvents.length === 0 && !error

  const handleNewChat = () => {
    newChat()
    setDraft('')
    navigate('/chat')
  }

  const submit = async (text: string, attachments: ChatMessageView['attachments']) => {
    clearError()
    await send(text, attachments)
  }

  const conversationList = (
    <div className="space-y-1">
      {conversationsLoading && conversations.length === 0 ? (
        <>
          <Skeleton className="h-12" />
          <Skeleton className="h-12" />
          <Skeleton className="h-12" />
        </>
      ) : conversations.length === 0 ? (
        <p className="rounded-2xl border border-dashed border-line px-3 py-6 text-center text-xs text-ink-faint">
          Your conversations will live here.
        </p>
      ) : (
        conversations.map((conversation) => {
          const active = conversation.id === activeId
          return (
            <div
              key={conversation.id}
              className={cn(
                'group relative flex items-center rounded-2xl border transition-colors',
                active ? 'border-accent/30 bg-accent-soft/60' : 'border-transparent hover:bg-surface-muted/60',
              )}
            >
              <button
                type="button"
                onClick={() => {
                  navigate(`/chat/${conversation.id}`)
                  setHistoryOpen(false)
                }}
                className="ring-focus min-w-0 flex-1 rounded-2xl px-3 py-2.5 text-left"
              >
                <span className="block truncate text-[13px] font-bold text-ink">{conversation.title}</span>
                <span className="block truncate text-[11px] text-ink-faint">
                  {relativeTime(conversation.updated_at)}
                  {conversation.message_count > 0 ? ` · ${conversation.message_count} messages` : ''}
                </span>
              </button>
              <button
                type="button"
                onClick={() => void archiveConversation(conversation.id)}
                className="ring-focus mr-1 rounded-lg p-1.5 text-ink-faint opacity-0 transition-opacity hover:bg-surface-strong hover:text-danger focus-visible:opacity-100 group-hover:opacity-100"
                aria-label={`Archive ${conversation.title}`}
                title="Archive conversation"
              >
                <Archive className="size-3.5" aria-hidden />
              </button>
            </div>
          )
        })
      )}
    </div>
  )

  return (
    <div>
      <PageHeader
        eyebrow="Conversations"
        title="Chat with Mochi"
        description="Ask in your own words. Mochi will show every tool it uses and pause for approval when it matters."
        icon={<MessageCirclePlus className="size-5" aria-hidden />}
        actions={
          <>
            <Button variant="outline" size="sm" onClick={() => setHistoryOpen(true)} className="lg:hidden" icon={<History className="size-4" aria-hidden />}>
              History
            </Button>
            <Button variant="soft" size="sm" onClick={handleNewChat} icon={<Sparkles className="size-4" aria-hidden />}>
              New chat
            </Button>
          </>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[248px_minmax(0,1fr)] lg:items-start">
        <aside className="hidden lg:sticky lg:top-2 lg:block">
          <Card className="p-3">
            <div className="flex items-center justify-between px-1.5 pb-2">
              <p className="text-[11px] font-extrabold uppercase tracking-[0.1em] text-ink-faint">History</p>
              <span className="text-[10px] font-bold text-ink-faint">{conversations.length}</span>
            </div>
            <div className="mochi-scroll max-h-[calc(100vh-260px)] overflow-y-auto pr-0.5">{conversationList}</div>
          </Card>
        </aside>

        <section
          className="flex min-h-[68vh] flex-col overflow-hidden rounded-[28px] border border-line bg-surface/70 shadow-[var(--shadow-card)] backdrop-blur-2xl"
          aria-label="Conversation"
        >
          <header className="flex shrink-0 items-center gap-3 border-b border-line px-4 py-3">
            <MascotAvatar state={sending ? 'working' : error ? 'error' : 'idle'} size={38} />
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-extrabold text-ink">
                {activeConversation?.title ?? (activeId ? 'Conversation' : 'A fresh conversation')}
              </p>
              <p className="flex items-center gap-1.5 text-[11px] text-ink-faint">
                {sending ? (
                  <>
                    <span className="size-1.5 rounded-full bg-accent" aria-hidden />
                    {statusLabel(taskStatus ?? 'running')}
                  </>
                ) : (
                  <>
                    <span className="size-1.5 rounded-full bg-success" aria-hidden />
                    Ready when you are
                  </>
                )}
              </p>
            </div>
            {emergencyStopped && <Badge tone="danger">Paused</Badge>}
            {provider && !provider.configured && <Badge tone="warning">No model</Badge>}
          </header>

          <div className="mochi-scroll flex-1 overflow-y-auto px-3 py-5 sm:px-5" style={{ maxHeight: 'calc(100vh - 300px)', minHeight: 320 }}>
            {messagesLoading ? (
              <div className="space-y-3">
                <Skeleton className="h-16 w-2/3" />
                <Skeleton className="ml-auto h-12 w-1/2" />
                <Skeleton className="h-28 w-4/5" />
              </div>
            ) : messages.length === 0 && !hasLive ? (
              <div className="flex h-full flex-col items-center justify-center py-8 text-center">
                <Mascot state="listening" size={140} title="Mochi is listening" />
                <h2 className="mt-2 text-xl font-extrabold text-ink">Hi! I’m Mochi.</h2>
                <p className="mt-1 max-w-md text-sm leading-relaxed text-ink-soft">
                  Tell me what you need. I can look at your screen, browse the web, tidy files and run workflows — and I will
                  always be honest about what actually happened.
                </p>
                <div className="mt-4 flex max-w-xl flex-wrap justify-center gap-2">
                  {SUGGESTIONS.map((suggestion) => (
                    <button
                      key={suggestion}
                      type="button"
                      onClick={() => void send(suggestion)}
                      className="ring-focus rounded-full border border-line bg-surface-strong/70 px-3 py-1.5 text-[11px] font-semibold text-ink-soft transition-colors hover:border-accent/40 hover:text-accent-strong"
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="space-y-5">
                {messages.map((message) => (
                  <MessageBubble key={message.id} message={message} />
                ))}

                {hasLive && (
                  <div className="flex gap-2.5">
                    <MascotAvatar state={error ? 'error' : sending ? 'working' : 'success'} size={32} className="mt-1" />
                    <div className="min-w-0 flex-1 space-y-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[13px] font-extrabold text-ink">Mochi</span>
                        {taskStatus && (
                          <Badge tone={taskTone(taskStatus)} dot>
                            {statusLabel(taskStatus)}
                          </Badge>
                        )}
                        {activeTaskId && <span className="font-mono text-[10px] text-ink-faint">{truncate(activeTaskId, 18)}</span>}
                      </div>

                      {(objective || plan.length > 0) && (
                        <div className="rounded-2xl border border-line bg-surface-strong/70 p-3.5">
                          {objective && <p className="mb-2 text-[13px] font-bold text-ink-soft">{objective}</p>}
                          <TaskTimeline steps={plan} currentStep={-1} status={sending ? 'running' : taskStatus ?? undefined} />
                        </div>
                      )}

                      {toolEvents.length > 0 && (
                        <div className="space-y-1.5">
                          {toolEvents.map((event) => (
                            <ToolCard
                              key={event.id ?? `${event.key}-${event.status}-${event.durationMs ?? ''}`}
                              event={event}
                            />
                          ))}
                        </div>
                      )}

                      {showThinking && (
                        <div className="inline-flex items-center gap-2 rounded-2xl border border-line bg-surface-strong/70 px-3.5 py-2.5">
                          <span className="flex gap-1" aria-hidden>
                            {[0, 1, 2].map((index) => (
                              <span
                                key={index}
                                className="size-1.5 animate-bounce rounded-full bg-accent"
                                style={{ animationDelay: `${index * 0.12}s` }}
                              />
                            ))}
                          </span>
                          <span className="text-xs font-semibold text-ink-soft">
                            {taskStatus === 'planning' ? 'Planning a careful approach…' : 'Thinking it through…'}
                          </span>
                        </div>
                      )}

                      {streamingText && (
                        <div className="rounded-2xl rounded-tl-md border border-line bg-surface-strong/85 px-4 py-3">
                          <StreamingText text={streamingText} />
                        </div>
                      )}

                      {error && (
                        <div className="rounded-2xl border border-danger/25 bg-danger-soft/60 p-3.5">
                          <p className="flex items-start gap-2 text-sm font-bold text-danger">
                            <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden />
                            {error}
                          </p>
                          {errorHint && <p className="mt-1 pl-6 text-xs leading-relaxed text-ink-soft">{errorHint}</p>}
                          <div className="mt-2.5 flex gap-2 pl-6">
                            <Button size="sm" variant="outline" icon={<RotateCcw className="size-3.5" aria-hidden />} onClick={() => void retry()}>
                              Try again
                            </Button>
                            <Button size="sm" variant="ghost" onClick={clearError}>
                              Dismiss
                            </Button>
                          </div>
                        </div>
                      )}

                      {sending && (
                        <div className="flex flex-wrap items-center gap-2 text-[11px] text-ink-faint">
                          <Wrench className="size-3" aria-hidden />
                          Every step above comes from real backend events.
                          <Button size="sm" variant="ghost" onClick={() => void stop()} icon={<Square className="size-3 fill-current" aria-hidden />}>
                            Stop
                          </Button>
                        </div>
                      )}
                    </div>
                  </div>
                )}
                <div ref={endRef} />
              </div>
            )}
          </div>

          <div className="shrink-0 border-t border-line bg-surface/50 p-2.5 sm:p-3">
            <Composer
              value={draft}
              onChange={setDraft}
              onSubmit={submit}
              onStop={() => void stop()}
              sending={sending}
              placeholder="Ask Mochi anything…"
            />
            {!provider?.configured && (
              <p className="mt-1.5 px-2 text-[11px] font-semibold text-warning">
                Requests will fail until an AI model is configured in Settings.
              </p>
            )}
          </div>
        </section>
      </div>

      <Modal open={historyOpen} onClose={() => setHistoryOpen(false)} title="Conversation history" size="sm">
        {conversationList}
        <Button variant="soft" className="mt-3 w-full" onClick={handleNewChat} icon={<Sparkles className="size-4" aria-hidden />}>
          Start a new chat
        </Button>
      </Modal>
    </div>
  )
}

function MessageBubble({ message }: { message: ChatMessageView }) {
  const isUser = message.role === 'user'
  const isEmpty = !message.content.trim()

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[88%] sm:max-w-[76%]">
          <div className="rounded-3xl rounded-br-md border border-accent/20 bg-gradient-to-br from-accent-soft to-blush/50 px-4 py-2.5 shadow-sm">
            <p className="whitespace-pre-wrap text-[15px] leading-relaxed text-ink">{message.content}</p>
            {message.attachments.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {message.attachments.map((attachment) => (
                  <span
                    key={attachment.path}
                    className="rounded-lg border border-line bg-surface-strong/70 px-2 py-1 text-[10px] font-semibold text-ink-soft"
                  >
                    {attachment.name}
                  </span>
                ))}
              </div>
            )}
          </div>
          <p className="mt-1 pr-1 text-right text-[10px] text-ink-faint">{formatTime(message.created_at)}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex gap-2.5">
      <MascotAvatar state={message.tools?.some((tool) => tool.status === 'failed') ? 'error' : 'idle'} size={32} className="mt-1" />
      <div className="min-w-0 flex-1">
        <div className="mb-1 flex items-center gap-2">
          <span className="text-[13px] font-extrabold text-ink">Mochi</span>
          <span className="text-[10px] text-ink-faint">{formatTime(message.created_at)}</span>
        </div>
        {!isEmpty && (
          <div className="rounded-2xl rounded-tl-md border border-line bg-surface-strong/85 px-4 py-3">
            <Markdown content={message.content} />
          </div>
        )}
        {message.tools && message.tools.length > 0 && (
          <div className="mt-2 space-y-1.5">
            {message.tools.map((tool) => (
              <ToolCard key={tool.id ?? `${tool.key}-${tool.status}`} event={tool} />
            ))}
          </div>
        )}
        {isEmpty && (!message.tools || message.tools.length === 0) && (
          <p className="rounded-2xl border border-dashed border-line px-3 py-2 text-xs text-ink-faint">
            Mochi finished this task. Open the task in <a href="#/tasks" className="font-bold text-accent-strong">My Tasks</a> for the full history.
          </p>
        )}
      </div>
    </div>
  )
}
