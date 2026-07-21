import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
} from 'react'
import {
  getProviderOption,
  providerModelOptions,
  type ProviderName,
} from '../constants/providerModels'
import type { ProviderCapabilityFlags } from '../hooks/useChatStreamingEnabled'

interface ComposerProps {
  onSend: (
    content: string,
    provider?: ProviderName,
    model?: string,
    options?: { useWebSearch?: boolean; useDocuments?: boolean },
  ) => void
  onStop: () => void
  isStreaming: boolean
  /** When false, in-flight status uses "Waiting for response" instead of "Streaming response". */
  showStreamingStatus?: boolean
  /** Guests use the fixed system default and never see the switcher (plan Section 3.2). */
  canSwitchProvider: boolean
  /** True when sending is blocked (e.g. guest daily quota reached, plan Section 3.1). */
  disabled?: boolean
  isAuthenticated: boolean
  toolsEnabled: boolean
  ragEnabled: boolean
  capabilitiesByProvider: Partial<Record<ProviderName, ProviderCapabilityFlags>>
  /** When true, unified toggles route through non-streaming chat (Phase 3). */
  streamingOnlyMode: boolean
}

const TEXTAREA_LINE_HEIGHT_PX = 24
const TEXTAREA_MAX_LINES = 6
const TEXTAREA_MIN_HEIGHT_PX = TEXTAREA_LINE_HEIGHT_PX * 2
const TEXTAREA_MAX_HEIGHT_PX = TEXTAREA_LINE_HEIGHT_PX * TEXTAREA_MAX_LINES

