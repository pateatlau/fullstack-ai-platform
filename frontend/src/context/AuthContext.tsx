import { createContext, useContext, useState, type ReactNode } from 'react'
import { AuthApiError, loginWithGoogle } from '../api/authClient'
import { isJwtExpired } from '../auth/jwt'
import {
  clearAccessToken,
  getStoredAccessToken,
  getStoredGuestToken,
  getStoredUser,
  storeSession,
} from '../auth/tokenStorage'
import type { AuthenticatedUser } from '../types/auth'

export type AuthStatus = 'guest' | 'authenticated'

/** Maps a login failure to user-facing copy (plan Section 5.5). */
export interface LoginErrorInfo {
  code: string
  message: string
}

interface AuthContextValue {
  status: AuthStatus
  user: AuthenticatedUser | null
  /** Exchanges a Google ID token for an app JWT and switches to authenticated. Never throws; failures populate `loginError`. */
  login: (idToken: string) => Promise<void>
  /** Client-only token discard (Decision D2): no backend call, no revocation. */
  logout: () => void
  /** Set when the most recent `login()` call failed; cleared on the next login attempt or `clearLoginError()`. */
  loginError: LoginErrorInfo | null
  clearLoginError: () => void
  /** True after an expired/invalid app JWT was locally detected and cleared (plan Section 3.3). */
  sessionExpired: boolean
  dismissSessionExpired: () => void
  /**
   * Defensive handler for a `invalid_access_token` response from an
   * authenticated request (today's backend degrades to guest silently
   * instead, but this keeps the client correct if that ever changes).
   * Clears the JWT, drops to guest, and surfaces the same re-login prompt
   * as local expiry detection.
   */
  handleInvalidAccessToken: () => void
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

function classifyLoginError(error: unknown): LoginErrorInfo {
  if (error instanceof AuthApiError) {
    if (error.code === 'auth_not_configured') {
      return { code: error.code, message: 'Login is temporarily unavailable.' }
    }
    if (error.code === 'invalid_google_token') {
      return { code: error.code, message: 'Google sign-in failed. Please try again.' }
    }
    return { code: error.code ?? 'auth_error', message: error.message }
  }
  return {
    code: 'network_error',
    message: 'Could not reach the backend. Check your connection and try again.',
  }
}

interface StoredSession {
  status: AuthStatus
  user: AuthenticatedUser | null
  expired: boolean
}

function readStoredSession(): StoredSession {
  const storedToken = getStoredAccessToken()
  const storedUser = getStoredUser()
  if (!storedToken || !storedUser) {
    return { status: 'guest', user: null, expired: false }
  }
  if (isJwtExpired(storedToken)) {
    clearAccessToken()
    return { status: 'guest', user: null, expired: true }
  }
  return { status: 'authenticated', user: storedUser, expired: false }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [initialSession] = useState(readStoredSession)
  const [status, setStatus] = useState<AuthStatus>(initialSession.status)
  const [user, setUser] = useState<AuthenticatedUser | null>(initialSession.user)
  const [loginError, setLoginError] = useState<LoginErrorInfo | null>(null)
  const [sessionExpired, setSessionExpired] = useState<boolean>(initialSession.expired)

  const login = async (idToken: string): Promise<void> => {
    setLoginError(null)
    try {
      const result = await loginWithGoogle(idToken, getStoredGuestToken() ?? undefined)
      storeSession(result.access_token, result.user)
      setUser(result.user)
      setStatus('authenticated')
      setSessionExpired(false)
    } catch (error) {
      setLoginError(classifyLoginError(error))
    }
  }

  const logout = (): void => {
    // Retains the guest token (plan Section 4.3): only the app JWT is discarded.
    clearAccessToken()
    setUser(null)
    setStatus('guest')
    setSessionExpired(false)
  }

  const handleInvalidAccessToken = (): void => {
    clearAccessToken()
    setUser(null)
    setStatus('guest')
    setSessionExpired(true)
  }

  return (
    <AuthContext.Provider
      value={{
        status,
        user,
        login,
        logout,
        loginError,
        clearLoginError: () => setLoginError(null),
        sessionExpired,
        dismissSessionExpired: () => setSessionExpired(false),
        handleInvalidAccessToken,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components -- context hook pairs with AuthProvider by design
export function useAuthContext(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuthContext must be used within an AuthProvider')
  }
  return context
}
