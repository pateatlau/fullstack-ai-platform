import { useCallback, useEffect, useRef, useState } from 'react'
import {
  MicCapture,
  Pcm16AudioPlayer,
  VoiceClient,
  base64ToBytes,
  type VoiceConnectOptions,
  type WebSocketFactory,
} from '../api/voiceClient'
import type { VoiceServerMessage } from '../types/voice'
import type { TurnCompleteMetadata } from '../types/voice'

const SPEAKING_END_POLL_MS = 100
const SPEAKING_END_IDLE_POLLS = 5
const SPEAKING_END_GRACE_MS = 50
/** Keep interrupt visible across backend TTS segment gaps after the text stream ends. */
const TTS_INTER_SEGMENT_GAP_MS = 2_500

export interface UseVoiceSessionOptions {
  sessionId: string | null
  enabled?: boolean
  accessToken?: string | null
  useWebSearch?: boolean
  useDocuments?: boolean
  provider?: string
  model?: string
  webSocketFactory?: WebSocketFactory
  onSessionStarted?: (voiceSessionId: string) => void
  onTranscriptPartial?: (text: string, stability?: number | null) => void
  onTranscriptFinal?: (text: string) => void
  onStart?: () => void
  onDelta?: (text: string) => void
  onEnd?: (metadata: TurnCompleteMetadata) => void
  onToolStart?: (name: string) => void
  onToolEnd?: (name: string, success: boolean) => void
  onInterrupted?: () => void
  onError?: (error: Error | { code: string; message: string }) => void
  onClose?: (reason: string) => void
}

/**
 * Manages a voice WebSocket session: connect/disconnect, mic capture, assistant
 * audio playback, and event callbacks aligned with ``useChatStream`` where possible.
 */
