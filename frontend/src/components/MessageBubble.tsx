import type { Message } from '../types/chat'
import { StreamingIndicator } from './StreamingIndicator'

interface MessageBubbleProps {
  message: Message
  onRetry?: (messageId: string) => void
  /** When false, in-flight assistant messages omit the "Streaming" status label. */
  showStreamingStatus?: boolean
}

export function MessageBubble({
  message,
  onRetry,
  showStreamingStatus = true,
}: MessageBubbleProps) {
  const isWaitingForFirstToken = message.status === 'streaming' && message.content === ''
  const canRetry = message.canRetry && onRetry && message.role === 'assistant'
  const roleLabel = message.role === 'user' ? 'You' : 'Assistant'

  const alignment = message.role === 'user' ? 'ml-auto' : 'mr-auto'
  const baseBubble = 'max-w-[92%] border px-4 py-3 shadow-sm sm:max-w-[78%]'
  const roleBubble =
    message.role === 'user'
      ? 'rounded-[1.5rem] rounded-br-md border-zinc-300 bg-zinc-200 text-zinc-950'
      : 'rounded-[1.5rem] rounded-bl-md border-zinc-200 bg-zinc-50 text-shell-950'
  const statusTone =
    message.status === 'error'
      ? 'border-danger-600/40 bg-danger-100/60'
      : message.status === 'interrupted'
        ? 'border-amber-500/40 bg-amber-50'
        : ''

  const showStatusBadge =
    message.status !== 'complete' && !(message.status === 'streaming' && !showStreamingStatus)
  const statusLabel = message.status === 'streaming' ? 'Streaming' : message.status

  return (
    <article className={`${alignment} ${baseBubble} ${roleBubble} ${statusTone}`}>
      <div className="mb-2 flex items-center justify-between gap-3">
        <span className="text-[11px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
          {roleLabel}
        </span>
        {showStatusBadge ? (
          <span className="text-[11px] capitalize text-zinc-400">{statusLabel}</span>
        ) : null}
      </div>

      {isWaitingForFirstToken ? (
        <StreamingIndicator />
      ) : (
        <p className="text-sm leading-7 whitespace-pre-wrap [overflow-wrap:anywhere]">
          {message.content}
        </p>
      )}

      {message.status === 'error' && (
        <p className="mt-3 rounded-xl bg-white/70 px-3 py-2 text-xs font-medium text-danger-600">
          {message.errorMessage ?? 'Something went wrong.'}
        </p>
      )}
      {message.status === 'interrupted' && (
        <p className="mt-3 rounded-xl bg-white/70 px-3 py-2 text-xs font-medium text-amber-700">
          {message.errorMessage ?? 'The stream was interrupted before completion.'}
        </p>
      )}
      {message.status === 'stopped' && (
        <p className="mt-3 rounded-xl bg-white/70 px-3 py-2 text-xs font-medium text-amber-700">
          Stopped.
        </p>
      )}
      {canRetry ? (
        <button
          type="button"
          className="mt-3 rounded-chip border border-zinc-300 bg-white px-3 py-1.5 text-xs font-semibold text-zinc-950 transition hover:bg-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
          onClick={() => onRetry(message.id)}
        >
          Retry
        </button>
      ) : null}
    </article>
  )
}
