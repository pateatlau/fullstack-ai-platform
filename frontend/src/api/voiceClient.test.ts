/* @vitest-environment jsdom */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  MicCapture,
  Pcm16AudioPlayer,
  VoiceClient,
  base64ToBytes,
  buildVoiceWebSocketUrl,
  bytesToBase64,
  decodeVoiceMessage,
  encodeVoiceMessage,
  float32ToPcm16Bytes,
  pcm16BytesToFloat32,
  resampleFloat32,
} from './voiceClient'
import { storeSession } from '../auth/tokenStorage'
import type { AuthenticatedUser } from '../types/auth'

const user: AuthenticatedUser = {
  id: 'user-1',
  email: 'person@example.com',
  display_name: 'Person',
  picture_url: null,
}

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

describe('voiceClient codec', () => {
  it('round-trips client audio_in messages', () => {
    const payload = bytesToBase64(new Uint8Array([0, 1, 2, 3]))
    const encoded = encodeVoiceMessage({
      type: 'audio_in',
      seq: 0,
      payload_b64: payload,
      final: false,
    })
    const decoded = decodeVoiceMessage(encoded)
    expect(decoded).toEqual({
      type: 'audio_in',
      seq: 0,
      payload_b64: payload,
      final: false,
    })
  })

  it('round-trips PCM16 bytes through base64 helpers', () => {
    const pcm = float32ToPcm16Bytes(new Float32Array([0, 0.5, -0.5]))
    const restored = pcm16BytesToFloat32(base64ToBytes(bytesToBase64(pcm)))
    expect(restored.length).toBe(3)
    expect(restored[1]).toBeCloseTo(0.5, 2)
    expect(restored[2]).toBeCloseTo(-0.5, 2)
  })

  it('downsamples float audio to the voice wire-format sample rate', () => {
    const input = new Float32Array([1, 0, -1, 0])
    const resampled = resampleFloat32(input, 48_000, 24_000)
    expect(resampled.length).toBe(2)
    expect(resampled[0]).toBeCloseTo(1, 5)
    expect(resampled[1]).toBeCloseTo(-1, 5)
  })

  it('buildVoiceWebSocketUrl includes session id and bearer token query param', () => {
    vi.stubEnv('VITE_API_BASE_URL', '')
    storeSession('voice-jwt', user)

    const url = buildVoiceWebSocketUrl({ sessionId: 'chat-123', useWebSearch: true })
    const parsed = new URL(url.replace(/^ws:/, 'http:'))

    expect(parsed.pathname).toBe('/api/voice/ws')
    expect(parsed.searchParams.get('session_id')).toBe('chat-123')
    expect(parsed.searchParams.get('use_web_search')).toBe('true')
    expect(parsed.searchParams.get('access_token')).toBe('voice-jwt')
  })
})

describe('VoiceClient', () => {
  beforeEach(() => {
    MockWebSocket.instances = []
    vi.stubGlobal('WebSocket', MockWebSocket as unknown as typeof WebSocket)
  })

  afterEach(() => {
    vi.restoreAllMocks()
    window.localStorage.clear()
  })

  it('connects, sends heartbeat, and parses inbound server frames', async () => {
    vi.useFakeTimers()
    const onMessage = vi.fn()

    const client = new VoiceClient((url) => new MockWebSocket(url) as unknown as WebSocket)
    await client.connect({
      sessionId: 'chat-1',
      accessToken: 'jwt',
      heartbeatIntervalMs: 1000,
      onMessage,
    })

    expect(client.isConnected).toBe(true)
    expect(MockWebSocket.instances[0]?.url).toContain('session_id=chat-1')

    vi.advanceTimersByTime(1000)
    const heartbeat = JSON.parse(MockWebSocket.instances[0]?.sent[0] ?? '{}') as {
      type: string
    }
    expect(heartbeat.type).toBe('heartbeat')

    MockWebSocket.instances[0]?.emitMessage(
      JSON.stringify({
        type: 'session_started',
        voice_session_id: 'voice-1',
        audio_format: 'pcm16_24k_mono',
      }),
    )
    expect(onMessage).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'session_started', voice_session_id: 'voice-1' }),
    )

    client.sendInterrupt()
    expect(JSON.parse(MockWebSocket.instances[0]?.sent.at(-1) ?? '{}')).toEqual({
      type: 'interrupt',
    })

    client.disconnect()
    vi.useRealTimers()
  })
})

