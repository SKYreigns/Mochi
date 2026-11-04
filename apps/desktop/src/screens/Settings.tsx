import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  AlertTriangle,
  Brain,
  Check,
  Cpu,
  Database,
  Download,
  ExternalLink,
  Eye,
  Gauge,
  Globe,
  HardDrive,
  KeyRound,
  Lock,
  Mic,
  MonitorCog,
  Moon,
  Palette,
  Play,
  Plus,
  Power,
  RefreshCw,
  Save,
  Shield,
  ShieldCheck,
  Sparkles,
  Square,
  Sun,
  Trash2,
  Volume2,
  Wifi,
  Wind,
  X,
} from 'lucide-react'
import { API_BASE, WS_URL, api, errorMessage } from '@/lib/api'
import type {
  ModelInfo,
  PermissionMode,
  SettingsSnapshot,
  SystemMetrics,
  SystemProcess,
  ToolInfo,
  VoiceStatus,
} from '@/lib/types'
import { firstIssue, modelSettingsSchema, type ModelSettingsValues } from '@/lib/schemas'
import { useAppStore } from '@/stores/app'
import { toast } from '@/stores/toast'
import { PageHeader } from '@/components/AppShell'
import { Mascot } from '@/components/Mascot'
import {
  Badge,
  Button,
  Card,
  Field,
  Input,
  Progress,
  SectionHeading,
  Select,
  Skeleton,
  Switch,
  Tabs,
} from '@/components/ui'
import { cn, formatBytes, percent } from '@/lib/utils'

type Section =
  | 'appearance'
  | 'models'
  | 'hardware'
  | 'permissions'
  | 'voice'
  | 'browser'
  | 'general'

const SECTIONS: Array<{ value: Section; label: string }> = [
  { value: 'appearance', label: 'Appearance' },
  { value: 'models', label: 'AI Models' },
  { value: 'hardware', label: 'Hardware' },
  { value: 'permissions', label: 'Permissions' },
  { value: 'voice', label: 'Voice' },
  { value: 'browser', label: 'Browser' },
  { value: 'general', label: 'General' },
]

const PROVIDERS = [
  { value: 'ollama', label: 'Ollama (local)', needsKey: false, hint: 'Runs models on this PC. No cloud calls.' },
  { value: 'openai', label: 'OpenAI', needsKey: true, hint: 'Cloud model using an OpenAI API key.' },
  { value: 'openai_compatible', label: 'OpenAI-compatible', needsKey: true, hint: 'Any server that speaks the OpenAI API.' },
  { value: 'openrouter', label: 'OpenRouter', needsKey: true, hint: 'Many models through one gateway.' },
  { value: 'groq', label: 'Groq', needsKey: true, hint: 'Fast hosted inference.' },
  { value: 'lmstudio', label: 'LM Studio (local)', needsKey: false, hint: 'A local OpenAI-compatible server.' },
  { value: 'mock', label: 'Mock (testing)', needsKey: false, hint: 'Deterministic replies for debugging only.' },
] as const

const PERMISSION_OPTIONS: Array<{
  value: PermissionMode
  label: string
  description: string
  icon: typeof Eye
}> = [
  {
    value: 'observe',
    label: 'Observe',
    description: 'Mochi can look and read, but never change anything on this computer.',
    icon: Eye,
  },
  {
    value: 'assisted',
    label: 'Assisted',
    description: 'Mochi asks before consequential actions. Recommended for everyday use.',
    icon: Shield,
  },
  {
    value: 'autonomous',
    label: 'Autonomous',
    description: 'Mochi can act without asking, but destructive tools still require approval.',
    icon: Sparkles,
  },
]

const ACCENTS = [
  { value: 'lilac', label: 'Lilac', color: '#9b7fd9' },
  { value: 'seafoam', label: 'Seafoam', color: '#5faeae' },
  { value: 'blush', label: 'Blush', color: '#d97e9f' },
  { value: 'honey', label: 'Honey', color: '#c99a4d' },
] as const

function mergeSnapshot(base: SettingsSnapshot | null, next: Partial<SettingsSnapshot>): SettingsSnapshot | null {
  if (!base) return (next as SettingsSnapshot) ?? null
  return {
    ...base,
    ...next,
    llm: next.llm ? { ...base.llm, ...next.llm } : base.llm,
    safety: next.safety ? { ...base.safety, ...next.safety } : base.safety,
    voice: next.voice ? { ...base.voice, ...next.voice } : base.voice,
    browser: next.browser ? { ...base.browser, ...next.browser } : base.browser,
  }
}

