import { getStoredAccessToken } from '../auth/tokenStorage'
import type { VoiceClientMessage, VoiceServerMessage, VoiceWsMessage } from '../types/voice'
import {
  VOICE_HEARTBEAT_INTERVAL_MS,
  VOICE_MAX_CHUNK_BYTES,
  VOICE_SAMPLE_RATE_HZ,
  isVoiceServerMessage,
} from '../types/voice'
import { API_BASE_URL } from './request'

export type WebSocketFactory = (url: string, protocols?: string | string[]) => WebSocket

export interface VoiceConnectOptions {
  sessionId: string
  accessToken?: string | null
  useWebSearch?: boolean
  useDocuments?: boolean
  provider?: string
  model?: string
  heartbeatIntervalMs?: number
  onMessage?: (message: VoiceServerMessage) => void
  onOpen?: () => void
  onClose?: (event: CloseEvent) => void
  onError?: (event: Event) => void
}

function resolveWebSocketOrigin(): string {
  const configured = API_BASE_URL.trim()
  if (configured) {
    const url = new URL(configured)
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
    return url.origin
  }

  if (typeof window !== 'undefined') {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${protocol}//${window.location.host}`
  }

  return 'ws://localhost'
}

/** Builds the voice WebSocket URL including chat session and optional turn toggles. */
export function buildVoiceWebSocketUrl(options: VoiceConnectOptions): string {
  const params = new URLSearchParams({ session_id: options.sessionId })

  if (options.useWebSearch) {
    params.set('use_web_search', 'true')
  }
  if (options.useDocuments) {
    params.set('use_documents', 'true')
  }
  if (options.provider) {
    params.set('provider', options.provider)
  }
  if (options.model) {
    params.set('model', options.model)
  }

  const token = options.accessToken ?? getStoredAccessToken()
  if (token) {
    // Browser WebSocket cannot set Authorization headers; pass token for same-origin proxy
    // or server-side query-param auth (see backend voice router).
    params.set('access_token', token)
  }

  return `${resolveWebSocketOrigin()}/api/voice/ws?${params.toString()}`
}

/** Serializes an outbound voice frame. */
export function encodeVoiceMessage(message: VoiceClientMessage): string {
  return JSON.stringify(message)
}

/** Parses an inbound voice frame; throws on invalid JSON or missing type. */
export function decodeVoiceMessage(raw: string): VoiceWsMessage {
  const parsed: unknown = JSON.parse(raw)
  if (
    typeof parsed !== 'object' ||
    parsed === null ||
    !('type' in parsed) ||
    typeof (parsed as { type: unknown }).type !== 'string'
  ) {
    throw new Error('Invalid voice WebSocket message: missing type')
  }
  return parsed as VoiceWsMessage
}

export function bytesToBase64(bytes: Uint8Array): string {
  let binary = ''
  for (const byte of bytes) {
    binary += String.fromCharCode(byte)
  }
  return btoa(binary)
}

export function base64ToBytes(base64: string): Uint8Array {
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index)
  }
  return bytes
}

/** Converts signed PCM16 little-endian bytes to normalized float samples. */
export function pcm16BytesToFloat32(pcm16: Uint8Array): Float32Array {
  const sampleCount = Math.floor(pcm16.byteLength / 2)
  const view = new DataView(pcm16.buffer, pcm16.byteOffset, pcm16.byteLength)
  const samples = new Float32Array(sampleCount)
  for (let index = 0; index < sampleCount; index += 1) {
    const int16 = view.getInt16(index * 2, true)
    samples[index] = int16 / 32768
  }
  return samples
}

/** Converts normalized float samples to signed PCM16 little-endian bytes. */
export function float32ToPcm16Bytes(samples: Float32Array): Uint8Array {
  const bytes = new Uint8Array(samples.length * 2)
  const view = new DataView(bytes.buffer)
  for (let index = 0; index < samples.length; index += 1) {
    const clamped = Math.max(-1, Math.min(1, samples[index] ?? 0))
    view.setInt16(index * 2, clamped < 0 ? clamped * 32768 : clamped * 32767, true)
  }
  return bytes
}

/** Linearly resample mono float samples to the voice wire-format sample rate. */
export function resampleFloat32(
  input: Float32Array,
  inputRateHz: number,
  outputRateHz: number,
): Float32Array {
  if (inputRateHz === outputRateHz || input.length === 0) {
    return input
  }

  const ratio = inputRateHz / outputRateHz
  const outputLength = Math.max(1, Math.round(input.length / ratio))
  const output = new Float32Array(outputLength)

  for (let index = 0; index < outputLength; index += 1) {
    const srcIndex = index * ratio
    const lowerIndex = Math.floor(srcIndex)
    const fraction = srcIndex - lowerIndex
    const sample0 = input[lowerIndex] ?? 0
    const sample1 = input[Math.min(lowerIndex + 1, input.length - 1)] ?? sample0
    output[index] = sample0 + fraction * (sample1 - sample0)
  }

  return output
}

/** Round-trips PCM16 bytes through the voice base64 codec. */
export function pcm16RoundTrip(base64Payload: string): Uint8Array {
  return base64ToBytes(base64Payload)
}

