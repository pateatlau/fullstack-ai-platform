import { getStoredAccessToken, getStoredGuestToken, storeGuestToken } from '../auth/tokenStorage'
import type { ChatRequest, ChatSessionDetail, ChatSessionListItem } from '../types/chat'

const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? 'http://localhost:8000'

const GUEST_TOKEN_HEADER = 'X-Guest-Token'
export const REQUEST_ID_HEADER = 'X-Request-ID'

let lastRequestId: string | null = null
let pendingRetryRequestId: string | null = null

/**
 * When set, the next API request forwards this value as ``X-Request-ID`` so
 * backend retries stay correlated with the original attempt.
 */
export function setRetryRequestId(requestId: string | null): void {
  pendingRetryRequestId = requestId
}

export function getLastRequestId(): string | null {
  return lastRequestId
}

/** Captures ``X-Request-ID`` from a response for retry traceability. */
function captureRequestId(response: Response): void {
  lastRequestId = response.headers.get(REQUEST_ID_HEADER)
}

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
  if (pendingRetryRequestId) {
    headers[REQUEST_ID_HEADER] = pendingRetryRequestId
    pendingRetryRequestId = null
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
  // Populated when backend persistence is active (plan Section 2.4).
  session_id?: string | null
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
export async function sendChat(request: ChatRequest, signal?: AbortSignal): Promise<ChatResponse> {
  const response = await fetch(`${API_BASE_URL}/api/chat`, {
    method: 'POST',
    headers: buildRequestHeaders(),
    body: JSON.stringify(request),
    signal,
  })

  captureGuestToken(response)
  captureRequestId(response)

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
  captureRequestId(response)

  return response
}

/** Lists the caller's chat sessions (plan Section 2.2). Owner-scoped and
 * ordered server-side; empty for a guest with no default chat yet, or when
 * backend persistence is disabled. */
export async function listChatSessions(): Promise<ChatSessionListItem[]> {
  const response = await fetch(`${API_BASE_URL}/api/chat/sessions`, {
    method: 'GET',
    headers: buildRequestHeaders(),
  })

  captureGuestToken(response)
  captureRequestId(response)

  if (!response.ok) {
    throw await toChatApiError(response, `Failed to list chat sessions: ${response.status}`)
  }

  return (await response.json()) as ChatSessionListItem[]
}

/** Creates a new empty chat session (authenticated-only; guests get 403 `new_chat_forbidden`). */
export async function createChatSession(): Promise<ChatSessionDetail> {
  const response = await fetch(`${API_BASE_URL}/api/chat/sessions`, {
    method: 'POST',
    headers: buildRequestHeaders(),
  })

  captureGuestToken(response)
  captureRequestId(response)

  if (!response.ok) {
    throw await toChatApiError(response, `Failed to create chat session: ${response.status}`)
  }

  return (await response.json()) as ChatSessionDetail
}

/** Fetches a session's full transcript to resume it (ownership-checked; 404 if foreign/unknown). */
export async function getChatSession(sessionId: string): Promise<ChatSessionDetail> {
  const response = await fetch(`${API_BASE_URL}/api/chat/sessions/${sessionId}`, {
    method: 'GET',
    headers: buildRequestHeaders(),
  })

  captureGuestToken(response)
  captureRequestId(response)

  if (!response.ok) {
    throw await toChatApiError(response, `Failed to load chat session: ${response.status}`)
  }

  return (await response.json()) as ChatSessionDetail
}
