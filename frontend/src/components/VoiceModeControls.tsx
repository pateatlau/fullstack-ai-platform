import {
  useCallback,
  useRef,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react'
import { MicIcon, StopCircleIcon } from './icons/ShellIcons'

export interface VoiceModeControlsProps {
  voiceModeEnabled: boolean
  onVoiceModeChange: (enabled: boolean) => void
  isRecording: boolean
  isSpeaking: boolean
  isVoiceReady: boolean
  onMicPressStart: () => void
  onMicPressEnd: () => void
  onInterrupt: () => void
  transcriptPartial?: string
  disabled?: boolean
  micError?: string | null
  hasActiveSession: boolean
}

const VOICE_MODE_TOOLTIP = 'Switch to voice input — hold the mic to speak instead of typing.'
const MIC_TOOLTIP = 'Hold to speak. Release to send your message.'
const INTERRUPT_TOOLTIP = 'Stop assistant speech and cancel the current reply.'
const SESSION_REQUIRED_MESSAGE = 'Select or start a chat before using voice mode.'

function voiceChipClassName(active: boolean, disabled: boolean): string {
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

export function VoiceModeControls({
  voiceModeEnabled,
  onVoiceModeChange,
  isRecording,
  isSpeaking,
  isVoiceReady,
  onMicPressStart,
  onMicPressEnd,
  onInterrupt,
  transcriptPartial = '',
  disabled = false,
  micError = null,
  hasActiveSession,
}: VoiceModeControlsProps) {
  const micHeldRef = useRef(false)

  const micDisabled = disabled || !voiceModeEnabled || !isVoiceReady || isSpeaking
  const showMic = voiceModeEnabled
  const showInterrupt = voiceModeEnabled && isSpeaking

  const beginMicPress = useCallback(() => {
    if (micDisabled || micHeldRef.current) return
    micHeldRef.current = true
    onMicPressStart()
  }, [micDisabled, onMicPressStart])

  const endMicPress = useCallback(() => {
    if (!micHeldRef.current) return
    micHeldRef.current = false
    onMicPressEnd()
  }, [onMicPressEnd])

  const handleMicPointerDown = useCallback(
    (event: ReactPointerEvent<HTMLButtonElement>) => {
      if (micDisabled || micHeldRef.current) return
      event.preventDefault()
      if (typeof event.currentTarget.setPointerCapture === 'function') {
        event.currentTarget.setPointerCapture(event.pointerId)
      }
      beginMicPress()
    },
    [micDisabled, beginMicPress],
  )

  const handleMicPointerUp = useCallback(
    (event: ReactPointerEvent<HTMLButtonElement>) => {
      if (!micHeldRef.current) return
      if (
        typeof event.currentTarget.hasPointerCapture === 'function' &&
        event.currentTarget.hasPointerCapture(event.pointerId)
      ) {
        event.currentTarget.releasePointerCapture(event.pointerId)
      }
      endMicPress()
    },
    [endMicPress],
  )

  const handleMicKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLButtonElement>) => {
      if (micDisabled) return
      if (event.key !== ' ' && event.key !== 'Enter') return
      if (event.repeat) return
      event.preventDefault()
      beginMicPress()
    },
    [micDisabled, beginMicPress],
  )

  const handleMicKeyUp = useCallback(
    (event: ReactKeyboardEvent<HTMLButtonElement>) => {
      if (event.key !== ' ' && event.key !== 'Enter') return
      event.preventDefault()
      endMicPress()
    },
    [endMicPress],
  )

  const handleMicBlur = useCallback(() => {
    endMicPress()
  }, [endMicPress])

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <label className={voiceChipClassName(voiceModeEnabled, disabled)} title={VOICE_MODE_TOOLTIP}>
        <input
          type="checkbox"
          className="size-3.5 shrink-0 rounded border-zinc-300 accent-brand-600 focus:ring-brand-500 disabled:cursor-not-allowed"
          checked={voiceModeEnabled}
          onChange={(event) => onVoiceModeChange(event.target.checked)}
          disabled={disabled}
          aria-label="Voice mode"
        />
        <MicIcon className="h-3.5 w-3.5 shrink-0" />
        <span>
          <span className="sm:hidden">Voice</span>
          <span className="hidden sm:inline">Voice mode</span>
        </span>
      </label>

      {voiceModeEnabled && !hasActiveSession ? (
        <span className="text-[11px] text-amber-700 sm:text-xs" role="status">
          {SESSION_REQUIRED_MESSAGE}
        </span>
      ) : null}

      {showMic ? (
        <button
          type="button"
          className={[
            'inline-flex min-h-11 min-w-11 shrink-0 cursor-pointer touch-none items-center justify-center rounded-xl border transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
            isRecording
              ? 'border-danger-600/40 bg-danger-600/10 text-danger-600 animate-pulse'
              : 'border-brand-500/40 bg-brand-500/10 text-brand-600 hover:bg-brand-500/15',
            micDisabled ? 'cursor-not-allowed opacity-50' : '',
          ].join(' ')}
          aria-label={isRecording ? 'Recording — release to send' : 'Hold to speak'}
          aria-pressed={isRecording}
          title={MIC_TOOLTIP}
          disabled={micDisabled}
          onPointerDown={handleMicPointerDown}
          onPointerUp={handleMicPointerUp}
          onPointerCancel={handleMicPointerUp}
          onLostPointerCapture={handleMicPointerUp}
          onKeyDown={handleMicKeyDown}
          onKeyUp={handleMicKeyUp}
          onBlur={handleMicBlur}
        >
          <MicIcon className="h-5 w-5" />
        </button>
      ) : null}

      {showInterrupt ? (
        <button
          type="button"
          className="inline-flex min-h-11 shrink-0 cursor-pointer items-center justify-center gap-1.5 rounded-xl bg-danger-600 px-3 py-2.5 text-sm font-semibold text-white transition hover:bg-danger-600/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger-600"
          aria-label="Interrupt assistant"
          title={INTERRUPT_TOOLTIP}
          onClick={onInterrupt}
        >
          <StopCircleIcon className="h-4 w-4" />
          <span className="hidden sm:inline">Interrupt</span>
        </button>
      ) : null}

      {transcriptPartial ? (
        <p
          className="min-w-0 flex-1 truncate text-xs italic text-zinc-600 sm:text-sm"
          aria-live="polite"
        >
          {transcriptPartial}
        </p>
      ) : null}

      {micError ? (
        <p className="w-full text-xs text-danger-600" role="alert">
          {micError}
        </p>
      ) : null}
    </div>
  )
}
