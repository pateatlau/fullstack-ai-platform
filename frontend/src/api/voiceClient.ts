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
 * Lead time before the first scheduled buffer in a playback session absorbs
 * startup jitter without adding a full buffer on every network underrun.
 */
const PLAYBACK_INITIAL_LEAD_SECONDS = 0.12

/** Small lead when the queue drained and new audio arrives late. */
const PLAYBACK_CATCHUP_LEAD_SECONDS = 0.025

/** ~100 ms of PCM16 mono at 24 kHz — merges bursty WS frames without adding latency. */
const PLAYBACK_COALESCE_TARGET_BYTES = 4_800

/** Streams PCM16 chunks through the Web Audio API for assistant playback. */
export class Pcm16AudioPlayer {
  private audioContext: AudioContext | null = null
  private nextStartTime = 0
  private pendingPcm = new Uint8Array(0)
  private drainPromise: Promise<void> = Promise.resolve()
  private readonly sampleRateHz: number

  constructor(sampleRateHz = VOICE_SAMPLE_RATE_HZ) {
    this.sampleRateHz = sampleRateHz
  }

  /** Milliseconds of scheduled audio still to play; `0` once the queue drains. */
  get remainingPlaybackMs(): number {
    const pendingMs = (this.pendingPcm.length / 2 / this.sampleRateHz) * 1000
    if (!this.audioContext) {
      return pendingMs
    }
    const scheduledMs = Math.max(0, (this.nextStartTime - this.audioContext.currentTime) * 1000)
    return scheduledMs + pendingMs
  }

  /** True while PCM is queued but not yet scheduled in the audio context. */
  get hasPendingPlayback(): boolean {
    return this.pendingPcm.length > 0
  }

  async playChunk(pcm16Bytes: Uint8Array): Promise<void> {
    if (pcm16Bytes.length === 0) {
      return
    }

    this.enqueuePcm(pcm16Bytes)
    this.drainPromise = this.drainPromise.then(() => this.drainPending())
    await this.drainPromise
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
    this.pendingPcm = new Uint8Array(0)
    this.drainPromise = Promise.resolve()
    if (this.audioContext) {
      void this.audioContext.close()
      this.audioContext = null
    }
  }

  private enqueuePcm(pcm16Bytes: Uint8Array): void {
    const merged = new Uint8Array(this.pendingPcm.length + pcm16Bytes.length)
    merged.set(this.pendingPcm, 0)
    merged.set(pcm16Bytes, this.pendingPcm.length)
    this.pendingPcm = merged
  }

  /** Drain coalesced PCM through the scheduler one buffer at a time. */
  private async drainPending(): Promise<void> {
    while (this.pendingPcm.length > 0) {
      const context = await this.ensureContext()
      const takeBytes = Math.min(this.pendingPcm.length, PLAYBACK_COALESCE_TARGET_BYTES)
      const chunk = this.pendingPcm.slice(0, takeBytes)
      this.pendingPcm = this.pendingPcm.slice(takeBytes)
      this.scheduleBuffer(context, chunk)
    }
  }

