import type { ChatRequest } from '../types/chat'

const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? 'http://localhost:8000'

export interface ChatResponse {
  id: string
  role: 'assistant'
  content: string
  model: string
  provider: 'openai' | 'gemini' | 'groq' | 'anthropic'
  created_at: string
}

interface ErrorResponse {
  error?: {
    code?: string
    message?: string
  }
}

export class ChatApiError extends Error {
  status: number
  code?: string

  constructor(message: string, status: number, code?: string) {
    super(message)
    this.name = 'ChatApiError'
    this.status = status
    this.code = code
  }
}

export async function toChatApiError(
  response: Response,
  fallbackMessage: string,
): Promise<ChatApiError> {
  let payload: ErrorResponse | null

  try {
    payload = (await response.json()) as ErrorResponse
  } catch {
    payload = null
  }

  return new ChatApiError(
    payload?.error?.message ?? fallbackMessage,
    response.status,
    payload?.error?.code,
  )
}

/** Calls the non-streaming `POST /api/chat` fallback endpoint. */
export async function sendChat(request: ChatRequest): Promise<ChatResponse> {
  const response = await fetch(`${API_BASE_URL}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    throw await toChatApiError(response, `Chat request failed: ${response.status}`)
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
