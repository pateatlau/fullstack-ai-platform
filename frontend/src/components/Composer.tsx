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
import { ChevronDownIcon, DocumentIcon, GlobeIcon } from './icons/ShellIcons'

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
}

const TEXTAREA_LINE_HEIGHT_PX = 24
const TEXTAREA_MAX_LINES = 6
const TEXTAREA_MIN_HEIGHT_PX = TEXTAREA_LINE_HEIGHT_PX
const TEXTAREA_MAX_HEIGHT_PX = TEXTAREA_LINE_HEIGHT_PX * TEXTAREA_MAX_LINES

const PROVIDER_MODEL_TOOLTIP = 'Choose which AI provider and model to use for this message.'
const WEB_SEARCH_TOOLTIP = 'Search the web for current information to include in the reply.'
const MY_DOCUMENTS_TOOLTIP = 'Ground the reply in your uploaded documents.'
const MANAGE_DOCUMENTS_TOOLTIP = 'Open your documents library to upload or manage files.'

function toolChipClassName(active: boolean, disabled: boolean): string {
  return [
    'inline-flex min-h-9 cursor-pointer items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition',
    'focus-within:outline-none focus-within:ring-2 focus-within:ring-brand-500',
    disabled
      ? 'cursor-not-allowed border-zinc-200 bg-zinc-100 text-zinc-400'
      : active
        ? 'border-brand-500/40 bg-brand-500/10 text-brand-600'
        : 'border-zinc-200 bg-zinc-50 text-shell-950 hover:bg-zinc-100',
  ].join(' ')
}

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
  const providerPanelRef = useRef<HTMLDivElement>(null)
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
  const showStatusChip = isStreaming || disabled

  const statusTone = isStreaming ? 'bg-amber-100 text-amber-800' : 'bg-danger-100 text-danger-600'

  const statusChipClassName = [
    'shrink-0 whitespace-nowrap rounded-chip px-2 py-1 text-[11px] font-medium',
    statusTone,
  ].join(' ')

  const renderStatusChip = () => (
    <span className={statusChipClassName} aria-live="polite">
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
      ) : (
        <>
          <span className="sm:hidden">Blocked</span>
          <span className="hidden sm:inline">Sending blocked</span>
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

  useEffect(() => {
    if (!isProviderSettingsExpanded) return

    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target
      if (!(target instanceof Node)) return
      if (providerPanelRef.current?.contains(target)) return
      setIsProviderSettingsExpanded(false)
    }

    const handleEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsProviderSettingsExpanded(false)
      }
    }

    document.addEventListener('mousedown', handlePointerDown)
    document.addEventListener('keydown', handleEscape)
    return () => {
      document.removeEventListener('mousedown', handlePointerDown)
      document.removeEventListener('keydown', handleEscape)
    }
  }, [isProviderSettingsExpanded])

  return (
    <form
      className="sticky bottom-0 z-10 mt-2 bg-linear-to-t from-shell-100 via-shell-100/95 to-transparent px-1 pt-2 sm:px-0"
      style={{
        paddingBottom: `calc(${keyboardInset}px + env(safe-area-inset-bottom) + 0.5rem)`,
      }}
      onSubmit={handleSubmit}
      aria-label="Message composer"
    >
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-2 rounded-2xl border border-zinc-200 bg-white/96 p-2 shadow-chat-card backdrop-blur sm:p-2.5">
        <div className="flex items-end gap-2">
          <label className="flex min-w-0 flex-1">
            <span className="sr-only">Message input</span>
            <textarea
              ref={textareaRef}
              className="min-h-10 w-full resize-none overflow-y-auto rounded-xl border border-zinc-200 bg-zinc-50 px-3 py-2.5 text-sm leading-6 text-shell-950 outline-none transition placeholder:text-zinc-500 focus:border-brand-500/60 focus:bg-white focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:bg-zinc-100"
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

          {isStreaming ? (
            <button
              type="button"
              className="inline-flex min-h-11 shrink-0 cursor-pointer items-center justify-center rounded-xl bg-danger-600 px-3.5 py-2.5 text-sm font-semibold text-white transition hover:bg-danger-600/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger-600 sm:min-w-20"
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
              className="inline-flex min-h-11 shrink-0 cursor-pointer items-center justify-center rounded-xl bg-brand-600 px-3.5 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:bg-brand-500/40 sm:min-w-20"
              disabled={!hasMessage || disabled}
            >
              Send
            </button>
          )}
        </div>

        {canSwitchProvider || isAuthenticated || showStatusChip ? (
          <div className="flex flex-wrap items-center gap-1.5">
            {canSwitchProvider ? (
              <div ref={providerPanelRef} className="relative min-w-0">
                <button
                  type="button"
                  className="inline-flex max-w-full min-h-9 items-center gap-1.5 rounded-lg border border-zinc-200 bg-zinc-50 px-2.5 py-1.5 text-left text-xs font-medium text-shell-950 transition hover:bg-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:opacity-60"
                  aria-expanded={isProviderSettingsExpanded}
                  aria-controls="provider-model-settings"
                  title={PROVIDER_MODEL_TOOLTIP}
                  disabled={isBlocked}
                  onClick={() => setIsProviderSettingsExpanded((expanded) => !expanded)}
                >
                  <span className="sr-only">Provider & model</span>
                  <span className="truncate">
                    {selectedProviderLabel} · {selectedModel}
                  </span>
                  <ChevronDownIcon
                    className={[
                      'h-3.5 w-3.5 shrink-0 text-zinc-500 transition',
                      isProviderSettingsExpanded ? 'rotate-180' : '',
                    ].join(' ')}
                  />
                </button>

                <div
                  id="provider-model-settings"
                  className={[
                    'absolute bottom-full left-0 z-20 mb-1.5 w-[min(100vw-2rem,22rem)] rounded-xl border border-zinc-200 bg-white p-2.5 shadow-chat-card sm:w-96',
                    isProviderSettingsExpanded ? 'grid gap-2 sm:grid-cols-2' : 'hidden',
                  ].join(' ')}
                >
                  <label className="flex flex-col gap-1">
                    <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-zinc-600">
                      Provider
                    </span>
                    <select
                      className="h-10 rounded-lg border border-zinc-200 bg-zinc-50 px-2.5 text-sm text-shell-950 outline-none transition focus:border-brand-500/60 focus:bg-white focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:bg-zinc-100"
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

                  <label className="flex flex-col gap-1">
                    <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-zinc-600">
                      Model
                    </span>
                    <select
                      className="h-10 rounded-lg border border-zinc-200 bg-zinc-50 px-2.5 text-sm text-shell-950 outline-none transition focus:border-brand-500/60 focus:bg-white focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:bg-zinc-100"
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
              <>
                <label
                  className={toolChipClassName(useWebSearch, webSearchDisabled)}
                  title={webSearchDisabledReason ?? WEB_SEARCH_TOOLTIP}
                >
                  <input
                    type="checkbox"
                    className="size-3.5 shrink-0 rounded border-zinc-300 accent-brand-600 focus:ring-brand-500 disabled:cursor-not-allowed"
                    checked={useWebSearch}
                    onChange={(event) => setUseWebSearch(event.target.checked)}
                    disabled={webSearchDisabled}
                    aria-label="Web search"
                  />
                  <GlobeIcon className="h-3.5 w-3.5 shrink-0" />
                  <span>
                    <span className="sm:hidden">Search</span>
                    <span className="hidden sm:inline">Web search</span>
                  </span>
                </label>

                <label
                  className={toolChipClassName(useDocuments, documentsDisabled)}
                  title={documentsDisabledReason ?? MY_DOCUMENTS_TOOLTIP}
                >
                  <input
                    type="checkbox"
                    className="size-3.5 shrink-0 rounded border-zinc-300 accent-brand-600 focus:ring-brand-500 disabled:cursor-not-allowed"
                    checked={useDocuments}
                    onChange={(event) => setUseDocuments(event.target.checked)}
                    disabled={documentsDisabled}
                    aria-label="My documents"
                  />
                  <DocumentIcon className="h-3.5 w-3.5 shrink-0" />
                  <span>
                    <span className="sm:hidden">Docs</span>
                    <span className="hidden sm:inline">My documents</span>
                  </span>
                </label>

                <a
                  href="/documents"
                  title={MANAGE_DOCUMENTS_TOOLTIP}
                  className="text-[11px] font-semibold text-brand-600 underline-offset-2 hover:underline sm:text-xs"
                >
                  Manage
                </a>
              </>
            ) : null}

            {showStatusChip ? <div className="ml-auto">{renderStatusChip()}</div> : null}
          </div>
        ) : null}
      </div>
    </form>
  )
}