  private scheduleBuffer(context: AudioContext, pcm16Bytes: Uint8Array): void {
    const alignedByteLength = pcm16Bytes.byteLength - (pcm16Bytes.byteLength % 2)
    if (alignedByteLength <= 0) {
      return
    }

    const alignedBytes =
      alignedByteLength === pcm16Bytes.byteLength
        ? pcm16Bytes
        : pcm16Bytes.slice(0, alignedByteLength)

    const samples = pcm16BytesToFloat32(alignedBytes)
    const buffer = context.createBuffer(1, samples.length, this.sampleRateHz)
    // buffer.copyToChannel(samples, 0)
    // Reconstruct the array to force the underlying buffer type to ArrayBuffer
    buffer.copyToChannel(new Float32Array(samples), 0)

    const source = context.createBufferSource()
    source.buffer = buffer
    source.connect(context.destination)

    const queueAheadSeconds = this.nextStartTime - context.currentTime
    const leadSeconds =
      this.nextStartTime === 0
        ? PLAYBACK_INITIAL_LEAD_SECONDS
        : queueAheadSeconds <= 0
          ? PLAYBACK_CATCHUP_LEAD_SECONDS
          : 0

    const startAt = Math.max(context.currentTime + leadSeconds, this.nextStartTime)
    source.start(startAt)
    this.nextStartTime = startAt + buffer.duration
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

interface WorkletCaptureMessage {
  type: 'frame' | 'flush'
  generation: number
  samples: Float32Array
}

/** Captures microphone input, resamples to PCM16 mono, and emits bounded chunks. */
export class MicCapture {
  private mediaStream: MediaStream | null = null
  private audioContext: AudioContext | null = null
  private processor: AudioWorkletNode | null = null
  private source: MediaStreamAudioSourceNode | null = null
  private workletModuleLoaded = false
  private pending = new Uint8Array(0)
  private maxChunkBytes = VOICE_MAX_CHUNK_BYTES
  private targetSampleRateHz = VOICE_SAMPLE_RATE_HZ
  private inputRateHz = VOICE_SAMPLE_RATE_HZ
  private captureGeneration = 0
  private activeCaptureGeneration = 0
  private awaitingFlushGeneration: number | null = null
  private stopFinal = true
  /** The gate: true only between a `start()` and the matching `stop()`. */
  private capturing = false
  private onChunk: ((chunk: Uint8Array, final: boolean) => void) | null = null
  private onError: ((error: Error) => void) | null = null
  /** De-dupes concurrent prepare() calls (e.g. connect()'s warm-up racing a fast first press). */
  private preparePromise: Promise<void> | null = null

  /**
   * Acquire the mic stream AND build/wire the full capture graph so a later
   * `start()` only has to flip a flag. Safe to call early (right after the
   * voice WebSocket connects) and safe to call repeatedly — it's a no-op
   * once everything is already warm.
   */
  async prepare(): Promise<void> {
    if (this.preparePromise) {
      return this.preparePromise
    }
    this.preparePromise = this.doPrepare()
    try {
      await this.preparePromise
    } finally {
      this.preparePromise = null
    }
  }

  private async doPrepare(): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error('Microphone capture is not supported in this browser')
    }

    if (!this.mediaStream?.active) {
      this.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true })
    }

    if (!this.audioContext || this.audioContext.state === 'closed') {
      // Use the device's native rate; mobile browsers often ignore a requested rate.
      this.audioContext = new AudioContext()
    }
    if (this.audioContext.state === 'suspended') {
      await this.audioContext.resume()
    }

    if (this.processor && this.source) {
      // Graph already built and wired up from a previous prepare() — nothing left to do.
      return
    }

    if (!this.workletModuleLoaded) {
      if (!this.audioContext.audioWorklet) {
        throw new Error('AudioWorklet is not supported in this browser')
      }
      const workletUrl = new URL('/audio-worklets/pcm-capture-processor.js', import.meta.url).href
      await this.audioContext.audioWorklet.addModule(workletUrl)
      this.workletModuleLoaded = true
    }

    const frameSize = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent) ? 2048 : 4096

    this.inputRateHz = this.audioContext.sampleRate
    this.source = this.audioContext.createMediaStreamSource(this.mediaStream)
    this.processor = new AudioWorkletNode(this.audioContext, 'pcm-capture-processor', {
      numberOfInputs: 1,
      numberOfOutputs: 0,
      channelCount: 1,
      channelCountMode: 'explicit',
      processorOptions: { frameSize },
    })

    this.processor.port.onmessage = (event: MessageEvent<WorkletCaptureMessage>) => {
      const message = event.data
      if (message.type === 'frame') {
        if (!this.capturing || message.generation !== this.activeCaptureGeneration) {
          return
        }
        this.processWorkletSamples(message.samples)
        return
      }

      if (message.type !== 'flush' || message.generation !== this.awaitingFlushGeneration) {
        return
      }

      if (message.samples.length > 0) {
        this.processWorkletSamples(message.samples)
      }
      this.completeStop(this.stopFinal)
    }

    this.source.connect(this.processor)
    // AudioWorkletNode with numberOfOutputs: 0 does not need a destination
    // connection to keep processing; omitting destination avoids mic echo.
  }

  /**
   * Open the capture gate. Falls back to building the graph now if it isn't
   * warm yet (same cost as the old behavior, but now only a fallback path,
   * not the common one).
   */
  async start(options: MicCaptureOptions): Promise<void> {
    const generation = (this.captureGeneration += 1)
    this.maxChunkBytes = options.maxChunkBytes ?? VOICE_MAX_CHUNK_BYTES
    this.targetSampleRateHz = options.sampleRateHz ?? VOICE_SAMPLE_RATE_HZ
    this.pending = new Uint8Array(0)
    this.onChunk = options.onChunk
    this.onError = options.onError ?? null

    try {
      await this.prepare()
    } catch (cause) {
      const error =
        cause instanceof Error ? cause : new Error('Microphone permission denied or unavailable')
      this.onError?.(error)
      throw error
    }

    // A stop() (or a newer start()) landed while prepare() was resolving.
    if (generation !== this.captureGeneration) {
      return
    }

    this.activeCaptureGeneration = generation
    this.awaitingFlushGeneration = null
    this.processor?.port.postMessage({ type: 'start', generation })
    this.capturing = true
  }

  /** Close the capture gate. The graph itself stays warm and running for the next press. */
  stop(final = true): void {
    const stoppedGeneration = this.activeCaptureGeneration
    this.captureGeneration += 1
    this.capturing = false

    if (this.processor) {
      this.awaitingFlushGeneration = stoppedGeneration
      this.stopFinal = final
      this.processor.port.postMessage({ type: 'stop', generation: stoppedGeneration })
      return
    }

    this.completeStop(final)
  }

  /** Release mic hardware and audio resources when the voice session ends. */
  dispose(): void {
    this.stop(false)
    this.processor?.disconnect()
    this.source?.disconnect()
    this.processor = null
    this.source = null

    for (const track of this.mediaStream?.getTracks() ?? []) {
      track.stop()
    }
    this.mediaStream = null

    if (this.audioContext) {
      void this.audioContext.close()
      this.audioContext = null
      this.workletModuleLoaded = false
    }
  }

  private processWorkletSamples(input: Float32Array): void {
    const resampled =
      this.inputRateHz === this.targetSampleRateHz
        ? input
        : resampleFloat32(input, this.inputRateHz, this.targetSampleRateHz)
    const pcmBytes = float32ToPcm16Bytes(resampled)
    this.enqueueBytes(pcmBytes)
  }

  private completeStop(final: boolean): void {
    this.awaitingFlushGeneration = null

    if (this.pending.length > 0) {
      this.emitChunk(this.pending.slice(), final)
      this.pending = new Uint8Array(0)
    } else if (final) {
      this.emitChunk(new Uint8Array(0), true)
    }

    this.onChunk = null
    this.onError = null
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
