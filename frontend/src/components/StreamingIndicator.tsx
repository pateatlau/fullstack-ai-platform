import { TypingDots } from './LoadingIndicator'

export type StreamingIndicatorVariant =
  'typing' | 'transcribing' | 'searching_web' | 'searching_documents'

const LABELS: Record<StreamingIndicatorVariant, { text: string; ariaLabel: string }> = {
  typing: { text: 'typing…', ariaLabel: 'Assistant is typing' },
  transcribing: { text: 'transcribing…', ariaLabel: 'Transcribing your speech' },
  searching_web: { text: 'searching web…', ariaLabel: 'Assistant is searching the web' },
  searching_documents: {
    text: 'searching docs…',
    ariaLabel: 'Assistant is searching docs',
  },
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
      <TypingDots />
      {text}
    </span>
  )
}
