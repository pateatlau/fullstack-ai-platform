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
  const turnStartedRef = useRef(false)
  const turnEndedRef = useRef(true)
  const speakingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
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

  /**
   * Settle `isSpeaking` once the text stream ended *and* queued audio ran out.
   * `turn_complete` mirrors the SSE `end` frame, so it lands while the tail of
   * the reply is still being synthesised and played.
   */
  const scheduleSpeakingEnd = useCallback(() => {
    clearSpeakingTimer()
    if (!turnEndedRef.current) return

    const remainingMs = playerRef.current?.remainingPlaybackMs ?? 0
    if (remainingMs <= 0) {
      setIsSpeaking(false)
      return
    }
    speakingTimerRef.current = setTimeout(() => {
      speakingTimerRef.current = null
      setIsSpeaking(false)
    }, remainingMs)
  }, [clearSpeakingTimer])

  const stopPlayback = useCallback(() => {
    clearSpeakingTimer()
    playerRef.current?.stop()
    playerRef.current = new Pcm16AudioPlayer()
    setIsSpeaking(false)
  }, [clearSpeakingTimer])

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
            clearSpeakingTimer()
            callbacks.onStart?.()
          }
          setIsSpeaking(true)
          callbacks.onDelta?.(message.text)
          break
        case 'audio_out': {
          const player = playerRef.current
          if (!player || audioSuppressedRef.current) break
          // Audio outlives `turn_complete`, so playback re-asserts the speaking
          // state rather than relying on the text stream still being open.
          setIsSpeaking(true)
          void player
            .playChunk(base64ToBytes(message.payload_b64))
            .then(scheduleSpeakingEnd, scheduleSpeakingEnd)
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
          setIsSpeaking(false)
          callbacks.onError?.({ code: message.code, message: message.message })
          break
        default:
          break
      }
    },
    [clearSpeakingTimer, scheduleSpeakingEnd, stopPlayback],
  )

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
        setIsSpeaking(false)
      },
      onError: (event) => {
        if (event instanceof Event) {
          optionsRef.current.onError?.(new Error('Voice WebSocket error'))
        }
      },
    }

    await clientRef.current.connect(connectOptions)
    setIsConnected(true)
  }, [handleServerMessage, clearSpeakingTimer])

  const disconnect = useCallback(() => {
    micRef.current?.stop(false)
    micRef.current = null
    setIsRecording(false)

    clientRef.current?.disconnect()
    clientRef.current = null

    clearSpeakingTimer()
    playerRef.current?.stop()
    playerRef.current = null

    setIsConnected(false)
    setIsSpeaking(false)
    setVoiceSessionId(null)
    turnStartedRef.current = false
    turnEndedRef.current = true
    audioSuppressedRef.current = false
  }, [clearSpeakingTimer])

  const startRecording = useCallback(async () => {
    if (!clientRef.current?.isConnected) {
      throw new Error('Connect the voice session before recording')
    }

    if (!micRef.current) {
      micRef.current = new MicCapture()
    }

    if (!playerRef.current) {
      playerRef.current = new Pcm16AudioPlayer()
    }
    await playerRef.current.prime()

    await micRef.current.start({
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
    setIsRecording(true)
  }, [])

  const stopRecording = useCallback(() => {
    micRef.current?.stop(true)
    micRef.current = null
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
    startRecording,
    stopRecording,
    interrupt,
    isConnected,
    isRecording,
    isSpeaking,
    voiceSessionId,
  }
}