describe('Pcm16AudioPlayer', () => {
  it('schedules audio buffer playback through AudioContext', async () => {
    const start = vi.fn()
    const createBuffer = vi.fn((_channels: number, length: number, sampleRate: number) => ({
      duration: length / sampleRate,
      copyToChannel: vi.fn(),
    }))
    const createBufferSource = vi.fn(() => ({ buffer: null, connect: vi.fn(), start }))
    const resume = vi.fn().mockResolvedValue(undefined)

    class MockAudioContext {
      state = 'running'
      currentTime = 0
      destination = {}
      createBuffer = createBuffer
      createBufferSource = createBufferSource
      resume = resume
      close = vi.fn()
    }

    vi.stubGlobal('AudioContext', MockAudioContext)

    const player = new Pcm16AudioPlayer()
    await player.playChunk(float32ToPcm16Bytes(new Float32Array([0.1, -0.1])))

    expect(createBuffer).toHaveBeenCalled()
    expect(start).toHaveBeenCalled()
    player.stop()
  })

  it('schedules consecutive chunks gaplessly without re-applying the initial lead', async () => {
    const startAtTimes: number[] = []
    const createBuffer = vi.fn((_channels: number, length: number, sampleRate: number) => ({
      duration: length / sampleRate,
      copyToChannel: vi.fn(),
    }))
    const createBufferSource = vi.fn(() => ({
      buffer: null,
      connect: vi.fn(),
      start: (when: number) => {
        startAtTimes.push(when)
      },
    }))
    const resume = vi.fn().mockResolvedValue(undefined)

    class MockAudioContext {
      state = 'running'
      currentTime = 0
      destination = {}
      createBuffer = createBuffer
      createBufferSource = createBufferSource
      resume = resume
      close = vi.fn()
    }

    vi.stubGlobal('AudioContext', MockAudioContext)

    const player = new Pcm16AudioPlayer()
    const frame = float32ToPcm16Bytes(new Float32Array(2400).fill(0.1))

    await player.playChunk(frame)
    await player.playChunk(frame)

    expect(startAtTimes).toHaveLength(2)
    expect(startAtTimes[0]).toBeCloseTo(0.12, 3)
    expect(startAtTimes[1]).toBeCloseTo(startAtTimes[0]! + 2400 / 24_000, 3)
    player.stop()
  })

  it('uses a short catch-up lead after the queue underruns', async () => {
    const startAtTimes: number[] = []
    let currentTime = 0
    const createBuffer = vi.fn((_channels: number, length: number, sampleRate: number) => ({
      duration: length / sampleRate,
      copyToChannel: vi.fn(),
    }))
    const createBufferSource = vi.fn(() => ({
      buffer: null,
      connect: vi.fn(),
      start: (when: number) => {
        startAtTimes.push(when)
      },
    }))
    const resume = vi.fn().mockResolvedValue(undefined)

    class MockAudioContext {
      state = 'running'
      get currentTime() {
        return currentTime
      }
      destination = {}
      createBuffer = createBuffer
      createBufferSource = createBufferSource
      resume = resume
      close = vi.fn()
    }

    vi.stubGlobal('AudioContext', MockAudioContext)

    const player = new Pcm16AudioPlayer()
    const shortFrame = float32ToPcm16Bytes(new Float32Array(240).fill(0.1))

    await player.playChunk(shortFrame)
    currentTime = 1
    await player.playChunk(shortFrame)

    expect(startAtTimes).toHaveLength(2)
    expect(startAtTimes[1]).toBeCloseTo(1.025, 3)
    player.stop()
  })

  it('coalesces bursty small frames before scheduling', async () => {
    const scheduledSampleCounts: number[] = []
    const createBuffer = vi.fn((channels: number, length: number, sampleRate: number) => {
      scheduledSampleCounts.push(length)
      return {
        duration: length / sampleRate,
        copyToChannel: vi.fn(),
      }
    })
    const createBufferSource = vi.fn(() => ({
      buffer: null,
      connect: vi.fn(),
      start: vi.fn(),
    }))
    const resume = vi.fn().mockResolvedValue(undefined)

    class MockAudioContext {
      state = 'running'
      currentTime = 0
      destination = {}
      createBuffer = createBuffer
      createBufferSource = createBufferSource
      resume = resume
      close = vi.fn()
    }

    vi.stubGlobal('AudioContext', MockAudioContext)

    const player = new Pcm16AudioPlayer()
    const smallFrame = float32ToPcm16Bytes(new Float32Array(600).fill(0.1))

    await Promise.all([player.playChunk(smallFrame), player.playChunk(smallFrame)])

    expect(scheduledSampleCounts).toEqual([1200])
    player.stop()
  })
})

