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
  const optionsRef = useRef(options)

  useEffect(() => {
    optionsRef.current = options
  }, [options])

  const handleServerMessage = useCallback((message: VoiceServerMessage) => {
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
        callbacks.onTranscriptFinal?.(message.text)
        break
      case 'assistant_text_delta':
        if (!turnStartedRef.current) {
          turnStartedRef.current = true
          callbacks.onStart?.()
        }
        setIsSpeaking(true)
        callbacks.onDelta?.(message.text)
        break
      case 'audio_out':
        void playerRef.current?.playChunk(base64ToBytes(message.payload_b64))
        break
      case 'tool_start':
        callbacks.onToolStart?.(message.name)
        break
      case 'tool_end':
        callbacks.onToolEnd?.(message.name, message.success)
        break
      case 'turn_complete':
        turnStartedRef.current = false
        setIsSpeaking(false)
        callbacks.onEnd?.({
          tools_used: message.tools_used,
          retrieved_chunk_count: message.retrieved_chunk_count,
          citations: message.citations,
        })
        break
      case 'interrupted':
        turnStartedRef.current = false
        setIsSpeaking(false)
        playerRef.current?.stop()
        playerRef.current = new Pcm16AudioPlayer()
        callbacks.onInterrupted?.()
        break
      case 'session_closed':
        setIsConnected(false)
        setIsSpeaking(false)
        setVoiceSessionId(null)
        callbacks.onClose?.(message.reason)
        break
      case 'error':
        callbacks.onError?.({ code: message.code, message: message.message })
        break
      default:
        break
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
  }, [handleServerMessage])

  const disconnect = useCallback(() => {
    micRef.current?.stop(false)
    micRef.current = null
    setIsRecording(false)

    clientRef.current?.disconnect()
    clientRef.current = null

    playerRef.current?.stop()
    playerRef.current = null

    setIsConnected(false)
    setIsSpeaking(false)
    setVoiceSessionId(null)
    turnStartedRef.current = false
  }, [])

  const startRecording = useCallback(async () => {
    if (!clientRef.current?.isConnected) {
      throw new Error('Connect the voice session before recording')
    }

    if (!micRef.current) {
      micRef.current = new MicCapture()
    }

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
    clientRef.current?.sendInterrupt()
    playerRef.current?.stop()
    playerRef.current = new Pcm16AudioPlayer()
    setIsSpeaking(false)
  }, [])

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