export function useVoiceSession(options: UseVoiceSessionOptions) {
  const [isConnected, setIsConnected] = useState(false)
  const [isRecording, setIsRecording] = useState(false)
  const [isSpeaking, setIsSpeaking] = useState(false)
  const [voiceSessionId, setVoiceSessionId] = useState<string | null>(null)

  const clientRef = useRef<VoiceClient | null>(null)
  const playerRef = useRef<Pcm16AudioPlayer | null>(null)
  const micRef = useRef<MicCapture | null>(null)
  const recordingGenerationRef = useRef(0)
  const micStartPromiseRef = useRef<Promise<void> | null>(null)
  const micStartInFlightRef = useRef(false)
  const pendingStopRef = useRef(false)
  const turnStartedRef = useRef(false)
  const turnEndedRef = useRef(true)
  const speakingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const isSpeakingRef = useRef(false)
  const lastAudioReceivedAtRef = useRef(0)
  const audioSuppressedRef = useRef(false)
  const optionsRef = useRef(options)

  useEffect(() => {
    optionsRef.current = options
  }, [options])

  const clearSpeakingTimer = useCallback(() => {
    if (speakingTimerRef.current !== null) {
      clearTimeout(speakingTimerRef.current)
      speakingTimerRef.current = null
    }
  }, [])

  const markSpeaking = useCallback(() => {
    if (isSpeakingRef.current) {
      return
    }
    isSpeakingRef.current = true
    setIsSpeaking(true)
  }, [])

  const clearSpeaking = useCallback(() => {
    if (!isSpeakingRef.current) {
      return
    }
    isSpeakingRef.current = false
    setIsSpeaking(false)
  }, [])

  /**
   * Settle `isSpeaking` once the text stream ended *and* queued audio ran out.
   * `turn_complete` mirrors the SSE `end` frame, so it lands while the tail of
   * the reply is still being synthesised and played.
   */
  const scheduleSpeakingEnd = useCallback(() => {
    clearSpeakingTimer()
    if (!turnEndedRef.current) {
      return
    }

    let idlePolls = 0

    const poll = () => {
      const player = playerRef.current
      const remainingMs = player?.remainingPlaybackMs ?? 0
      const hasPending = player?.hasPendingPlayback ?? false
      const awaitingMoreTts =
        turnEndedRef.current &&
        lastAudioReceivedAtRef.current > 0 &&
        Date.now() - lastAudioReceivedAtRef.current < TTS_INTER_SEGMENT_GAP_MS

      if (remainingMs > SPEAKING_END_GRACE_MS || hasPending || awaitingMoreTts) {
        idlePolls = 0
        speakingTimerRef.current = setTimeout(poll, SPEAKING_END_POLL_MS)
        return
      }

      idlePolls += 1
      if (idlePolls < SPEAKING_END_IDLE_POLLS) {
        speakingTimerRef.current = setTimeout(poll, SPEAKING_END_POLL_MS)
        return
      }

      clearSpeaking()
    }

    poll()
  }, [clearSpeaking, clearSpeakingTimer])

  const stopPlayback = useCallback(() => {
    clearSpeakingTimer()
    lastAudioReceivedAtRef.current = 0
    playerRef.current?.stop()
    playerRef.current = new Pcm16AudioPlayer()
    clearSpeaking()
  }, [clearSpeaking, clearSpeakingTimer])

  const handleServerMessage = useCallback(
    (message: VoiceServerMessage) => {
      const callbacks = optionsRef.current

      switch (message.type) {
        case 'session_started':
          setVoiceSessionId(message.voice_session_id)
          callbacks.onSessionStarted?.(message.voice_session_id)
          break
        case 'transcript_partial':
          callbacks.onTranscriptPartial?.(message.text, message.stability)
          break
        case 'transcript_final':
          turnStartedRef.current = false
          // A committed user utterance ends the interrupted turn window; without
          // this, TTS-only or reordered audio_out for the next reply stays muted.
          audioSuppressedRef.current = false
          callbacks.onTranscriptFinal?.(message.text)
          break
        case 'assistant_text_delta':
          if (!turnStartedRef.current) {
            turnStartedRef.current = true
            turnEndedRef.current = false
            audioSuppressedRef.current = false
            lastAudioReceivedAtRef.current = 0
            clearSpeakingTimer()
            callbacks.onStart?.()
          }
          markSpeaking()
          callbacks.onDelta?.(message.text)
          break
        case 'audio_out': {
          const player = playerRef.current
          if (!player || audioSuppressedRef.current) break
          lastAudioReceivedAtRef.current = Date.now()
          markSpeaking()
          void player.playChunk(base64ToBytes(message.payload_b64)).then(
            () => {
              if (turnEndedRef.current) {
                scheduleSpeakingEnd()
              }
            },
            () => {
              if (turnEndedRef.current) {
                scheduleSpeakingEnd()
              }
            },
          )
          break
        }
        case 'tool_start':
          callbacks.onToolStart?.(message.name)
          break
        case 'tool_end':
          callbacks.onToolEnd?.(message.name, message.success)
          break
        case 'turn_complete':
          turnStartedRef.current = false
          turnEndedRef.current = true
          callbacks.onEnd?.({
            tools_used: message.tools_used,
            retrieved_chunk_count: message.retrieved_chunk_count,
            citations: message.citations,
          })
          scheduleSpeakingEnd()
          break
        case 'interrupted':
          turnStartedRef.current = false
          turnEndedRef.current = true
          audioSuppressedRef.current = true
          stopPlayback()
          callbacks.onInterrupted?.()
          break
        case 'session_closed':
          setIsConnected(false)
          turnEndedRef.current = true
          stopPlayback()
          setVoiceSessionId(null)
          callbacks.onClose?.(message.reason)
          break
        case 'error':
          turnStartedRef.current = false
          turnEndedRef.current = true
          clearSpeakingTimer()
          clearSpeaking()
          callbacks.onError?.({ code: message.code, message: message.message })
          break
        default:
          break
      }
    },
    [clearSpeaking, clearSpeakingTimer, markSpeaking, scheduleSpeakingEnd, stopPlayback],
  )

  const prepareMic = useCallback(async () => {
    if (!micRef.current) {
      micRef.current = new MicCapture()
    }
    try {
      await micRef.current.prepare()
    } catch {
      // Permission may be granted on the first push-to-talk press instead.
    }
  }, [])

  const connect = useCallback(async () => {
    const {
      sessionId,
      accessToken,
      useWebSearch,
      useDocuments,
      provider,
      model,
      webSocketFactory,
    } = optionsRef.current

    if (!sessionId) {
      throw new Error('Voice session requires a chat session id')
    }

    if (!clientRef.current) {
      clientRef.current = new VoiceClient(webSocketFactory)
    }
    if (!playerRef.current) {
      playerRef.current = new Pcm16AudioPlayer()
    }

    const connectOptions: VoiceConnectOptions = {
      sessionId,
      accessToken,
      useWebSearch,
      useDocuments,
      provider,
      model,
      onMessage: handleServerMessage,
      onClose: () => {
        setIsConnected(false)
        clearSpeakingTimer()
        clearSpeaking()
      },
      onError: (event) => {
        if (event instanceof Event) {
          optionsRef.current.onError?.(new Error('Voice WebSocket error'))
        }
      },
    }

    await clientRef.current.connect(connectOptions)
    if (!playerRef.current) {
      playerRef.current = new Pcm16AudioPlayer()
    }
    void playerRef.current.prime()
    setIsConnected(true)
    void prepareMic()
  }, [clearSpeaking, clearSpeakingTimer, handleServerMessage, prepareMic])

  const primePlayback = useCallback(async () => {
    if (!playerRef.current) {
      playerRef.current = new Pcm16AudioPlayer()
    }
    await playerRef.current.prime()
  }, [])

  const disconnect = useCallback(() => {
    recordingGenerationRef.current += 1
    micRef.current?.dispose()
    micRef.current = null
    setIsRecording(false)

    clientRef.current?.disconnect()
    clientRef.current = null

    clearSpeakingTimer()
    playerRef.current?.stop()
    playerRef.current = null

    setIsConnected(false)
    clearSpeaking()
    setVoiceSessionId(null)
    turnStartedRef.current = false
    turnEndedRef.current = true
    audioSuppressedRef.current = false
  }, [clearSpeaking, clearSpeakingTimer])

  const startRecording = useCallback(async () => {
    if (!clientRef.current?.isConnected) {
      throw new Error('Connect the voice session before recording')
    }

    if (!micRef.current) {
      micRef.current = new MicCapture()
    }

    micStartInFlightRef.current = true
    pendingStopRef.current = false

    try {
      if (!playerRef.current) {
        playerRef.current = new Pcm16AudioPlayer()
      }
      await playerRef.current.prime()

      const generation = (recordingGenerationRef.current += 1)

      const startPromise = micRef.current.start({
        onChunk: (chunk, final) => {
          clientRef.current?.sendAudioChunk(chunk, final)
          if (final) {
            setIsRecording(false)
          }
        },
        onError: (error) => {
          setIsRecording(false)
          optionsRef.current.onError?.(error)
        },
      })
      micStartPromiseRef.current = startPromise

      await startPromise

      if (pendingStopRef.current || generation !== recordingGenerationRef.current) {
        pendingStopRef.current = false
        micRef.current?.stop(true)
        setIsRecording(false)
        return
      }
      setIsRecording(true)
    } finally {
      micStartInFlightRef.current = false
      micStartPromiseRef.current = null
    }
  }, [])

  const stopRecording = useCallback(() => {
    recordingGenerationRef.current += 1
    if (micStartInFlightRef.current) {
      pendingStopRef.current = true
      setIsRecording(false)
      return
    }
    micRef.current?.stop(true)
    setIsRecording(false)
  }, [])

  const interrupt = useCallback(() => {
    // Audio already in flight would otherwise resume on the fresh player before
    // the server's cancellation lands, so drop frames until the next turn.
    audioSuppressedRef.current = true
    turnEndedRef.current = true
    clientRef.current?.sendInterrupt()
    stopPlayback()
  }, [stopPlayback])

  useEffect(() => {
    return () => {
      disconnect()
    }
  }, [disconnect])

  return {
    connect,
    disconnect,
    prepareMic,
    startRecording,
    stopRecording,
    interrupt,
    primePlayback,
    isConnected,
    isRecording,
    isSpeaking,
    voiceSessionId,
  }
}