describe('MicCapture', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('reports permission errors from getUserMedia', async () => {
    const onError = vi.fn()
    vi.stubGlobal('navigator', {
      mediaDevices: {
        getUserMedia: vi.fn().mockRejectedValue(new DOMException('denied', 'NotAllowedError')),
      },
    })

    const mic = new MicCapture()
    await expect(
      mic.start({
        onChunk: vi.fn(),
        onError,
      }),
    ).rejects.toThrow()

    expect(onError).toHaveBeenCalled()
  })

  it('chunks PCM16 bytes to the configured max size', async () => {
    const onChunk = vi.fn()
    const processorListeners: Array<(event: AudioProcessingEvent) => void> = []

    vi.stubGlobal('navigator', {
      mediaDevices: {
        getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: vi.fn() }] }),
      },
    })

    class MockAudioContext {
      sampleRate = 48_000
      state = 'running'
      destination = {}
      resume = vi.fn().mockResolvedValue(undefined)
      close = vi.fn()
      createMediaStreamSource = vi.fn(() => ({ connect: vi.fn(), disconnect: vi.fn() }))
      createScriptProcessor = vi.fn(() => ({
        connect: vi.fn(),
        disconnect: vi.fn(),
        set onaudioprocess(listener: (event: AudioProcessingEvent) => void) {
          processorListeners.push(listener)
        },
      }))
    }

    vi.stubGlobal('AudioContext', MockAudioContext)

    const mic = new MicCapture()
    await mic.start({ onChunk, maxChunkBytes: 4 })

    const samples = new Float32Array(4)
    processorListeners[0]?.({
      inputBuffer: { getChannelData: () => samples },
    } as unknown as AudioProcessingEvent)

    expect(onChunk).toHaveBeenCalledWith(expect.any(Uint8Array), false)
    expect(onChunk.mock.calls[0]?.[0]?.length).toBe(4)

    mic.stop(true)
    expect(onChunk.mock.calls.some((call) => call[1] === true)).toBe(true)
  })

  it('aborts start when stop is called while getUserMedia is pending', async () => {
    const onChunk = vi.fn()
    let resolveMedia: ((stream: MediaStream) => void) | null = null
    const createScriptProcessor = vi.fn(() => ({
      connect: vi.fn(),
      disconnect: vi.fn(),
      onaudioprocess: null,
    }))

    vi.stubGlobal('navigator', {
      mediaDevices: {
        getUserMedia: vi.fn(
          () =>
            new Promise<MediaStream>((resolve) => {
              resolveMedia = resolve
            }),
        ),
      },
    })

    class MockAudioContext {
      sampleRate = 48_000
      state = 'running'
      destination = {}
      resume = vi.fn().mockResolvedValue(undefined)
      close = vi.fn()
      createMediaStreamSource = vi.fn(() => ({ connect: vi.fn(), disconnect: vi.fn() }))
      createScriptProcessor = createScriptProcessor
    }

    vi.stubGlobal('AudioContext', MockAudioContext)

    const mic = new MicCapture()
    const startPromise = mic.start({ onChunk })

    mic.stop(false)
    resolveMedia?.({ active: true, getTracks: () => [{ stop: vi.fn() }] } as unknown as MediaStream)
    await startPromise

    expect(createScriptProcessor).not.toHaveBeenCalled()
    expect(onChunk).not.toHaveBeenCalled()
  })

  it('reuses the warmed media stream across utterances', async () => {
    const getUserMedia = vi.fn().mockResolvedValue({
      active: true,
      getTracks: () => [{ stop: vi.fn() }],
    })

    vi.stubGlobal('navigator', {
      mediaDevices: { getUserMedia },
    })

    class MockAudioContext {
      sampleRate = 48_000
      state = 'running'
      destination = {}
      resume = vi.fn().mockResolvedValue(undefined)
      close = vi.fn()
      createMediaStreamSource = vi.fn(() => ({ connect: vi.fn(), disconnect: vi.fn() }))
      createScriptProcessor = vi.fn(() => ({
        connect: vi.fn(),
        disconnect: vi.fn(),
        onaudioprocess: null,
      }))
    }

    vi.stubGlobal('AudioContext', MockAudioContext)

    const mic = new MicCapture()
    await mic.prepare()
    await mic.start({ onChunk: vi.fn() })
    mic.stop(true)
    await mic.start({ onChunk: vi.fn() })

    expect(getUserMedia).toHaveBeenCalledTimes(1)
    mic.dispose()
  })
})
