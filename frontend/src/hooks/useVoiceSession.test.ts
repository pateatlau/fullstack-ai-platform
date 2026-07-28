/* @vitest-environment jsdom */

import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useVoiceSession } from './useVoiceSession'

type MockWebSocketListener = ((event: Event | MessageEvent | CloseEvent) => void) | null

class MockWebSocket {
  static instances: MockWebSocket[] = []

  readonly url: string
  readyState: number = WebSocket.CONNECTING
  onopen: MockWebSocketListener = null
  onmessage: MockWebSocketListener = null
  onerror: MockWebSocketListener = null
  onclose: MockWebSocketListener = null
  sent: string[] = []

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
    queueMicrotask(() => {
      this.readyState = WebSocket.OPEN
      this.onopen?.(new Event('open'))
    })
  }

  send(data: string): void {
    this.sent.push(data)
  }

  close(code = 1000, reason = ''): void {
    this.readyState = WebSocket.CLOSED
    this.onclose?.(new CloseEvent('close', { code, reason }))
  }

  emitMessage(data: string): void {
    this.onmessage?.(new MessageEvent('message', { data }))
  }
}

describe('useVoiceSession', () => {
  beforeEach(() => {
    MockWebSocket.instances = []
    vi.stubGlobal('WebSocket', MockWebSocket as unknown as typeof WebSocket)

    class MockAudioContext {
      state = 'running'
      currentTime = 0
      destination = {}
      createBuffer = vi.fn(() => ({ duration: 0.1, copyToChannel: vi.fn() }))
      createBufferSource = vi.fn(() => ({
        buffer: null,
        connect: vi.fn(),
        start: vi.fn(),
      }))
      resume = vi.fn().mockResolvedValue(undefined)
      close = vi.fn()
    }

    vi.stubGlobal('AudioContext', MockAudioContext)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('connects and maps assistant deltas to onStart/onDelta/onEnd callbacks', async () => {
    const onSessionStarted = vi.fn()
    const onStart = vi.fn()
    const onDelta = vi.fn()
    const onEnd = vi.fn()

    const { result } = renderHook(() =>
      useVoiceSession({
        sessionId: 'chat-1',
        accessToken: 'jwt',
        onSessionStarted,
        onStart,
        onDelta,
        onEnd,
        webSocketFactory: (url) => new MockWebSocket(url) as unknown as WebSocket,
      }),
    )

    await result.current.connect()

    await waitFor(() => {
      expect(result.current.isConnected).toBe(true)
    })

    const socket = MockWebSocket.instances[0]
    socket?.emitMessage(
      JSON.stringify({
        type: 'session_started',
        voice_session_id: 'voice-1',
        audio_format: 'pcm16_24k_mono',
      }),
    )
    expect(onSessionStarted).toHaveBeenCalledWith('voice-1')

    socket?.emitMessage(JSON.stringify({ type: 'assistant_text_delta', text: 'Hello' }))
    socket?.emitMessage(JSON.stringify({ type: 'assistant_text_delta', text: ' world' }))
    socket?.emitMessage(
      JSON.stringify({
        type: 'turn_complete',
        tools_used: ['web_search'],
        retrieved_chunk_count: 2,
      }),
    )

    await waitFor(() => {
      expect(onStart).toHaveBeenCalledTimes(1)
      expect(onDelta).toHaveBeenCalledWith('Hello')
      expect(onDelta).toHaveBeenCalledWith(' world')
      expect(onEnd).toHaveBeenCalledWith(
        expect.objectContaining({ tools_used: ['web_search'], retrieved_chunk_count: 2 }),
      )
      expect(result.current.isSpeaking).toBe(false)
    })
  })

  it('sends interrupt and forwards transcript events', async () => {
    const onTranscriptPartial = vi.fn()
    const onTranscriptFinal = vi.fn()
    const onInterrupted = vi.fn()

    const { result } = renderHook(() =>
      useVoiceSession({
        sessionId: 'chat-1',
        accessToken: 'jwt',
        onTranscriptPartial,
        onTranscriptFinal,
        onInterrupted,
        webSocketFactory: (url) => new MockWebSocket(url) as unknown as WebSocket,
      }),
    )

    await result.current.connect()
    const socket = MockWebSocket.instances[0]

    socket?.emitMessage(JSON.stringify({ type: 'transcript_partial', text: 'hel', stability: 0.5 }))
    socket?.emitMessage(JSON.stringify({ type: 'transcript_final', text: 'hello' }))
    expect(onTranscriptPartial).toHaveBeenCalledWith('hel', 0.5)
    expect(onTranscriptFinal).toHaveBeenCalledWith('hello')

    result.current.interrupt()
    expect(JSON.parse(socket?.sent.at(-1) ?? '{}')).toEqual({ type: 'interrupt' })

    socket?.emitMessage(JSON.stringify({ type: 'interrupted' }))
    await waitFor(() => {
      expect(onInterrupted).toHaveBeenCalled()
    })
  })
})