export function SettingsScreen() {
  const [searchParams, setSearchParams] = useSearchParams()
  const requested = searchParams.get('section') as Section | null
  const section: Section = SECTIONS.some((item) => item.value === requested)
    ? (requested as Section)
    : 'appearance'
  const setSection = (value: Section) => {
    const next = new URLSearchParams(searchParams)
    next.set('section', value)
    setSearchParams(next, { replace: true })
  }

  const storeSettings = useAppStore((state) => state.settings)
  const health = useAppStore((state) => state.health)
  const backendStatus = useAppStore((state) => state.backendStatus)
  const refreshSettings = useAppStore((state) => state.refreshSettings)
  const refreshHealth = useAppStore((state) => state.refreshHealth)
  const stopEverything = useAppStore((state) => state.stopEverything)
  const resumeEverything = useAppStore((state) => state.resumeEverything)
  const stoppedLoading = useAppStore((state) => state.stoppedLoading)

  const [snapshot, setSnapshot] = useState<SettingsSnapshot | null>(storeSettings)
  const [loading, setLoading] = useState(true)
  const [savingKey, setSavingKey] = useState<string | null>(null)

  const loadSnapshot = useCallback(async () => {
    setLoading(true)
    try {
      const response = await api.settings()
      setSnapshot(response)
    } catch (err) {
      toast.error('Could not read settings', errorMessage(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadSnapshot()
  }, [loadSnapshot])

  useEffect(() => {
    if (storeSettings && !snapshot) setSnapshot(storeSettings)
  }, [storeSettings, snapshot])

  const save = useCallback(
    async (updates: Record<string, unknown>, message: string) => {
      try {
        const response = await api.updateSettings(updates)
        setSnapshot((current) => mergeSnapshot(current, response.settings))
        toast.success(message)
        void refreshSettings()
        void refreshHealth()
        return true
      } catch (err) {
        toast.error('Could not save that setting', errorMessage(err))
        return false
      }
    },
    [refreshHealth, refreshSettings],
  )

  const voice: VoiceStatus | null = health?.services.voice ?? null
  const providerStatus = health?.services.provider ?? snapshot?.provider_status ?? null
  const emergency = health?.services.emergency_stopped ?? snapshot?.emergency_stopped ?? false

  return (
    <div>
      <PageHeader
        eyebrow="Settings"
        title="Make Mochi feel like yours"
        description="Appearance is stored on this device. Everything else is saved by the local backend — no fake controls."
        icon={<MonitorCog className="size-5" aria-hidden />}
        actions={
          <Badge tone={backendStatus === 'online' ? 'success' : backendStatus === 'connecting' ? 'warning' : 'danger'} dot>
            {backendStatus === 'online' ? 'Backend connected' : backendStatus === 'connecting' ? 'Waking up' : 'Backend offline'}
          </Badge>
        }
      />

      <Tabs
        items={SECTIONS}
        value={section}
        onChange={setSection}
        ariaLabel="Settings sections"
        className="mb-5"
      />

      {loading && !snapshot ? (
        <div className="space-y-3">
          <Skeleton className="h-40" />
          <Skeleton className="h-40" />
        </div>
      ) : section === 'appearance' ? (
        <AppearancePanel />
      ) : section === 'models' ? (
        <ModelsPanel
          snapshot={snapshot}
          providerStatus={providerStatus}
          savingKey={savingKey}
          setSavingKey={setSavingKey}
          save={save}
          onReload={() => void loadSnapshot()}
        />
      ) : section === 'hardware' ? (
        <HardwarePanel />
      ) : section === 'permissions' ? (
        <PermissionsPanel snapshot={snapshot} save={save} />
      ) : section === 'voice' ? (
        <VoicePanel snapshot={snapshot} voice={voice} save={save} />
      ) : section === 'browser' ? (
        <BrowserPanel snapshot={snapshot} save={save} />
      ) : (
        <GeneralPanel
          snapshot={snapshot}
          emergency={emergency}
          stoppedLoading={stoppedLoading}
          onStop={() => void stopEverything()}
          onResume={() => void resumeEverything()}
        />
      )}
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Appearance                                                                 */
/* -------------------------------------------------------------------------- */

function AppearancePanel() {
  const theme = useAppStore((state) => state.theme)
  const accent = useAppStore((state) => state.accent)
  const motionPreference = useAppStore((state) => state.motion)
  const sidebarCollapsed = useAppStore((state) => state.sidebarCollapsed)
  const setTheme = useAppStore((state) => state.setTheme)
  const setAccent = useAppStore((state) => state.setAccent)
  const setMotion = useAppStore((state) => state.setMotion)
  const toggleSidebar = useAppStore((state) => state.toggleSidebar)

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
      <div className="space-y-4">
        <Card className="p-5">
          <SectionHeading
            title="Theme"
            description="Mochi is designed to feel soft in daylight and cozy at night."
            action={<Palette className="size-4 text-accent" aria-hidden />}
          />
          <div className="mt-3 grid gap-2 sm:grid-cols-3">
            {[
              { value: 'light', label: 'Daylight', icon: Sun },
              { value: 'dark', label: 'Moonlight', icon: Moon },
              { value: 'system', label: 'Follow system', icon: MonitorCog },
            ].map((option) => {
              const Icon = option.icon
              const selected = theme === option.value
              return (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setTheme(option.value as 'light' | 'dark' | 'system')}
                  aria-pressed={selected}
                  className={cn(
                    'ring-focus flex items-center gap-2 rounded-2xl border px-3 py-2.5 text-left transition-colors',
                    selected
                      ? 'border-accent/45 bg-accent-soft/60 text-accent-strong'
                      : 'border-line bg-surface-muted/30 text-ink-soft hover:border-accent/25',
                  )}
                >
                  <Icon className="size-4 shrink-0" aria-hidden />
                  <span className="text-xs font-extrabold">{option.label}</span>
                  {selected && <Check className="ml-auto size-3.5" aria-hidden />}
                </button>
              )
            })}
          </div>
        </Card>

        <Card className="p-5">
          <SectionHeading
            title="Accent"
            description="The little glow behind buttons, badges and highlights."
          />
          <div className="mt-3 grid gap-2 sm:grid-cols-4">
            {ACCENTS.map((option) => {
              const selected = accent === option.value
              return (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setAccent(option.value)}
                  aria-pressed={selected}
                  className={cn(
                    'ring-focus flex items-center gap-2 rounded-2xl border px-3 py-2.5 transition-colors',
                    selected ? 'border-accent/50 bg-surface-muted' : 'border-line hover:border-accent/30',
                  )}
                >
                  <span
                    className="size-5 rounded-full border border-white/60 shadow-sm"
                    style={{ backgroundColor: option.color }}
                    aria-hidden
                  />
                  <span className="text-xs font-extrabold text-ink">{option.label}</span>
                  {selected && <Check className="ml-auto size-3.5 text-accent" aria-hidden />}
                </button>
              )
            })}
          </div>
        </Card>

        <Card className="p-5">
          <SectionHeading
            title="Motion"
            description="Animations are gentle by design. You can reduce or turn them off."
            action={<Wind className="size-4 text-accent" aria-hidden />}
          />
          <div className="mt-3 grid gap-2 sm:grid-cols-3">
            {[
              { value: 'full', label: 'Gentle motion', hint: 'Floating and fades' },
              { value: 'reduced', label: 'Reduced', hint: 'Almost still' },
              { value: 'system', label: 'Follow system', hint: 'Respects your OS setting' },
            ].map((option) => {
              const selected = motionPreference === option.value
              return (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setMotion(option.value as 'full' | 'reduced' | 'system')}
                  aria-pressed={selected}
                  className={cn(
                    'ring-focus rounded-2xl border p-3 text-left transition-colors',
                    selected ? 'border-accent/45 bg-accent-soft/60' : 'border-line hover:border-accent/25',
                  )}
                >
                  <span className="block text-xs font-extrabold text-ink">{option.label}</span>
                  <span className="mt-0.5 block text-[10px] text-ink-faint">{option.hint}</span>
                </button>
              )
            })}
          </div>
        </Card>

        <Card className="p-5">
          <Switch
            checked={sidebarCollapsed}
            onChange={() => toggleSidebar()}
            label="Collapse the sidebar"
            description="Keep more room for conversations on smaller screens."
          />
        </Card>
      </div>

      <Card className="flex flex-col items-center justify-center gap-3 p-6 text-center">
        <Mascot state="success" size={168} title="Mochi preview" />
        <div>
          <p className="text-lg font-extrabold text-ink">Looking cozy</p>
          <p className="mt-1 max-w-xs text-xs leading-relaxed text-ink-faint">
            Appearance preferences apply instantly and are remembered on this device only.
          </p>
        </div>
        <div className="flex flex-wrap justify-center gap-1.5">
          <Badge tone="accent">Dreamy pastel</Badge>
          <Badge tone="info">Soft glass</Badge>
          <Badge tone="success">Reduced-motion aware</Badge>
        </div>
      </Card>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Models                                                                     */
/* -------------------------------------------------------------------------- */

function ModelsPanel({
  snapshot,
  providerStatus,
  savingKey,
  setSavingKey,
  save,
  onReload,
}: {
  snapshot: SettingsSnapshot | null
  providerStatus: { provider: string; model: string; base_url: string; configured: boolean; has_api_key: boolean } | null
  savingKey: string | null
  setSavingKey: (value: string | null) => void
  save: (updates: Record<string, unknown>, message: string) => Promise<boolean>
  onReload: () => void
}) {
  const [provider, setProvider] = useState(snapshot?.llm.provider ?? 'ollama')
  const [model, setModel] = useState(snapshot?.llm.model ?? '')
  const [baseUrl, setBaseUrl] = useState(snapshot?.llm.base_url ?? '')
  const [temperature, setTemperature] = useState(String(snapshot?.llm.temperature ?? 0.2))
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [models, setModels] = useState<ModelInfo[]>([])
  const [modelsError, setModelsError] = useState<string | null>(null)
  const [listing, setListing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [apiKey, setApiKey] = useState('')

  useEffect(() => {
    if (!snapshot) return
    setProvider(snapshot.llm.provider)
    setModel(snapshot.llm.model)
    setBaseUrl(snapshot.llm.base_url ?? '')
    setTemperature(String(snapshot.llm.temperature ?? 0.2))
  }, [snapshot])

  const meta = PROVIDERS.find((item) => item.value === provider) ?? PROVIDERS[0]
  const configured = providerStatus?.configured ?? false
  const hasKey = providerStatus?.has_api_key ?? Boolean(snapshot?.llm.has_api_key)

  const listModels = async () => {
    setListing(true)
    setModelsError(null)
    try {
      const response = await api.models({
        provider,
        base_url: baseUrl.trim() || undefined,
      })
      setModels(response.models)
      if (response.models.length === 0) {
        setModelsError('The provider returned no models. Download one, or type a model name manually.')
      }
    } catch (err) {
      setModels([])
      setModelsError(errorMessage(err))
    } finally {
      setListing(false)
    }
  }

  const submit = async () => {
    const parsed = modelSettingsSchema.safeParse({
      provider,
      model: model.trim(),
      base_url: baseUrl.trim(),
      temperature: Number(temperature),
    })
    if (!parsed.success) {
      setErrors(firstIssue(parsed.error))
      return
    }
    setErrors({})
    setSaving(true)
    const values: ModelSettingsValues = parsed.data
    const ok = await save(
      {
        llm: {
          provider: values.provider,
          model: values.model,
          base_url: values.base_url,
          temperature: values.temperature,
        },
      },
      'AI model settings saved',
    )
    setSaving(false)
    if (ok) onReload()
  }

  const storeKey = async () => {
    const value = apiKey.trim()
    if (!value) return
    setSavingKey(provider)
    try {
      await api.saveApiKey(provider, value)
      setApiKey('')
      toast.success('API key saved', 'It is stored in Windows Credential Manager.')
      onReload()
    } catch (err) {
      toast.error('Could not save the API key', errorMessage(err))
    } finally {
      setSavingKey(null)
    }
  }

  const removeKey = async () => {
    setSavingKey(`${provider}-delete`)
    try {
      await api.deleteApiKey(provider)
      toast.success('API key removed')
      onReload()
    } catch (err) {
      toast.error('Could not remove the API key', errorMessage(err))
    } finally {
      setSavingKey(null)
    }
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
      <div className="space-y-4">
        <Card className="p-5">
          <SectionHeading
            title="Language model"
            description="Mochi needs a model to reason about your requests. Tools work without one."
            action={<Brain className="size-4 text-accent" aria-hidden />}
          />

          <div className={cn(
            'mt-3 flex flex-wrap items-center gap-2 rounded-2xl border px-3.5 py-2.5',
            configured ? 'border-success/25 bg-success-soft/50 text-success' : 'border-warning/25 bg-warning-soft/60 text-warning',
          )}>
            <span className={cn('size-2 rounded-full', configured ? 'bg-success' : 'bg-warning')} aria-hidden />
            <p className="text-xs font-bold">
              {configured
                ? `Ready · ${providerStatus?.provider} · ${providerStatus?.model}`
                : 'No model is configured yet. Tasks that need reasoning will fail.'}
            </p>
          </div>

          <div className="mt-4 space-y-3">
            <Field label="Provider" htmlFor="provider" error={errors.provider} hint={meta.hint}>
              <Select
                id="provider"
                value={provider}
                onChange={(event) => {
                  setProvider(event.target.value)
                  setModels([])
                  setModelsError(null)
                }}
              >
                {PROVIDERS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
            </Field>

            <Field
              label="Model name"
              htmlFor="model"
              error={errors.model}
              hint={
                provider === 'ollama'
                  ? 'For example the exact tag shown after `ollama list`.'
                  : 'Use the model identifier required by this provider.'
              }
            >
              <Input
                id="model"
                value={model}
                onChange={(event) => setModel(event.target.value)}
                placeholder={provider === 'ollama' ? 'llama3.2:3b' : 'model-id'}
                autoComplete="off"
              />
            </Field>

            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => void listModels()}
                loading={listing}
                icon={<Download className="size-3.5" aria-hidden />}
              >
                Load available models
              </Button>
              {models.length > 0 && (
                <span className="text-[11px] font-semibold text-ink-faint">
                  {models.length} found — pick one below
                </span>
              )}
            </div>

            {modelsError && (
              <p className="rounded-2xl border border-warning/25 bg-warning-soft/60 px-3 py-2 text-xs leading-relaxed text-warning">
                {modelsError}
              </p>
            )}

            {models.length > 0 && (
              <div className="mochi-scroll max-h-44 space-y-1 overflow-y-auto rounded-2xl border border-line bg-surface-muted/30 p-2">
                {models.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => setModel(item.id)}
                    className={cn(
                      'ring-focus flex w-full items-center gap-2 rounded-xl px-2.5 py-1.5 text-left transition-colors',
                      model === item.id ? 'bg-accent-soft text-accent-strong' : 'hover:bg-surface-strong/70',
                    )}
                  >
                    <span className="min-w-0 flex-1 truncate font-mono text-[11px] font-semibold">{item.id}</span>
                    {typeof item.size === 'number' && item.size > 0 && (
                      <span className="shrink-0 text-[10px] text-ink-faint">{formatBytes(item.size)}</span>
                    )}
                    {model === item.id && <Check className="size-3.5 shrink-0" aria-hidden />}
                  </button>
                ))}
              </div>
            )}

            <Field
              label="Base URL (optional)"
              htmlFor="base-url"
              error={errors.base_url}
              hint="Leave empty to use the provider default. Required for some local servers."
            >
              <Input
                id="base-url"
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
                placeholder={provider === 'ollama' ? 'http://127.0.0.1:11434' : 'https://…'}
                autoComplete="off"
                spellCheck={false}
              />
            </Field>

            <Field label={`Temperature · ${temperature}`} htmlFor="temperature" error={errors.temperature} hint="Lower is steadier; higher is more playful.">
              <input
                id="temperature"
                type="range"
                min={0}
                max={2}
                step={0.1}
                value={temperature}
                onChange={(event) => setTemperature(event.target.value)}
                className="w-full accent-[var(--accent)]"
              />
            </Field>

            <div className="flex justify-end gap-2 pt-1">
              <Button variant="ghost" onClick={onReload}>
                Reset
              </Button>
              <Button onClick={() => void submit()} loading={saving} icon={<Save className="size-4" aria-hidden />}>
                Save model settings
              </Button>
            </div>
          </div>
        </Card>

        {meta.needsKey && (
          <Card className="p-5">
            <SectionHeading
              title="API key"
              description="Stored in Windows Credential Manager. It is never sent back to the interface."
              action={<KeyRound className="size-4 text-accent" aria-hidden />}
            />
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Badge tone={hasKey ? 'success' : 'warning'} dot>
                {hasKey ? 'A key is saved' : 'No key saved'}
              </Badge>
              {hasKey && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="text-danger hover:bg-danger-soft"
                  onClick={() => void removeKey()}
                  loading={savingKey === `${provider}-delete`}
                  icon={<Trash2 className="size-3.5" aria-hidden />}
                >
                  Remove key
                </Button>
              )}
            </div>
            <div className="mt-3 flex gap-2">
              <Input
                type="password"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder="Paste the API key"
                aria-label="API key"
                autoComplete="off"
              />
              <Button
                variant="soft"
                onClick={() => void storeKey()}
                loading={savingKey === provider}
                disabled={!apiKey.trim()}
              >
                Save key
              </Button>
            </div>
            <p className="mt-2 text-[11px] leading-relaxed text-ink-faint">
              You can also provide the key through an environment variable supported by the backend.
            </p>
          </Card>
        )}
      </div>

      <div className="space-y-4">
        <Card className="p-5">
          <SectionHeading title="What this enables" />
          <ul className="mt-3 space-y-2 text-xs leading-relaxed text-ink-soft">
            <li className="flex gap-2">
              <Sparkles className="mt-0.5 size-3.5 shrink-0 text-accent" aria-hidden />
              Chat replies, task planning and multi-step reasoning.
            </li>
            <li className="flex gap-2">
              <ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-success" aria-hidden />
              Tool use still follows the permission mode and approval rules.
            </li>
            <li className="flex gap-2">
              <Lock className="mt-0.5 size-3.5 shrink-0 text-info" aria-hidden />
              Credentials stay in the operating system's credential store.
            </li>
          </ul>
        </Card>

        <Card className="border-info/25 bg-info-soft/30 p-5">
          <p className="flex items-center gap-1.5 text-xs font-extrabold uppercase tracking-[0.08em] text-info">
            <AlertTriangle className="size-3.5" aria-hidden /> Honest limitation
          </p>
          <p className="mt-1.5 text-xs leading-relaxed text-ink-soft">
            Mochi ships with no model bundled. If Ollama is not installed and no provider key is configured,
            starting a task will return a real configuration error instead of a pretend answer.
          </p>
        </Card>

        {snapshot?.app && (
          <Card className="p-4">
            <p className="text-[10px] font-extrabold uppercase tracking-[0.1em] text-ink-faint">Backend</p>
            <p className="mt-1 truncate font-mono text-[11px] text-ink-soft">{API_BASE}</p>
            <p className="mt-0.5 text-[11px] text-ink-faint">
              {snapshot.tools_count ?? 0} tools · environment {snapshot.app.environment ?? 'unknown'}
            </p>
          </Card>
        )}
      </div>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Hardware                                                                   */
/* -------------------------------------------------------------------------- */

function HardwarePanel() {
  const [metrics, setMetrics] = useState<SystemMetrics | null>(null)
  const [processes, setProcesses] = useState<SystemProcess[]>([])
  const [osInfo, setOsInfo] = useState<Record<string, unknown> | null>(null)
  const [osError, setOsError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    const [metricsResult, processResult, osResult] = await Promise.allSettled([
      api.metrics(),
      api.processes(10),
      api.osInfo(),
    ])
    if (metricsResult.status === 'fulfilled') setMetrics(metricsResult.value)
    if (processResult.status === 'fulfilled') setProcesses(processResult.value.processes)
    if (osResult.status === 'fulfilled') {
      setOsInfo(osResult.value.data)
      setOsError(null)
    } else {
      setOsError(errorMessage(osResult.reason))
    }
    setLoading(false)
  }, [])

  useEffect(() => {
    void load()
    const interval = window.setInterval(() => void load(), 8000)
    return () => window.clearInterval(interval)
  }, [load])

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-semibold text-ink-faint">
          Read live from this Windows machine by the backend.
        </p>
        <Button size="sm" variant="outline" onClick={() => void load()} loading={loading} icon={<RefreshCw className="size-4" aria-hidden />}>
          Refresh
        </Button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Card className="p-4">
          <p className="flex items-center justify-between text-[10px] font-extrabold uppercase tracking-[0.1em] text-ink-faint">
            CPU <Cpu className="size-3.5 text-accent" aria-hidden />
          </p>
          <p className="mt-1 text-2xl font-extrabold text-ink">{metrics ? percent(metrics.cpu_percent) : '—'}</p>
          <Progress value={metrics?.cpu_percent ?? 0} className="mt-2" label="CPU usage" />
          <p className="mt-1 text-[10px] text-ink-faint">{metrics ? `${metrics.cpu_cores} logical cores` : 'Reading…'}</p>
        </Card>
        <Card className="p-4">
          <p className="flex items-center justify-between text-[10px] font-extrabold uppercase tracking-[0.1em] text-ink-faint">
            Memory <Database className="size-3.5 text-info" aria-hidden />
          </p>
          <p className="mt-1 text-2xl font-extrabold text-ink">{metrics ? percent(metrics.ram_percent) : '—'}</p>
          <Progress value={metrics?.ram_percent ?? 0} tone="info" className="mt-2" label="Memory usage" />
          <p className="mt-1 text-[10px] text-ink-faint">
            {metrics ? `${metrics.ram_used_gb.toFixed(1)} / ${metrics.ram_total_gb.toFixed(1)} GB` : 'Reading…'}
          </p>
        </Card>
        <Card className="p-4">
          <p className="flex items-center justify-between text-[10px] font-extrabold uppercase tracking-[0.1em] text-ink-faint">
            Battery <Gauge className="size-3.5 text-success" aria-hidden />
          </p>
          <p className="mt-1 text-2xl font-extrabold text-ink">
            {metrics?.battery ? percent(metrics.battery.percent) : 'No battery'}
          </p>
          <Progress
            value={metrics?.battery?.percent ?? 0}
            tone={metrics?.battery && metrics.battery.percent < 20 ? 'danger' : 'success'}
            className="mt-2"
            label="Battery level"
          />
          <p className="mt-1 text-[10px] text-ink-faint">
            {metrics?.battery ? (metrics.battery.plugged_in ? 'Plugged in' : 'Running on battery') : 'Desktop power'}
          </p>
        </Card>
        <Card className="p-4">
          <p className="flex items-center justify-between text-[10px] font-extrabold uppercase tracking-[0.1em] text-ink-faint">
            Uptime <Power className="size-3.5 text-warning" aria-hidden />
          </p>
          <p className="mt-1 text-2xl font-extrabold text-ink">
            {metrics ? formatUptime(metrics.uptime_seconds) : '—'}
          </p>
          <p className="mt-2 text-[10px] text-ink-faint">Since the last Windows boot.</p>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card className="p-5">
          <SectionHeading title="Storage" description="Free space reported by Windows." action={<HardDrive className="size-4 text-accent" aria-hidden />} />
          <div className="mt-3 space-y-3">
            {metrics && metrics.volumes.length > 0 ? (
              metrics.volumes.map((volume) => (
                <div key={volume.mountpoint}>
                  <div className="flex items-baseline justify-between gap-2 text-xs">
                    <p className="font-bold text-ink">
                      {volume.mountpoint} <span className="font-mono text-[10px] text-ink-faint">{volume.device}</span>
                    </p>
                    <p className="text-[11px] text-ink-faint">
                      {volume.free_gb.toFixed(1)} GB free of {volume.total_gb.toFixed(1)} GB
                    </p>
                  </div>
                  <Progress
                    value={volume.percent}
                    tone={volume.percent > 90 ? 'danger' : volume.percent > 75 ? 'warning' : 'accent'}
                    className="mt-1.5"
                    label={`Disk ${volume.mountpoint}`}
                  />
                </div>
              ))
            ) : (
              <p className="text-xs text-ink-faint">No disk information returned.</p>
            )}
          </div>
        </Card>

        <Card className="p-5">
          <SectionHeading title="Operating system" description="Reported by the real system tool." action={<MonitorCog className="size-4 text-ink-faint" aria-hidden />} />
          {osError ? (
            <p className="mt-3 rounded-2xl border border-warning/25 bg-warning-soft/60 px-3 py-2 text-xs text-warning">
              {osError}
            </p>
          ) : osInfo ? (
            <div className="mt-3 grid gap-x-4 gap-y-1 rounded-2xl border border-line bg-surface-muted/30 p-3 text-[11px] sm:grid-cols-2">
              {Object.entries(osInfo).slice(0, 12).map(([key, value]) => (
                <div key={key} className="flex min-w-0 gap-2">
                  <span className="w-24 shrink-0 font-bold text-ink-faint">{key.replace(/_/g, ' ')}</span>
                  <span className="min-w-0 flex-1 break-all font-mono text-ink-soft">
                    {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <Skeleton className="mt-3 h-24" />
          )}
        </Card>

        <Card className="p-5">
          <SectionHeading title="Top processes" description="Ordered by memory use." action={<Cpu className="size-4 text-ink-faint" aria-hidden />} />
          <div className="mt-3 space-y-1">
            {processes.length === 0 ? (
              <Skeleton className="h-24" />
            ) : (
              processes.map((process, index) => (
                <div key={`${process.pid ?? index}-${process.name ?? ''}`} className="flex items-center gap-2 rounded-xl px-2 py-1.5 hover:bg-surface-muted/50">
                  <span className="w-6 text-right font-mono text-[10px] text-ink-faint">{process.pid}</span>
                  <p className="min-w-0 flex-1 truncate text-[11px] font-semibold text-ink-soft">{process.name || 'Unknown'}</p>
                  <span className="shrink-0 text-[10px] text-ink-faint">{formatBytes((process.memory_mb ?? 0) * 1024 * 1024)}</span>
                </div>
              ))
            )}
          </div>
        </Card>

        <Card className="border-warning/25 bg-warning-soft/25 p-5">
          <SectionHeading title="GPU acceleration" action={<Gauge className="size-4 text-warning" aria-hidden />} />
          <div className="mt-3 flex items-start gap-2 rounded-2xl border border-warning/25 bg-surface/60 p-3">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
            <p className="text-xs leading-relaxed text-ink-soft">
              <span className="font-extrabold text-ink">Unavailable.</span> The backend does not expose a GPU or VRAM
              telemetry endpoint, so Mochi cannot show real graphics-card usage. No numbers are invented here.
            </p>
          </div>
          <p className="mt-2 text-[11px] leading-relaxed text-ink-faint">
            Local model speed depends on Ollama and your hardware. Check Ollama's own logs for acceleration details.
          </p>
        </Card>
      </div>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Permissions                                                                */
/* -------------------------------------------------------------------------- */

function PermissionsPanel({
  snapshot,
  save,
}: {
  snapshot: SettingsSnapshot | null
  save: (updates: Record<string, unknown>, message: string) => Promise<boolean>
}) {
  const [mode, setMode] = useState<PermissionMode>(snapshot?.permission_mode ?? 'assisted')
  const [directories, setDirectories] = useState<string[]>(snapshot?.allowed_directories ?? [])
  const [directoryDraft, setDirectoryDraft] = useState('')
  const [autoTools, setAutoTools] = useState<string[]>(snapshot?.safety.auto_approve_tools ?? [])
  const [confirmDestructive, setConfirmDestructive] = useState(snapshot?.safety.confirm_destructive ?? true)
  const [tools, setTools] = useState<ToolInfo[]>([])
  const [toolsError, setToolsError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!snapshot) return
    setMode(snapshot.permission_mode)
    setDirectories(snapshot.allowed_directories)
    setAutoTools(snapshot.safety.auto_approve_tools)
    setConfirmDestructive(snapshot.safety.confirm_destructive)
  }, [snapshot])

  useEffect(() => {
    let cancelled = false
    api
      .tools()
      .then((response) => {
        if (!cancelled) setTools(response.tools)
      })
      .catch((err: unknown) => {
        if (!cancelled) setToolsError(errorMessage(err))
      })
    return () => {
      cancelled = true
    }
  }, [])

  const changeMode = async (next: PermissionMode) => {
    setMode(next)
    const ok = await save({ permission_mode: next }, `Permission mode is now ${next}`)
    if (!ok) setMode(snapshot?.permission_mode ?? 'assisted')
  }

  const addDirectory = async () => {
    const value = directoryDraft.trim()
    if (!value) return
    const next = Array.from(new Set([...directories, value]))
    setBusy(true)
    const ok = await save({ allowed_directories: next }, 'Folder allowed')
    if (ok) {
      setDirectories(next)
      setDirectoryDraft('')
    }
    setBusy(false)
  }

  const removeDirectory = async (path: string) => {
    const next = directories.filter((item) => item !== path)
    setBusy(true)
    const ok = await save({ allowed_directories: next }, 'Folder removed')
    if (ok) setDirectories(next)
    setBusy(false)
  }

  const toggleTool = async (tool: ToolInfo) => {
    const next = autoTools.includes(tool.name)
      ? autoTools.filter((name) => name !== tool.name)
      : [...autoTools, tool.name]
    setAutoTools(next)
    const ok = await save(
      { safety: { auto_approve_tools: next } },
      autoTools.includes(tool.name)
        ? `${tool.name} will ask again`
        : `${tool.name} is auto-approved`,
    )
    if (!ok) setAutoTools(autoTools)
  }

  return (
    <div className="space-y-4">
      <Card className="p-5">
        <SectionHeading
          title="Permission mode"
          description="This decides when Mochi pauses for you. It applies to chat, workflows and schedules."
          action={<Shield className="size-4 text-accent" aria-hidden />}
        />
        <div className="mt-3 grid gap-2 lg:grid-cols-3">
          {PERMISSION_OPTIONS.map((option) => {
            const Icon = option.icon
            const selected = mode === option.value
            return (
              <button
                key={option.value}
                type="button"
                onClick={() => void changeMode(option.value)}
                aria-pressed={selected}
                className={cn(
                  'ring-focus flex flex-col rounded-2xl border p-3.5 text-left transition-colors',
                  selected ? 'border-accent/50 bg-accent-soft/50' : 'border-line hover:border-accent/25',
                )}
              >
                <span className="flex items-center gap-2">
                  <Icon className={cn('size-4', selected ? 'text-accent-strong' : 'text-ink-faint')} aria-hidden />
                  <span className="text-sm font-extrabold text-ink">{option.label}</span>
                  {selected && <Check className="ml-auto size-3.5 text-accent" aria-hidden />}
                </span>
                <span className="mt-1 text-[11px] leading-relaxed text-ink-faint">{option.description}</span>
              </button>
            )
          })}
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card className="p-5">
          <SectionHeading
            title="Allowed folders"
            description="The backend's path policy refuses anything outside this list."
            action={<Lock className="size-4 text-accent" aria-hidden />}
          />
          <div className="mt-3 space-y-1.5">
            {directories.length === 0 ? (
              <p className="rounded-2xl border border-dashed border-line px-3 py-4 text-xs text-ink-faint">
                No folders are allowed beyond Mochi's own workspace.
              </p>
            ) : (
              directories.map((path) => (
                <div key={path} className="flex items-center gap-2 rounded-xl border border-line bg-surface-muted/30 px-3 py-2">
                  <Lock className="size-3.5 shrink-0 text-ink-faint" aria-hidden />
                  <p className="min-w-0 flex-1 break-all font-mono text-[11px] text-ink-soft">{path}</p>
                  <button
                    type="button"
                    onClick={() => void removeDirectory(path)}
                    disabled={busy}
                    className="ring-focus shrink-0 rounded-lg p-1 text-ink-faint hover:bg-danger-soft hover:text-danger disabled:opacity-40"
                    aria-label={`Remove ${path}`}
                  >
                    <X className="size-3.5" aria-hidden />
                  </button>
                </div>
              ))
            )}
          </div>
          <div className="mt-3 flex gap-2">
            <Input
              value={directoryDraft}
              onChange={(event) => setDirectoryDraft(event.target.value)}
              placeholder="C:\Users\you\Documents"
              aria-label="Folder path to allow"
              onKeyDown={(event) => event.key === 'Enter' && void addDirectory()}
              spellCheck={false}
            />
            <Button variant="soft" onClick={() => void addDirectory()} loading={busy} disabled={!directoryDraft.trim()} icon={<Plus className="size-4" aria-hidden />}>
              Allow
            </Button>
          </div>
          <p className="mt-2 text-[11px] text-ink-faint">
            The folder must already exist. The backend rejects relative or missing paths.
          </p>
        </Card>

        <Card className="p-5">
          <SectionHeading
            title="Safety"
            description="Extra guards around consequential work."
            action={<ShieldCheck className="size-4 text-success" aria-hidden />}
          />
          <div className="mt-3 space-y-4">
            <Switch
              checked={confirmDestructive}
              onChange={(next) => {
                setConfirmDestructive(next)
                void save(
                  { safety: { confirm_destructive: next } },
                  next ? 'Destructive actions will ask first' : 'Destructive actions can run automatically',
                ).then((ok) => {
                  if (!ok) setConfirmDestructive(!next)
                })
              }}
              label="Confirm destructive tools"
              description="Deleting, overwriting or moving files always asks, even in autonomous mode."
            />
            <div className="rounded-2xl border border-warning/25 bg-warning-soft/40 p-3 text-[11px] leading-relaxed text-warning">
              Auto-approving a tool removes the pause for that tool. Only do this for tools you trust completely.
            </div>
          </div>
        </Card>
      </div>

      <Card className="p-5">
        <SectionHeading
          title="Auto-approved tools"
          description="These tools will run without an approval prompt. Everything else follows the mode above."
          action={<Badge tone={autoTools.length > 0 ? 'warning' : 'success'}>{autoTools.length} auto-approved</Badge>}
        />
        {toolsError ? (
          <p className="mt-3 rounded-2xl border border-warning/25 bg-warning-soft/60 px-3 py-2 text-xs text-warning">
            {toolsError}
          </p>
        ) : tools.length === 0 ? (
          <Skeleton className="mt-3 h-24" />
        ) : (
          <div className="mochi-scroll mt-3 grid max-h-[360px] gap-1.5 overflow-y-auto pr-1 sm:grid-cols-2">
            {tools.map((tool) => {
              const approved = autoTools.includes(tool.name)
              return (
                <button
                  key={tool.name}
                  type="button"
                  onClick={() => void toggleTool(tool)}
                  aria-pressed={approved}
                  className={cn(
                    'ring-focus flex items-center gap-2 rounded-xl border px-2.5 py-2 text-left transition-colors',
                    approved
                      ? 'border-warning/35 bg-warning-soft/50'
                      : 'border-line bg-surface-muted/20 hover:border-accent/25',
                  )}
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-mono text-[11px] font-semibold text-ink">{tool.name}</span>
                    <span className="block truncate text-[10px] text-ink-faint">{tool.permission}</span>
                  </span>
                  <span
                    className={cn(
                      'grid size-5 shrink-0 place-items-center rounded-full border',
                      approved ? 'border-warning bg-warning text-white' : 'border-line-strong text-transparent',
                    )}
                    aria-hidden
                  >
                    <Check className="size-3" />
                  </span>
                </button>
              )
            })}
          </div>
        )}
      </Card>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Voice                                                                      */
/* -------------------------------------------------------------------------- */

function VoicePanel({
  snapshot,
  voice,
  save,
}: {
  snapshot: SettingsSnapshot | null
  voice: VoiceStatus | null
  save: (updates: Record<string, unknown>, message: string) => Promise<boolean>
}) {
  const current = voice ?? snapshot?.voice ?? null
  const [enabled, setEnabled] = useState(current?.enabled ?? false)
  const [pushToTalk, setPushToTalk] = useState(current?.push_to_talk ?? false)
  const [speakResponses, setSpeakResponses] = useState(current?.speak_responses ?? false)
  const [wakeWord, setWakeWord] = useState(current?.wake_word ?? '')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!current) return
    setEnabled(current.enabled)
    setPushToTalk(current.push_to_talk)
    setSpeakResponses(current.speak_responses)
    setWakeWord(current.wake_word)
  }, [current])

  const persist = async (updates: Record<string, unknown>, message: string) => {
    setSaving(true)
    await save({ voice: updates }, message)
    setSaving(false)
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
      <div className="space-y-4">
        <Card className="p-5">
          <SectionHeading
            title="Voice input"
            description="Dictation uses the backend's local speech-to-text service when it is installed."
            action={<Mic className="size-4 text-accent" aria-hidden />}
          />
          <div
            className={cn(
              'mt-3 flex flex-wrap items-center gap-2 rounded-2xl border px-3.5 py-2.5',
              current?.whisper_installed
                ? 'border-success/25 bg-success-soft/50 text-success'
                : 'border-warning/25 bg-warning-soft/60 text-warning',
            )}
          >
            <span
              className={cn('size-2 rounded-full', current?.whisper_installed ? 'bg-success' : 'bg-warning')}
              aria-hidden
            />
            <p className="text-xs font-bold">
              {current?.whisper_installed
                ? `faster-whisper is installed${current.whisper_model ? ` · ${current.whisper_model}` : ''}`
                : 'faster-whisper is not installed — dictation will report an honest error.'}
            </p>
          </div>

          <div className="mt-4 space-y-4">
            <Switch
              checked={enabled}
              onChange={(next) => {
                setEnabled(next)
                void persist({ enabled: next }, next ? 'Voice features enabled' : 'Voice features disabled')
              }}
              label="Enable voice features"
              description="Shows the microphone control and allows spoken responses."
            />
            <Switch
              checked={pushToTalk}
              onChange={(next) => {
                setPushToTalk(next)
                void persist({ push_to_talk: next }, next ? 'Push-to-talk enabled' : 'Push-to-talk disabled')
              }}
              label="Push to talk"
              description="Hold a key to record instead of clicking the microphone."
            />
            <Switch
              checked={speakResponses}
              onChange={(next) => {
                setSpeakResponses(next)
                void persist({ speak_responses: next }, next ? 'Mochi will speak replies' : 'Spoken replies disabled')
              }}
              label="Speak replies aloud"
              description={
                current?.tts_installed
                  ? 'Uses the backend text-to-speech voice.'
                  : 'Text-to-speech is not installed on the backend, so this will be unavailable until it is.'
              }
            />
          </div>
        </Card>

        <Card className="p-5">
          <SectionHeading title="Wake word" description="Reserved for hands-free listening when supported." />
          <div className="mt-3 flex gap-2">
            <Input
              value={wakeWord}
              onChange={(event) => setWakeWord(event.target.value)}
              placeholder="Hey Mochi"
              aria-label="Wake word"
              maxLength={60}
            />
            <Button
              variant="soft"
              loading={saving}
              onClick={() => void persist({ wake_word: wakeWord.trim() }, 'Wake word saved')}
              icon={<Save className="size-4" aria-hidden />}
            >
              Save
            </Button>
          </div>
          <p className="mt-2 text-[11px] leading-relaxed text-ink-faint">
            A wake word only works if the optional always-listening service is configured. Saving it here does not
            pretend that service exists.
          </p>
        </Card>
      </div>

      <div className="space-y-4">
        <Card className="p-5">
          <SectionHeading title="Capabilities" description="Reported by the live backend voice service." />
          <div className="mt-3 space-y-2">
            {[
              { label: 'Speech to text', value: current?.whisper_installed ?? false, detail: current?.whisper_model || 'faster-whisper' },
              { label: 'Whisper loaded', value: current?.whisper_loaded ?? false, detail: 'Model in memory' },
              { label: 'Text to speech', value: current?.tts_installed ?? false, detail: 'pyttsx3 / system voice' },
              { label: 'Browser speech', value: current?.browser_tts_available ?? false, detail: 'Web speech fallback' },
            ].map((row) => (
              <div key={row.label} className="flex items-center gap-2 rounded-xl border border-line bg-surface-muted/30 px-3 py-2">
                <span
                  className={cn(
                    'grid size-5 shrink-0 place-items-center rounded-full',
                    row.value ? 'bg-success-soft text-success' : 'bg-surface-muted text-ink-faint',
                  )}
                  aria-hidden
                >
                  {row.value ? <Check className="size-3" /> : <X className="size-3" />}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-[11px] font-bold text-ink">{row.label}</p>
                  <p className="truncate text-[10px] text-ink-faint">{row.detail}</p>
                </div>
                <Badge tone={row.value ? 'success' : 'neutral'}>{row.value ? 'ready' : 'missing'}</Badge>
              </div>
            ))}
          </div>
        </Card>

        {current?.note && (
          <Card className="border-info/25 bg-info-soft/30 p-5">
            <p className="flex items-center gap-1.5 text-xs font-extrabold uppercase tracking-[0.08em] text-info">
              <Volume2 className="size-3.5" aria-hidden /> Backend note
            </p>
            <p className="mt-1.5 text-xs leading-relaxed text-ink-soft">{current.note}</p>
          </Card>
        )}
      </div>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Browser                                                                    */
/* -------------------------------------------------------------------------- */

function BrowserPanel({
  snapshot,
  save,
}: {
  snapshot: SettingsSnapshot | null
  save: (updates: Record<string, unknown>, message: string) => Promise<boolean>
}) {
  const [headless, setHeadless] = useState(snapshot?.browser.headless ?? false)
  const [blocked, setBlocked] = useState<string[]>(snapshot?.safety.blocked_websites ?? [])
  const [blockedDraft, setBlockedDraft] = useState('')
  const [running, setRunning] = useState<boolean | null>(null)
  const [tabs, setTabs] = useState<Array<{ tab_id: string; url: string; title: string }>>([])
  const [browserError, setBrowserError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!snapshot) return
    setHeadless(snapshot.browser.headless)
    setBlocked(snapshot.safety.blocked_websites)
  }, [snapshot])

  const loadBrowser = useCallback(async () => {
    setBusy(true)
    try {
      const state = await api.browserState()
      setRunning(state.running)
      setTabs(state.tabs)
      setBrowserError(null)
    } catch (err) {
      setRunning(null)
      setTabs([])
      setBrowserError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }, [])

  useEffect(() => {
    void loadBrowser()
  }, [loadBrowser])

  const addBlocked = async () => {
    const value = blockedDraft.trim()
    if (!value) return
    const next = Array.from(new Set([...blocked, value]))
    const ok = await save({ safety: { blocked_websites: next } }, 'Website blocked')
    if (ok) {
      setBlocked(next)
      setBlockedDraft('')
    }
  }

  const removeBlocked = async (value: string) => {
    const next = blocked.filter((item) => item !== value)
    const ok = await save({ safety: { blocked_websites: next } }, 'Website unblocked')
    if (ok) setBlocked(next)
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
      <div className="space-y-4">
        <Card className="p-5">
          <SectionHeading
            title="Managed browser"
            description="Mochi uses its own isolated browser session for web tasks."
            action={<Globe className="size-4 text-accent" aria-hidden />}
          />
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Badge tone={running ? 'success' : running === false ? 'neutral' : 'warning'} dot>
              {running ? 'Running' : running === false ? 'Closed' : 'Unknown'}
            </Badge>
            <Button size="sm" variant="outline" onClick={() => void loadBrowser()} loading={busy} icon={<RefreshCw className="size-3.5" aria-hidden />}>
              Refresh state
            </Button>
          </div>

          {browserError && (
            <p className="mt-3 rounded-2xl border border-warning/25 bg-warning-soft/60 px-3 py-2 text-xs text-warning">
              {browserError}
            </p>
          )}

          {tabs.length > 0 && (
            <div className="mt-3 space-y-1.5">
              {tabs.map((tab) => (
                <div key={tab.tab_id} className="flex items-center gap-2 rounded-xl border border-line bg-surface-muted/30 px-2.5 py-1.5">
                  <Globe className="size-3.5 shrink-0 text-ink-faint" aria-hidden />
                  <p className="min-w-0 flex-1 truncate text-[11px] font-semibold text-ink-soft">{tab.title || tab.url}</p>
                  <span className="max-w-[45%] truncate font-mono text-[10px] text-ink-faint">{tab.url}</span>
                </div>
              ))}
            </div>
          )}

          <div className="mt-4">
            <Switch
              checked={headless}
              onChange={(next) => {
                setHeadless(next)
                void save({ browser: { headless: next } }, next ? 'Browser will run hidden' : 'Browser will show its window').then((ok) => {
                  if (!ok) setHeadless(!next)
                })
              }}
              label="Run the browser hidden"
              description="Headless mode is faster, but you cannot watch the page. Some sites may behave differently."
            />
          </div>
        </Card>

        <Card className="p-5">
          <SectionHeading
            title="Blocked websites"
            description="Mochi's browser policy refuses these domains."
            action={<Lock className="size-4 text-danger" aria-hidden />}
          />
          <div className="mt-3 flex flex-wrap gap-1.5">
            {blocked.length === 0 ? (
              <p className="text-[11px] text-ink-faint">No websites are blocked.</p>
            ) : (
              blocked.map((site) => (
                <span key={site} className="inline-flex items-center gap-1 rounded-lg border border-line bg-surface-muted px-2 py-1 text-[11px] font-semibold text-ink-soft">
                  {site}
                  <button
                    type="button"
                    onClick={() => void removeBlocked(site)}
                    className="ring-focus rounded p-0.5 text-ink-faint hover:text-danger"
                    aria-label={`Unblock ${site}`}
                  >
                    <X className="size-3" aria-hidden />
                  </button>
                </span>
              ))
            )}
          </div>
          <div className="mt-3 flex gap-2">
            <Input
              value={blockedDraft}
              onChange={(event) => setBlockedDraft(event.target.value)}
              placeholder="example.com"
              aria-label="Website to block"
              onKeyDown={(event) => event.key === 'Enter' && void addBlocked()}
              spellCheck={false}
            />
            <Button variant="soft" onClick={() => void addBlocked()} disabled={!blockedDraft.trim()} icon={<Plus className="size-4" aria-hidden />}>
              Block
            </Button>
          </div>
        </Card>
      </div>

      <Card className="p-5">
        <SectionHeading title="How the browser is used" />
        <ul className="mt-3 space-y-2.5 text-xs leading-relaxed text-ink-soft">
          <li className="flex gap-2">
            <Globe className="mt-0.5 size-3.5 shrink-0 text-accent" aria-hidden />
            It opens only when a task or you explicitly ask for a page.
          </li>
          <li className="flex gap-2">
            <ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-success" aria-hidden />
            Navigation is checked against the blocked list and permission mode.
          </li>
          <li className="flex gap-2">
            <Eye className="mt-0.5 size-3.5 shrink-0 text-info" aria-hidden />
            Screenshots and current URLs appear in Live Activity and Computer.
          </li>
          <li className="flex gap-2">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden />
            If the optional browser engine is not installed, opening a page returns a real error instead of
            pretending to browse.
          </li>
        </ul>
        <Button
          variant="outline"
          size="sm"
          className="mt-4"
          icon={<ExternalLink className="size-3.5" aria-hidden />}
          onClick={() => void loadBrowser()}
          loading={busy}
        >
          Check backend browser state
        </Button>
      </Card>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* General                                                                    */
/* -------------------------------------------------------------------------- */

function GeneralPanel({
  snapshot,
  emergency,
  stoppedLoading,
  onStop,
  onResume,
}: {
  snapshot: SettingsSnapshot | null
  emergency: boolean
  stoppedLoading: boolean
  onStop: () => void
  onResume: () => void
}) {
  const [confirmOpen, setConfirmOpen] = useState(false)

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
      <div className="space-y-4">
        <Card className="p-5">
          <SectionHeading
            title="About Mochi"
            description="A local-first companion that shows what it actually did."
            action={<MascotAvatarSmall />}
          />
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            <InfoRow label="Version" value={snapshot?.app?.version ?? '1.0.0'} />
            <InfoRow label="Environment" value={snapshot?.app?.environment ?? 'unknown'} />
            <InfoRow label="Backend" value={API_BASE} mono />
            <InfoRow label="Events" value={WS_URL} mono />
            <InfoRow label="Data folder" value={snapshot?.app?.data_dir ?? snapshot?.runtime?.data_dir ?? '—'} mono />
            <InfoRow label="Log folder" value={snapshot?.app?.log_dir ?? snapshot?.runtime?.log_dir ?? '—'} mono />
          </div>
        </Card>

        <Card className="p-5">
          <SectionHeading
            title="Runtime"
            description="Read from the live backend configuration."
            action={<Wifi className="size-4 text-accent" aria-hidden />}
          />
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            <InfoRow label="Host" value={snapshot?.runtime?.host ?? '—'} mono />
            <InfoRow label="Port" value={String(snapshot?.runtime?.port ?? '—')} mono />
            <InfoRow label="Permission mode" value={snapshot?.permission_mode ?? '—'} />
            <InfoRow label="Tools" value={String(snapshot?.tools_count ?? '—')} />
            <InfoRow label="Provider" value={snapshot?.llm.provider ?? '—'} />
            <InfoRow label="Model" value={snapshot?.llm.model || 'not configured'} />
          </div>
        </Card>

        <Card className="border-danger/25 bg-danger-soft/25 p-5">
          <SectionHeading
            title="Emergency controls"
            description="Stop every running task immediately. This never approves anything."
            action={<AlertTriangle className="size-4 text-danger" aria-hidden />}
          />
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {emergency ? (
              <Button variant="success" loading={stoppedLoading} onClick={onResume} icon={<Play className="size-4" aria-hidden />}>
                Resume Mochi
              </Button>
            ) : confirmOpen ? (
              <>
                <Button variant="danger" loading={stoppedLoading} onClick={() => {
                  onStop()
                  setConfirmOpen(false)
                }} icon={<Square className="size-3.5 fill-current" aria-hidden />}>
                  Yes, stop everything
                </Button>
                <Button variant="ghost" onClick={() => setConfirmOpen(false)}>
                  Cancel
                </Button>
              </>
            ) : (
              <Button variant="danger" onClick={() => setConfirmOpen(true)} icon={<Square className="size-3.5 fill-current" aria-hidden />}>
                Emergency stop
              </Button>
            )}
            <Badge tone={emergency ? 'danger' : 'success'} dot>
              {emergency ? 'Paused' : 'Running normally'}
            </Badge>
          </div>
          <p className="mt-2 text-[11px] leading-relaxed text-ink-faint">
            This calls the real backend stop endpoint and cancels active tasks. Nothing is approved by stopping.
          </p>
        </Card>
      </div>

      <div className="space-y-4">
        <Card className="p-5">
          <SectionHeading title="Where your data lives" />
          <ul className="mt-3 space-y-2 text-xs leading-relaxed text-ink-soft">
            <li className="flex gap-2">
              <Database className="mt-0.5 size-3.5 shrink-0 text-accent" aria-hidden />
              Conversations, tasks, memories and workflows are stored in the local data folder.
            </li>
            <li className="flex gap-2">
              <Lock className="mt-0.5 size-3.5 shrink-0 text-success" aria-hidden />
              API keys live in Windows Credential Manager, never in the frontend.
            </li>
            <li className="flex gap-2">
              <Globe className="mt-0.5 size-3.5 shrink-0 text-info" aria-hidden />
              The backend binds to localhost only. Remote access is not exposed by the desktop app.
            </li>
          </ul>
        </Card>

        <Card className="p-5">
          <SectionHeading title="Still being honest" />
          <ul className="mt-3 space-y-2 text-xs leading-relaxed text-ink-soft">
            <li>· No GPU/VRAM values are shown because the backend does not expose them.</li>
            <li>· Voice input requires the optional faster-whisper package.</li>
            <li>· Browser automation requires the optional browser engine.</li>
            <li>· A model must be configured before reasoning works.</li>
          </ul>
          <Button
            variant="outline"
            size="sm"
            className="mt-4"
            icon={<RefreshCw className="size-3.5" aria-hidden />}
            onClick={() => window.location.reload()}
          >
            Reload the interface
          </Button>
        </Card>
      </div>
    </div>
  )
}

function MascotAvatarSmall() {
  return <Mascot state="idle" size={56} />
}

function InfoRow({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex min-w-0 flex-col rounded-xl border border-line bg-surface-muted/30 px-3 py-2">
      <span className="text-[10px] font-extrabold uppercase tracking-[0.08em] text-ink-faint">{label}</span>
      <span className={cn('mt-0.5 truncate text-[11px] font-semibold text-ink-soft', mono && 'font-mono')} title={value}>
        {value}
      </span>
    </div>
  )
}

function formatUptime(seconds: number | undefined): string {
  if (!seconds || seconds < 0) return '—'
  const days = Math.floor(seconds / 86_400)
  const hours = Math.floor((seconds % 86_400) / 3600)
  if (days > 0) return `${days}d ${hours}h`
  const minutes = Math.floor((seconds % 3600) / 60)
  return `${hours}h ${minutes}m`
}
