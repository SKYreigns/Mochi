import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Cpu,
  Loader2,
  Mic,
  Paperclip,
  Send,
  Square,
  X,
} from 'lucide-react'
import { api, errorMessage } from '@/lib/api'
import type { ChatMessage } from '@/lib/types'
import { useAppStore } from '@/stores/app'
import { toast } from '@/stores/toast'
import { Badge, Button } from '@/components/ui'
import { cn, formatBytes } from '@/lib/utils'

export function Composer({
  value,
  onChange,
  onSubmit,
  onStop,
  sending = false,
  placeholder = 'Tell Mochi what you need help with…',
  autoFocus = false,
  className,
  modelHint = true,
}: {
  value: string
  onChange: (value: string) => void
  onSubmit: (text: string, attachments: ChatMessage['attachments']) => void | Promise<void>
  onStop?: () => void
  sending?: boolean
  placeholder?: string
  autoFocus?: boolean
  className?: string
  modelHint?: boolean
}) {
  const [attachments, setAttachments] = useState<ChatMessage['attachments']>([])
  const [uploading, setUploading] = useState(false)
  const [recording, setRecording] = useState(false)
  const [transcribing, setTranscribing] = useState(false)
  const [recordingSeconds, setRecordingSeconds] = useState(0)
  const [submitting, setSubmitting] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const timerRef = useRef<number | null>(null)
  const mounted = useRef(true)
  const navigate = useNavigate()

  const health = useAppStore((state) => state.health)
  const provider = health?.services.provider
  const voice = health?.services.voice

  useEffect(() => {
    if (autoFocus) textareaRef.current?.focus()
  }, [autoFocus])

  useEffect(() => {
    const element = textareaRef.current
    if (!element) return
    element.style.height = 'auto'
    element.style.height = `${Math.min(element.scrollHeight, 200)}px`
  }, [value])

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      if (timerRef.current) window.clearInterval(timerRef.current)
      recorderRef.current?.stream.getTracks().forEach((track) => track.stop())
    }
  }, [])

  const handleSubmit = async () => {
    const trimmed = value.trim()
    if (!trimmed || sending || submitting || uploading) return
    setSubmitting(true)
    try {
      await onSubmit(trimmed, attachments)
      setAttachments([])
      onChange('')
    } finally {
      if (mounted.current) setSubmitting(false)
    }
  }

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      void handleSubmit()
    }
  }

  const handleFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    setUploading(true)
    try {
      for (const file of Array.from(files).slice(0, 5)) {
        if (file.size > 25 * 1024 * 1024) {
          toast.warning('That file is too large', `${file.name} is larger than 25 MB.`)
          continue
        }
        const uploaded = await api.uploadFile(file)
        setAttachments((current) => [
          ...current,
          { name: uploaded.name, path: uploaded.path, mime: uploaded.mime, size: uploaded.size },
        ])
      }
    } catch (error) {
      toast.error('Could not attach that file', errorMessage(error))
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const startRecording = async () => {
    if (!voice?.whisper_installed) {
      toast.warning(
        'Voice input is not available yet',
        'Speech recognition needs the optional faster-whisper package on the backend. Install it and restart Mochi.',
      )
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const recorder = new MediaRecorder(stream)
      chunksRef.current = []
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data)
      }
      recorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop())
        if (timerRef.current) window.clearInterval(timerRef.current)
        timerRef.current = null
        setRecording(false)
        setRecordingSeconds(0)
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' })
        if (blob.size < 1200) return
        setTranscribing(true)
        try {
          const result = await api.transcribe(blob)
          if (result.text?.trim()) {
            onChange(`${value}${value.trim() ? ' ' : ''}${result.text.trim()}`)
          } else {
            toast.info('I could not hear any words', 'Try again a little closer to the microphone.')
          }
        } catch (error) {
          toast.error('Could not transcribe that recording', errorMessage(error))
        } finally {
          if (mounted.current) setTranscribing(false)
        }
      }
      recorder.start()
      recorderRef.current = recorder
      setRecording(true)
      setRecordingSeconds(0)
      timerRef.current = window.setInterval(() => setRecordingSeconds((seconds) => seconds + 1), 1000)
    } catch (error) {
      toast.error('Microphone unavailable', errorMessage(error))
    }
  }

  const stopRecording = () => {
    recorderRef.current?.stop()
    recorderRef.current = null
  }

  const toggleRecording = () => {
    if (recording) stopRecording()
    else void startRecording()
  }

  return (
    <div
      className={cn(
        'glass-strong rounded-[26px] p-2.5 transition-all duration-300 focus-within:border-accent/40 focus-within:shadow-[0_18px_44px_-24px_var(--accent)]',
        className,
      )}
    >
      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-1.5 px-1.5 pb-2 pt-1">
          {attachments.map((file) => (
            <span
              key={file.path}
              className="inline-flex max-w-[240px] items-center gap-1.5 rounded-xl border border-line bg-surface-muted px-2 py-1 text-[11px] font-semibold text-ink-soft"
            >
              <Paperclip className="size-3 shrink-0 text-ink-faint" aria-hidden />
              <span className="truncate">{file.name}</span>
              <span className="shrink-0 text-ink-faint">{formatBytes(file.size)}</span>
              <button
                type="button"
                onClick={() => setAttachments((current) => current.filter((item) => item.path !== file.path))}
                className="ring-focus shrink-0 rounded p-0.5 text-ink-faint hover:text-danger"
                aria-label={`Remove ${file.name}`}
              >
                <X className="size-3" aria-hidden />
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="flex items-end gap-2">
        <label className="sr-only" htmlFor="mochi-command">
          Message Mochi
        </label>
        <textarea
          id="mochi-command"
          ref={textareaRef}
          rows={1}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={recording ? 'Listening… tap the mic to stop.' : placeholder}
          className="mochi-scroll max-h-[200px] min-h-[44px] flex-1 resize-none bg-transparent px-2 py-2.5 text-[15px] leading-relaxed text-ink placeholder:text-ink-faint focus:outline-none"
          aria-label="Message Mochi"
          disabled={recording}
        />

        <div className="flex shrink-0 items-center gap-1 pb-0.5">
          <input
            ref={fileRef}
            type="file"
            multiple
            className="sr-only"
            onChange={(event) => void handleFiles(event.target.files)}
            aria-label="Attach files"
          />
          <Button
            variant="ghost"
            size="icon"
            onClick={() => fileRef.current?.click()}
            aria-label="Attach files"
            title="Attach files"
            disabled={uploading || sending}
          >
            {uploading ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Paperclip className="size-[18px]" aria-hidden />}
          </Button>
          <Button
            variant={recording ? 'danger' : 'ghost'}
            size="icon"
            onClick={toggleRecording}
            aria-label={recording ? 'Stop recording' : 'Dictate a message'}
            title={voice?.whisper_installed ? 'Dictate a message' : 'Voice input needs faster-whisper'}
            className={cn(recording && 'animate-pulse')}
            disabled={transcribing || sending}
          >
            {transcribing ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Mic className="size-[18px]" aria-hidden />}
          </Button>
          {sending && onStop ? (
            <Button variant="danger" size="icon" onClick={onStop} aria-label="Stop generating" title="Stop">
              <Square className="size-3.5 fill-current" aria-hidden />
            </Button>
          ) : (
            <Button
              variant="primary"
              size="icon"
              onClick={() => void handleSubmit()}
              disabled={!value.trim() || sending || submitting || uploading || recording}
              aria-label="Send message"
              title="Send (Enter)"
            >
              {submitting ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Send className="size-[17px]" aria-hidden />}
            </Button>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 px-2 pb-0.5 pt-1.5">
        {recording && (
          <Badge tone="danger" dot>
            Recording {recordingSeconds}s
          </Badge>
        )}
        {modelHint && (
          <>
            {provider?.configured ? (
              <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-ink-faint">
                <Cpu className="size-3 text-accent" aria-hidden />
                {provider.provider} · {provider.model}
              </span>
            ) : (
              <button
                type="button"
                onClick={() => navigate('/settings?section=models')}
                className="ring-focus inline-flex items-center gap-1 rounded-lg text-[11px] font-bold text-warning hover:underline"
              >
                <Cpu className="size-3" aria-hidden />
                No AI model configured — set one up
              </button>
            )}
            <span className="ml-auto hidden text-[10px] font-semibold text-ink-faint sm:inline">
              Enter to send · Shift + Enter for a new line
            </span>
          </>
        )}
      </div>
    </div>
  )
}
