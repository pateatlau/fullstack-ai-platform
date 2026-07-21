export type StreamingIndicatorVariant = 'typing' | 'searching_web'

const LABELS: Record<StreamingIndicatorVariant, { text: string; ariaLabel: string }> = {
  typing: { text: 'typing…', ariaLabel: 'Assistant is typing' },
  searching_web: { text: 'searching web…', ariaLabel: 'Assistant is searching the web' },
}

interface StreamingIndicatorProps {
  variant?: StreamingIndicatorVariant
}

export function StreamingIndicator({ variant = 'typing' }: StreamingIndicatorProps) {
  const { text, ariaLabel } = LABELS[variant]

  return (
    <span
      className="inline-flex items-center gap-2 rounded-chip bg-white/80 px-3 py-2 text-sm text-shell-800/80"
      aria-label={ariaLabel}
    >
      <span className="inline-flex gap-1" aria-hidden="true">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-zinc-400" />
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-zinc-400 [animation-delay:120ms]" />
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-zinc-400 [animation-delay:240ms]" />
      </span>
      {text}
    </span>
  )
}
