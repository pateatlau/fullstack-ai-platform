import type { ChatRequest } from '../types/chat'

const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? 'http://localhost:8000'

export interface ChatResponse {
  id: string
  role: 'assistant'
  content: string
  model: string
  provider: 'openai' | 'gemini'
  created_at: string
}

/** Calls the non-streaming `POST /api/chat` fallback endpoint. */
export async function sendChat(request: ChatRequest): Promise<ChatResponse> {
  const response = await fetch(`${API_BASE_URL}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    throw new Error(`Chat request failed: ${response.status}`)
  }

  return (await response.json()) as ChatResponse
}

/**
 * Opens the `POST /api/chat/stream` SSE connection. Returns the raw
 * `Response` so callers (see `useChatStream`) can read `response.body` with
 * their own `ReadableStreamDefaultReader` + `sseParser`.
 */
export function streamChat(request: ChatRequest, signal: AbortSignal): Promise<Response> {
  return fetch(`${API_BASE_URL}/api/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
    signal,
  })
}
