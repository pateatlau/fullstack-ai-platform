import type { Citation } from './citation'

/** Voice WebSocket protocol types (mirrors backend ``app/schemas/voice.py``). */

/** Client → server messages. */
export type VoiceClientMessage =
  | { type: 'audio_in'; seq: number; payload_b64: string; final: boolean }
  | { type: 'interrupt' }
  | { type: 'session_end' }
  | { type: 'heartbeat'; ts: number }

/** Server → client messages. */
export type VoiceServerMessage =
  | { type: 'session_started'; voice_session_id: string; audio_format: string }
  | { type: 'transcript_partial'; text: string; stability?: number | null }
  | { type: 'transcript_final'; text: string }
  | { type: 'assistant_text_delta'; text: string }
  | { type: 'audio_out'; seq: number; payload_b64: string }
  | { type: 'tool_start'; name: string }
  | { type: 'tool_end'; name: string; success: boolean }
  | { type: 'interrupted' }
  | {
      type: 'turn_complete'
      tools_used?: string[] | null
      retrieved_chunk_count?: number | null
      citations?: Citation[] | null
    }
  | { type: 'heartbeat'; ts: number }
  | { type: 'session_closed'; reason: string }
  | { type: 'error'; code: string; message: string }

export type VoiceWsMessage = VoiceClientMessage | VoiceServerMessage

export const VOICE_SAMPLE_RATE_HZ = 24_000
export const VOICE_MAX_CHUNK_BYTES = 4096
export const VOICE_HEARTBEAT_INTERVAL_MS = 30_000
export const VOICE_AUDIO_FORMAT = 'pcm16_24k_mono'

const SERVER_MESSAGE_TYPES = new Set<string>([
  'session_started',
  'transcript_partial',
  'transcript_final',
  'assistant_text_delta',
  'audio_out',
  'tool_start',
  'tool_end',
  'interrupted',
  'turn_complete',
  'heartbeat',
  'session_closed',
  'error',
])

/** Narrow a decoded frame to a server-originated message. */
export function isVoiceServerMessage(message: VoiceWsMessage): message is VoiceServerMessage {
  return SERVER_MESSAGE_TYPES.has(message.type)
}

export interface TurnCompleteMetadata {
  tools_used?: string[] | null
  retrieved_chunk_count?: number | null
  citations?: Citation[] | null
}