export function Composer({
  onSend,
  onStop,
  isStreaming,
  showStreamingStatus = true,
  canSwitchProvider,
  disabled = false,
  isAuthenticated,
  toolsEnabled,
  ragEnabled,
  capabilitiesByProvider,
  streamingOnlyMode,
}: ComposerProps) {
  const [value, setValue] = useState('')
  const [selectedProvider, setSelectedProvider] = useState<ProviderName>('openai')
  const [selectedModel, setSelectedModel] = useState(getProviderOption('openai').model)
  const [isProviderSettingsExpanded, setIsProviderSettingsExpanded] = useState(false)
  const [useWebSearch, setUseWebSearch] = useState(false)
  const [useDocuments, setUseDocuments] = useState(false)
  const selectedProviderRef = useRef<ProviderName>('openai')
  const selectedModelRef = useRef(getProviderOption('openai').model)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const [keyboardInset, setKeyboardInset] = useState(0)
  const hasMessage = value.trim().length > 0
  const isBlocked = isStreaming || disabled
  const modelOptions = providerModelOptions.filter((option) => option.provider === selectedProvider)
  const selectedProviderLabel = getProviderOption(selectedProvider).label
  const providerSupportsTools =
    capabilitiesByProvider[selectedProvider]?.supports_tool_calling ?? true

  const webSearchDisabledReason = !toolsEnabled
    ? 'Web search is not enabled on this server.'
    : !providerSupportsTools
      ? 'The selected provider does not support tool calling.'
      : null

  const documentsDisabledReason = !ragEnabled
    ? 'Document grounding is not enabled on this server.'
    : null

  const webSearchDisabled = isBlocked || !toolsEnabled || !providerSupportsTools

  const documentsDisabled = isBlocked || !ragEnabled

  const statusTone = isStreaming
    ? 'bg-amber-100 text-amber-800'
    : disabled
      ? 'bg-danger-100 text-danger-600'
      : 'bg-zinc-100 text-zinc-600'

  const statusChipClassName = [
    'shrink-0 whitespace-nowrap rounded-chip px-2.5 py-1 text-[11px] font-medium',
    statusTone,
  ].join(' ')

  const renderStatusChip = (className?: string) => (
    <span className={[statusChipClassName, className].filter(Boolean).join(' ')} aria-live="polite">
      {isStreaming ? (
        showStreamingStatus ? (
          <>
            <span className="sm:hidden">Streaming</span>
            <span className="hidden sm:inline">Streaming response</span>
          </>
        ) : (
          <>
            <span className="sm:hidden">Waiting</span>
            <span className="hidden sm:inline">Waiting for response</span>
          </>
        )
      ) : disabled ? (
        <>
          <span className="sm:hidden">Blocked</span>
          <span className="hidden sm:inline">Sending blocked</span>
        </>
      ) : hasMessage ? (
        <>
          <span className="sm:hidden">Ready</span>
          <span className="hidden sm:inline">Ready to send</span>
        </>
      ) : (
        <>
          <span className="sm:hidden">Waiting</span>
          <span className="hidden sm:inline">Waiting for input</span>
        </>
      )}
    </span>
  )

  const submit = () => {
    const trimmed = value.trim()
    if (!trimmed || isBlocked) return
    const toggleOptions = {
      useWebSearch: useWebSearch && isAuthenticated && toolsEnabled && providerSupportsTools,
      useDocuments: useDocuments && isAuthenticated && ragEnabled,
    }
    if (canSwitchProvider) {
      onSend(trimmed, selectedProviderRef.current, selectedModelRef.current, toggleOptions)
    } else {
      // Guests omit provider/model; the server applies the system default.
      onSend(trimmed, undefined, undefined, toggleOptions)
    }
    setValue('')
  }

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault()
    submit()
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  const handleProviderChange = (event: ChangeEvent<HTMLSelectElement>) => {
    const nextProvider = event.target.value as ProviderName
    const nextOption = getProviderOption(nextProvider)
    selectedProviderRef.current = nextProvider
    selectedModelRef.current = nextOption.model
    setSelectedProvider(nextProvider)
    setSelectedModel(nextOption.model)
    setIsProviderSettingsExpanded(false)
  }

  const handleModelChange = (event: ChangeEvent<HTMLSelectElement>) => {
    selectedModelRef.current = event.target.value
    setSelectedModel(event.target.value)
    setIsProviderSettingsExpanded(false)
  }

  const adjustTextareaHeight = () => {
    const textarea = textareaRef.current
    if (!textarea) return
    textarea.style.height = 'auto'
    const nextHeight = Math.min(
      Math.max(textarea.scrollHeight, TEXTAREA_MIN_HEIGHT_PX),
      TEXTAREA_MAX_HEIGHT_PX,
    )
    textarea.style.height = `${nextHeight}px`
  }

  useEffect(() => {
    adjustTextareaHeight()
  }, [value])

  useEffect(() => {
    const viewport = window.visualViewport
    if (!viewport) return

    const updateKeyboardInset = () => {
      const inset = Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop)
      setKeyboardInset(inset)
    }

    viewport.addEventListener('resize', updateKeyboardInset)
    viewport.addEventListener('scroll', updateKeyboardInset)
    updateKeyboardInset()

    return () => {
      viewport.removeEventListener('resize', updateKeyboardInset)
      viewport.removeEventListener('scroll', updateKeyboardInset)
    }
  }, [])

  return (
    <form
      className="sticky bottom-0 z-10 mt-3 bg-linear-to-t from-shell-100 via-shell-100/95 to-transparent px-1 pt-4 sm:px-0"
      style={{
        paddingBottom: `calc(${keyboardInset}px + env(safe-area-inset-bottom) + 0.5rem)`,
      }}
      onSubmit={handleSubmit}
      aria-label="Message composer"
    >
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-3 rounded-[1.75rem] border border-zinc-200 bg-white/96 p-3 shadow-chat-card backdrop-blur sm:p-4">
        <div className="hidden items-center justify-between gap-3 sm:flex">
          <p className="text-sm font-semibold text-zinc-950">Message</p>
          {renderStatusChip()}
        </div>

        {canSwitchProvider ? (
          <div className="space-y-3">
            <button
              type="button"
              className="flex w-full items-center justify-between gap-3 rounded-xl border border-zinc-200 bg-zinc-50 px-3 py-2.5 text-left text-sm text-shell-950 transition hover:bg-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 sm:hidden"
              aria-expanded={isProviderSettingsExpanded}
              aria-controls="provider-model-settings"
              onClick={() => setIsProviderSettingsExpanded((expanded) => !expanded)}
            >
              <span className="min-w-0">
                <span className="block text-[11px] font-semibold uppercase tracking-[0.16em] text-zinc-600">
                  Provider & model
                </span>
                <span className="mt-0.5 block truncate font-medium">
                  {selectedProviderLabel} · {selectedModel}
                </span>
              </span>
              <span className="shrink-0 text-xs font-semibold text-brand-600">
                {isProviderSettingsExpanded ? 'Hide' : 'Change'}
              </span>
            </button>

            <div
              id="provider-model-settings"
              className={[
                'grid gap-3 sm:grid-cols-2',
                isProviderSettingsExpanded ? 'grid' : 'hidden sm:grid',
              ].join(' ')}
            >
              <label className="flex flex-col gap-1.5">
                <span className="text-xs font-semibold uppercase tracking-[0.16em] text-zinc-600">
                  Provider
                </span>
                <select
                  className="h-11 rounded-xl border border-zinc-200 bg-zinc-50 px-3 text-sm text-shell-950 outline-none transition focus:border-brand-500/60 focus:bg-white focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:bg-zinc-100"
                  value={selectedProvider}
                  onChange={handleProviderChange}
                  disabled={isBlocked}
                  aria-label="Provider"
                >
                  {providerModelOptions.map((option) => (
                    <option key={option.provider} value={option.provider}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>

              <label className="flex flex-col gap-1.5">
                <span className="text-xs font-semibold uppercase tracking-[0.16em] text-zinc-600">
                  Model
                </span>
                <select
                  className="h-11 rounded-xl border border-zinc-200 bg-zinc-50 px-3 text-sm text-shell-950 outline-none transition focus:border-brand-500/60 focus:bg-white focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:bg-zinc-100"
                  value={selectedModel}
                  onChange={handleModelChange}
                  disabled={isBlocked}
                  aria-label="Model"
                >
                  {modelOptions.map((option) => (
                    <option key={option.model} value={option.model}>
                      {option.model}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </div>
        ) : null}

        {isAuthenticated ? (
          <div className="flex flex-wrap items-center gap-3 rounded-xl border border-zinc-200 bg-zinc-50 px-3 py-2.5">
            <label
              className="inline-flex items-center gap-2 text-sm text-shell-950"
              title={webSearchDisabledReason ?? undefined}
            >
              <input
                type="checkbox"
                className="size-4 rounded border-zinc-300 text-brand-600 focus:ring-brand-500 disabled:cursor-not-allowed"
                checked={useWebSearch}
                onChange={(event) => setUseWebSearch(event.target.checked)}
                disabled={webSearchDisabled}
                aria-label="Web search"
              />
              <span>Web search</span>
            </label>

            <label
              className="inline-flex items-center gap-2 text-sm text-shell-950"
              title={documentsDisabledReason ?? undefined}
            >
              <input
                type="checkbox"
                className="size-4 rounded border-zinc-300 text-brand-600 focus:ring-brand-500 disabled:cursor-not-allowed"
                checked={useDocuments}
                onChange={(event) => setUseDocuments(event.target.checked)}
                disabled={documentsDisabled}
                aria-label="My documents"
              />
              <span>My documents</span>
            </label>

            <a
              href="/documents"
              className="ml-auto text-xs font-semibold text-brand-600 underline-offset-2 hover:underline"
            >
              Manage documents
            </a>
          </div>
        ) : null}

        {isAuthenticated && streamingOnlyMode && useDocuments ? (
          <p className="text-xs text-zinc-600">
            Document grounding uses non-streaming chat until streaming RAG ships in a later release.
            Web search works in streaming mode when enabled.
          </p>
        ) : null}

        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <label className="flex min-w-0 flex-1">
            <span className="sr-only">Message input</span>
            <textarea
              ref={textareaRef}
              className="min-h-12 w-full resize-none overflow-y-auto rounded-[1.25rem] border border-zinc-200 bg-zinc-50 px-4 py-3 text-sm leading-6 text-shell-950 outline-none transition placeholder:text-zinc-500 focus:border-brand-500/60 focus:bg-white focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:bg-zinc-100 sm:min-h-24"
              value={value}
              onChange={(event) => {
                setValue(event.target.value)
                adjustTextareaHeight()
              }}
              onKeyDown={handleKeyDown}
              placeholder="Ask something…"
              disabled={isBlocked}
              rows={1}
              aria-label="Message input"
            />
          </label>

          <div className="flex items-center justify-end gap-2 self-end sm:flex-col sm:items-stretch sm:self-end">
            {isStreaming ? (
              <p className="hidden text-xs text-zinc-500 sm:block sm:max-w-28 sm:text-right">
                Stop the current response at any time.
              </p>
            ) : null}

            {renderStatusChip('sm:hidden')}

            {isStreaming ? (
              <button
                type="button"
                className="inline-flex min-h-11 items-center justify-center rounded-xl bg-danger-600 px-4 py-3 text-sm font-semibold text-white transition hover:bg-danger-600/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger-600"
                onMouseDown={(event) => {
                  event.preventDefault()
                  onStop()
                }}
                onClick={onStop}
              >
                Stop
              </button>
            ) : (
              <button
                type="submit"
                className="inline-flex min-h-11 items-center justify-center rounded-xl bg-brand-600 px-4 py-3 text-sm font-semibold text-white transition hover:bg-brand-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:bg-brand-500/40"
                disabled={!hasMessage || disabled}
              >
                Send
              </button>
            )}
          </div>
        </div>
      </div>
    </form>
  )
}
