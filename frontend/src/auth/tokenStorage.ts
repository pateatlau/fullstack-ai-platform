/**
 * App JWT + authenticated-user storage (Decision D1: `localStorage`, transported
 * as `Authorization: Bearer`). This is the single source of truth read by both
 * `AuthContext` (React state) and `chatClient` (plain fetch calls), so a page
 * reload rehydrates the same session both places read from.
 *
 * Guest-token (`X-Guest-Token`) storage lives here too, but is intentionally
 * independent of the access token: it is retained across both login and
 * logout (plan Section 4.3) so `clearAccessToken` never touches it.
 */

import type { AuthenticatedUser } from '../types/auth'

const ACCESS_TOKEN_KEY = 'auth.accessToken'
const USER_KEY = 'auth.user'
const GUEST_TOKEN_KEY = 'auth.guestToken'

export function getStoredAccessToken(): string | null {
  try {
    return window.localStorage.getItem(ACCESS_TOKEN_KEY)
  } catch {
    return null
  }
}

export function getStoredUser(): AuthenticatedUser | null {
  try {
    const raw = window.localStorage.getItem(USER_KEY)
    return raw ? (JSON.parse(raw) as AuthenticatedUser) : null
  } catch {
    return null
  }
}

export function storeSession(accessToken: string, user: AuthenticatedUser): void {
  try {
    window.localStorage.setItem(ACCESS_TOKEN_KEY, accessToken)
    window.localStorage.setItem(USER_KEY, JSON.stringify(user))
  } catch {
    // localStorage may be unavailable (private mode, quota exceeded); the
    // session simply won't persist across reloads in that case.
  }
}

export function clearAccessToken(): void {
  try {
    window.localStorage.removeItem(ACCESS_TOKEN_KEY)
    window.localStorage.removeItem(USER_KEY)
  } catch {
    // no-op
  }
}

export function getStoredGuestToken(): string | null {
  try {
    return window.localStorage.getItem(GUEST_TOKEN_KEY)
  } catch {
    return null
  }
}

/** Persists a server-minted guest token (captured from the `X-Guest-Token` response header). */
export function storeGuestToken(guestToken: string): void {
  try {
    window.localStorage.setItem(GUEST_TOKEN_KEY, guestToken)
  } catch {
    // no-op; guest continuity simply won't persist across reloads.
  }
}
