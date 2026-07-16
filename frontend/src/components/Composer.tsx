import { useState, type ChangeEvent, type FormEvent, type KeyboardEvent } from 'react'
import {
  getProviderOption,
  providerModelOptions,
  type ProviderName,
} from '../constants/providerModels'

interface ComposerProps {
  onSend: (content: string, provider: ProviderName, model: string) => void
  onStop: () => void
  isStreaming: boolean
}

export function Composer({ onSend, onStop, isStreaming }: ComposerProps) {
  const [value, setValue] = useState('')
  const [selectedProvider, setSelectedProvider] = useState<ProviderName>('openai')
  const [selectedModel, setSelectedModel] = useState(getProviderOption('openai').model)
  const hasMessage = value.trim().length > 0
  const modelOptions = providerModelOptions.filter((option) => option.provider === selectedProvider)

  const submit = () => {
    const trimmed = value.trim()
    if (!trimmed || isStreaming) return
    onSend(trimmed, selectedProvider, selectedModel)
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
    setSelectedProvider(nextProvider)
    setSelectedModel(nextOption.model)
  }

  const handleModelChange = (event: ChangeEvent<HTMLSelectElement>) => {
    setSelectedModel(event.target.value)
  }

  return (
    <form
      className="sticky bottom-0 z-10 mt-3 bg-linear-to-t from-shell-100 via-shell-100/95 to-transparent px-1 pb-[calc(env(safe-area-inset-bottom)+0.5rem)] pt-4 sm:px-0"
      onSubmit={handleSubmit}
      aria-label="Message composer"
    >
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-3 rounded-[1.75rem] border border-zinc-200 bg-white/96 p-3 shadow-chat-card backdrop-blur sm:p-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-zinc-950">Message</p>
            <p className="mt-1 text-xs text-zinc-500">
              Press Enter to send, Shift+Enter for a new line.
            </p>
          </div>
          <span
            className={[
              'rounded-chip px-2.5 py-1 text-[11px] font-medium',
              isStreaming ? 'bg-amber-100 text-amber-800' : 'bg-zinc-100 text-zinc-600',
            ].join(' ')}
            aria-live="polite"
          >
            {isStreaming
              ? 'Streaming response'
              : hasMessage
                ? 'Ready to send'
                : 'Waiting for input'}
          </span>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-semibold uppercase tracking-[0.16em] text-zinc-600">
              Provider
            </span>
            <select
              className="h-11 rounded-xl border border-zinc-200 bg-zinc-50 px-3 text-sm text-shell-950 outline-none transition focus:border-brand-500/60 focus:bg-white focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:bg-zinc-100"
              value={selectedProvider}
              onChange={handleProviderChange}
              disabled={isStreaming}
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
              disabled={isStreaming}
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

        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <label className="flex min-w-0 flex-1">
            <span className="sr-only">Message input</span>
            <textarea
              className="min-h-24 w-full resize-none rounded-[1.25rem] border border-zinc-200 bg-zinc-50 px-4 py-3 text-sm leading-6 text-shell-950 outline-none transition placeholder:text-zinc-500 focus:border-brand-500/60 focus:bg-white focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:bg-zinc-100"
              value={value}
              onChange={(event) => setValue(event.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask something…"
              disabled={isStreaming}
              rows={3}
              aria-label="Message input"
            />
          </label>

          <div className="flex items-center justify-between gap-2 self-end sm:flex-col sm:items-stretch sm:self-end">
            {isStreaming ? (
              <p className="text-xs text-zinc-500 sm:max-w-28 sm:text-right">
                Stop the current response at any time.
              </p>
            ) : null}

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
                disabled={!hasMessage}
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