const defaultWebSocketFactory: WebSocketFactory = (url, protocols) => new WebSocket(url, protocols)

/** Bidirectional voice WebSocket client with heartbeat and outbound audio sequencing. */
export class VoiceClient {
  private ws: WebSocket | null = null
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null
  private audioSeq = 0
  private readonly webSocketFactory: WebSocketFactory

  constructor(webSocketFactory: WebSocketFactory = defaultWebSocketFactory) {
    this.webSocketFactory = webSocketFactory
  }

  get isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN
  }

  connect(options: VoiceConnectOptions): Promise<void> {
    if (this.ws) {
      this.disconnect()
    }

    this.audioSeq = 0

    const url = buildVoiceWebSocketUrl(options)
    const socket = this.webSocketFactory(url)
    this.ws = socket

    return new Promise((resolve, reject) => {
      let settled = false

      const settleResolve = () => {
        if (!settled) {
          settled = true
          resolve()
        }
      }

      const settleReject = (error: Error) => {
        if (!settled) {
          settled = true
          reject(error)
        }
      }

      socket.onopen = () => {
        this.startHeartbeat(options.heartbeatIntervalMs ?? VOICE_HEARTBEAT_INTERVAL_MS)
        options.onOpen?.()
        settleResolve()
      }

      socket.onmessage = (event) => {
        try {
          const message = decodeVoiceMessage(String(event.data))
          if (isVoiceServerMessage(message)) {
            options.onMessage?.(message)
          }
        } catch {
          options.onError?.(new Event('voice_message_parse_error'))
        }
      }

      socket.onerror = (event) => {
        options.onError?.(event)
        settleReject(new Error('Voice WebSocket connection failed'))
      }

      socket.onclose = (event) => {
        this.clearHeartbeat()
        options.onClose?.(event)
        if (!settled) {
          settleReject(new Error(`Voice WebSocket closed before open: ${event.code}`))
        }
        this.ws = null
      }
    })
  }

  disconnect(): void {
    this.clearHeartbeat()
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      try {
        this.sendSessionEnd()
      } catch {
        // Socket may already be closing.
      }
      this.ws.close()
    }
    this.ws = null
  }

  sendAudioChunk(pcmBytes: Uint8Array, final = false): void {
    this.send({
      type: 'audio_in',
      seq: this.audioSeq,
      payload_b64: bytesToBase64(pcmBytes),
      final,
    })
    this.audioSeq += 1
  }

  sendInterrupt(): void {
    this.send({ type: 'interrupt' })
  }

  sendSessionEnd(): void {
    this.send({ type: 'session_end' })
  }

  private send(message: VoiceClientMessage): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new Error('Voice WebSocket is not connected')
    }
    this.ws.send(encodeVoiceMessage(message))
  }

  private startHeartbeat(intervalMs: number): void {
    this.clearHeartbeat()
    this.heartbeatTimer = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.send({ type: 'heartbeat', ts: Date.now() / 1000 })
      }
    }, intervalMs)
  }

  private clearHeartbeat(): void {
    if (this.heartbeatTimer !== null) {
      clearInterval(this.heartbeatTimer)
      this.heartbeatTimer = null
    }
  }
}

/**
 * Lead time applied when the playback queue has drained. Assistant audio now
 * arrives as small network-paced frames, so scheduling the first one slightly
 * ahead absorbs jitter that would otherwise be audible as clipped syllables.
 */
const PLAYBACK_JITTER_BUFFER_SECONDS = 0.15

/** Streams PCM16 chunks through the Web Audio API for assistant playback. */
export class Pcm16AudioPlayer {
  private audioContext: AudioContext | null = null
  private nextStartTime = 0
  private readonly sampleRateHz: number

  constructor(sampleRateHz = VOICE_SAMPLE_RATE_HZ) {
    this.sampleRateHz = sampleRateHz
  }

  /** Milliseconds of scheduled audio still to play; `0` once the queue drains. */
  get remainingPlaybackMs(): number {
    if (!this.audioContext) {
      return 0
    }
    return Math.max(0, (this.nextStartTime - this.audioContext.currentTime) * 1000)
  }

  async playChunk(pcm16Bytes: Uint8Array): Promise<void> {
    const context = await this.ensureContext()
    const samples = pcm16BytesToFloat32(pcm16Bytes)
    const buffer = context.createBuffer(1, samples.length, this.sampleRateHz)
    buffer.copyToChannel(Float32Array.from(samples), 0)

    const source = context.createBufferSource()
    source.buffer = buffer
    source.connect(context.destination)

    const startAt = Math.max(
      context.currentTime + PLAYBACK_JITTER_BUFFER_SECONDS,
      this.nextStartTime,
    )
    source.start(startAt)
    this.nextStartTime = startAt + buffer.duration
  }

  /** Resume AudioContext during a user gesture so later TTS chunks can play. */
  async prime(): Promise<void> {
    const context = await this.ensureContext()
    if (context.state === 'suspended') {
      await context.resume()
    }
  }

