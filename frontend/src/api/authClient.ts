import type { TokenResponse } from '../types/auth'

const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? 'http://localhost:8000'

interface AuthErrorResponse {
  error?: {
    code?: string
    message?: string
  }
}

export class AuthApiError extends Error {
  status: number
  code?: string

  constructor(message: string, status: number, code?: string) {
    super(message)
    this.name = 'AuthApiError'
    this.status = status
    this.code = code
  }
}

async function toAuthApiError(response: Response, fallbackMessage: string): Promise<AuthApiError> {
  let payload: AuthErrorResponse | null

  try {
    payload = (await response.json()) as AuthErrorResponse
  } catch {
    payload = null
  }

  return new AuthApiError(
    payload?.error?.message ?? fallbackMessage,
    response.status,
    payload?.error?.code,
  )
}

/**
 * Exchanges a Google ID token for an app JWT via `POST /api/auth/google`.
 * The backend is authoritative: it verifies the ID token server-side and
 * this client never self-asserts identity.
 *
 * When `guestToken` is provided, it is sent as `X-Guest-Token` so the backend
 * can link the presenting guest identity to the newly authenticated user
 * (link-only, fail-soft — plan Section 4).
 */
export async function loginWithGoogle(
  idToken: string,
  guestToken?: string,
): Promise<TokenResponse> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (guestToken) {
    headers['X-Guest-Token'] = guestToken
  }

  const response = await fetch(`${API_BASE_URL}/api/auth/google`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ id_token: idToken }),
  })

  if (!response.ok) {
    throw await toAuthApiError(response, `Google login failed: ${response.status}`)
  }

  return (await response.json()) as TokenResponse
}
