import { getStoredAccessToken, getStoredGuestToken, storeGuestToken } from '../auth/tokenStorage'
import type { ChatRequest } from '../types/chat'

const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? 'http://localhost:8000'

const GUEST_TOKEN_HEADER = 'X-Guest-Token'

/**
 * Attaches `Authorization: Bearer <jwt>` when an app JWT is stored (Decision D1),
 * and the stored guest token (if any) so the server can resolve/extend guest
 * continuity even before login.
 */
function buildRequestHeaders(): HeadersInit {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const accessToken = getStoredAccessToken()
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`
  }
  const guestToken = getStoredGuestToken()
  if (guestToken) {
    headers[GUEST_TOKEN_HEADER] = guestToken
  }
  return headers
}

/** Captures a server-minted guest token from a response and persists it, if present. */
function captureGuestToken(response: Response): void {
  const minted = response.headers.get(GUEST_TOKEN_HEADER)
  if (minted) {
    storeGuestToken(minted)
  }
}

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
    headers: buildRequestHeaders(),
    body: JSON.stringify(request),
  })

  captureGuestToken(response)

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
export async function streamChat(request: ChatRequest, signal: AbortSignal): Promise<Response> {
  const response = await fetch(`${API_BASE_URL}/api/chat/stream`, {
    method: 'POST',
    headers: buildRequestHeaders(),
    body: JSON.stringify(request),
    signal,
  })

  captureGuestToken(response)

  return response
}