  stop(): void {
    this.nextStartTime = 0
    if (this.audioContext) {
      void this.audioContext.close()
      this.audioContext = null
    }
  }

  private async ensureContext(): Promise<AudioContext> {
    if (!this.audioContext) {
      this.audioContext = new AudioContext({ sampleRate: this.sampleRateHz })
    }
    if (this.audioContext.state === 'suspended') {
      await this.audioContext.resume()
    }
    return this.audioContext
  }
}

export interface MicCaptureOptions {
  onChunk: (chunk: Uint8Array, final: boolean) => void
  onError?: (error: Error) => void
  maxChunkBytes?: number
  sampleRateHz?: number
}

/** Captures microphone input, resamples to PCM16 mono, and emits bounded chunks. */
export class MicCapture {
  private mediaStream: MediaStream | null = null
  private audioContext: AudioContext | null = null
  private processor: ScriptProcessorNode | null = null
  private source: MediaStreamAudioSourceNode | null = null
  private pending = new Uint8Array(0)
  private maxChunkBytes = VOICE_MAX_CHUNK_BYTES
  private targetSampleRateHz = VOICE_SAMPLE_RATE_HZ
  private captureGeneration = 0
  private stopped = true
  private onChunk: ((chunk: Uint8Array, final: boolean) => void) | null = null

  /** Acquire and keep the mic stream warm so push-to-talk starts immediately on mobile. */
  async prepare(): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error('Microphone capture is not supported in this browser')
    }
    if (this.mediaStream?.active) {
      return
    }
    this.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true })
  }

  async start(options: MicCaptureOptions): Promise<void> {
    const generation = (this.captureGeneration += 1)
    this.stopped = false
    this.maxChunkBytes = options.maxChunkBytes ?? VOICE_MAX_CHUNK_BYTES
    this.targetSampleRateHz = options.sampleRateHz ?? VOICE_SAMPLE_RATE_HZ
    this.pending = new Uint8Array(0)
    this.onChunk = options.onChunk

    if (!navigator.mediaDevices?.getUserMedia) {
      const error = new Error('Microphone capture is not supported in this browser')
      options.onError?.(error)
      throw error
    }

    try {
      await this.prepare()
    } catch (cause) {
      const error =
        cause instanceof Error ? cause : new Error('Microphone permission denied or unavailable')
      options.onError?.(error)
      throw error
    }

    if (this.stopped || generation !== this.captureGeneration) {
      return
    }

    if (!this.audioContext || this.audioContext.state === 'closed') {
      // Use the device native rate; mobile browsers often ignore a requested rate.
      this.audioContext = new AudioContext()
    }
    if (this.audioContext.state === 'suspended') {
      await this.audioContext.resume()
    }

    if (this.stopped || generation !== this.captureGeneration) {
      return
    }

    this.teardownCaptureGraph()

    const inputRateHz = this.audioContext.sampleRate
    this.source = this.audioContext.createMediaStreamSource(this.mediaStream!)
    this.processor = this.audioContext.createScriptProcessor(4096, 1, 1)

    this.processor.onaudioprocess = (event) => {
      if (this.stopped || generation !== this.captureGeneration) {
        return
      }
      const input = event.inputBuffer.getChannelData(0)
      const resampled =
        inputRateHz === this.targetSampleRateHz
          ? input
          : resampleFloat32(input, inputRateHz, this.targetSampleRateHz)
      const pcmBytes = float32ToPcm16Bytes(resampled)
      this.enqueueBytes(pcmBytes)
    }

    this.source.connect(this.processor)
    this.processor.connect(this.audioContext.destination)
  }

  stop(final = true): void {
    this.captureGeneration += 1
    this.stopped = true

    if (this.pending.length > 0) {
      this.emitChunk(this.pending.slice(), final)
      this.pending = new Uint8Array(0)
    } else if (final) {
      this.emitChunk(new Uint8Array(0), true)
    }

    this.teardownCaptureGraph()
    this.onChunk = null
  }

  /** Release mic hardware and audio resources when the voice session ends. */
  dispose(): void {
    this.stop(false)
    for (const track of this.mediaStream?.getTracks() ?? []) {
      track.stop()
    }
    this.mediaStream = null

    if (this.audioContext) {
      void this.audioContext.close()
      this.audioContext = null
    }
  }

  private teardownCaptureGraph(): void {
    this.processor?.disconnect()
    this.source?.disconnect()
    this.processor = null
    this.source = null
  }

  private enqueueBytes(bytes: Uint8Array): void {
    if (bytes.length === 0) {
      return
    }

    const merged = new Uint8Array(this.pending.length + bytes.length)
    merged.set(this.pending, 0)
    merged.set(bytes, this.pending.length)
    this.pending = merged

    while (this.pending.length >= this.maxChunkBytes) {
      const chunk = this.pending.slice(0, this.maxChunkBytes)
      this.pending = this.pending.slice(this.maxChunkBytes)
      this.emitChunk(chunk, false)
    }
  }

  private emitChunk(chunk: Uint8Array, final: boolean): void {
    this.onChunk?.(chunk, final)
  }
}
