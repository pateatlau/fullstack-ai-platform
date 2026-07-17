/* @vitest-environment jsdom */

import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider, useAuthContext } from './AuthContext'
import { AuthApiError } from '../api/authClient'
import {
  getStoredAccessToken,
  getStoredGuestToken,
  getStoredUser,
  storeGuestToken,
  storeSession,
} from '../auth/tokenStorage'
import * as authClient from '../api/authClient'
import type { AuthenticatedUser } from '../types/auth'

const user: AuthenticatedUser = {
  id: 'user-1',
  email: 'person@example.com',
  display_name: 'Person',
  picture_url: null,
}

function makeJwt(expSecondsFromNow: number): string {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))
  const exp = Math.floor(Date.now() / 1000) + expSecondsFromNow
  const payload = btoa(JSON.stringify({ exp }))
  return `${header}.${payload}.signature`
}

describe('AuthContext', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('starts as guest when no session is stored', () => {
    const { result } = renderHook(() => useAuthContext(), { wrapper: AuthProvider })

    expect(result.current.status).toBe('guest')
    expect(result.current.user).toBeNull()
  })

  it('rehydrates an authenticated session from storage on mount', async () => {
    storeSession('stored-jwt', user)

    const { result } = renderHook(() => useAuthContext(), { wrapper: AuthProvider })

    await waitFor(() => {
      expect(result.current.status).toBe('authenticated')
    })
    expect(result.current.user).toEqual(user)
  })

  it('clears an expired stored session on mount and flags sessionExpired', () => {
    storeSession(makeJwt(-3600), user)

    const { result } = renderHook(() => useAuthContext(), { wrapper: AuthProvider })

    expect(result.current.status).toBe('guest')
    expect(result.current.user).toBeNull()
    expect(result.current.sessionExpired).toBe(true)
    expect(getStoredAccessToken()).toBeNull()
  })

  it('does not flag an unexpired stored session as expired', () => {
    storeSession(makeJwt(3600), user)

    const { result } = renderHook(() => useAuthContext(), { wrapper: AuthProvider })

    expect(result.current.status).toBe('authenticated')
    expect(result.current.sessionExpired).toBe(false)
  })

  it('dismissSessionExpired clears the flag', () => {
    storeSession(makeJwt(-3600), user)
    const { result } = renderHook(() => useAuthContext(), { wrapper: AuthProvider })
    expect(result.current.sessionExpired).toBe(true)

    act(() => {
      result.current.dismissSessionExpired()
    })

    expect(result.current.sessionExpired).toBe(false)
  })

  it('login exchanges the Google ID token and stores the resulting session', async () => {
    vi.spyOn(authClient, 'loginWithGoogle').mockResolvedValue({
      access_token: 'new-jwt',
      token_type: 'bearer',
      expires_in: 3600,
      user,
    })

    const { result } = renderHook(() => useAuthContext(), { wrapper: AuthProvider })

    await act(async () => {
      await result.current.login('google-id-token')
    })

    expect(result.current.status).toBe('authenticated')
    expect(result.current.user).toEqual(user)
    expect(getStoredAccessToken()).toBe('new-jwt')
    expect(getStoredUser()).toEqual(user)
  })

  it('logout clears the stored session and reverts to guest', async () => {
    storeSession('stored-jwt', user)
    const { result } = renderHook(() => useAuthContext(), { wrapper: AuthProvider })
    await waitFor(() => expect(result.current.status).toBe('authenticated'))

    act(() => {
      result.current.logout()
    })

    expect(result.current.status).toBe('guest')
    expect(result.current.user).toBeNull()
    expect(getStoredAccessToken()).toBeNull()
  })

  it('login sends the stored guest token so the backend can link it', async () => {
    storeGuestToken('presenting-guest-token')
    const loginSpy = vi.spyOn(authClient, 'loginWithGoogle').mockResolvedValue({
      access_token: 'new-jwt',
      token_type: 'bearer',
      expires_in: 3600,
      user,
    })

    const { result } = renderHook(() => useAuthContext(), { wrapper: AuthProvider })

    await act(async () => {
      await result.current.login('google-id-token')
    })

    expect(loginSpy).toHaveBeenCalledWith('google-id-token', 'presenting-guest-token')
  })

  it('retains the guest token across login', async () => {
    storeGuestToken('presenting-guest-token')
    vi.spyOn(authClient, 'loginWithGoogle').mockResolvedValue({
      access_token: 'new-jwt',
      token_type: 'bearer',
      expires_in: 3600,
      user,
    })

    const { result } = renderHook(() => useAuthContext(), { wrapper: AuthProvider })

    await act(async () => {
      await result.current.login('google-id-token')
    })

    expect(getStoredGuestToken()).toBe('presenting-guest-token')
  })

  it('classifies auth_not_configured as a friendly, non-throwing loginError', async () => {
    vi.spyOn(authClient, 'loginWithGoogle').mockRejectedValue(
      new AuthApiError('boom', 503, 'auth_not_configured'),
    )

    const { result } = renderHook(() => useAuthContext(), { wrapper: AuthProvider })

    await act(async () => {
      await result.current.login('google-id-token')
    })

    expect(result.current.status).toBe('guest')
    expect(result.current.loginError).toEqual({
      code: 'auth_not_configured',
      message: 'Login is temporarily unavailable.',
    })
  })

  it('classifies invalid_google_token as a friendly loginError', async () => {
    vi.spyOn(authClient, 'loginWithGoogle').mockRejectedValue(
      new AuthApiError('boom', 401, 'invalid_google_token'),
    )

    const { result } = renderHook(() => useAuthContext(), { wrapper: AuthProvider })

    await act(async () => {
      await result.current.login('google-id-token')
    })

    expect(result.current.loginError).toEqual({
      code: 'invalid_google_token',
      message: 'Google sign-in failed. Please try again.',
    })
  })

  it('classifies a non-AuthApiError (network failure) as a generic loginError', async () => {
    vi.spyOn(authClient, 'loginWithGoogle').mockRejectedValue(new TypeError('Failed to fetch'))

    const { result } = renderHook(() => useAuthContext(), { wrapper: AuthProvider })

    await act(async () => {
      await result.current.login('google-id-token')
    })

    expect(result.current.loginError).toEqual({
      code: 'network_error',
      message: 'Could not reach the backend. Check your connection and try again.',
    })
  })

  it('clears loginError on the next login attempt', async () => {
    const loginSpy = vi
      .spyOn(authClient, 'loginWithGoogle')
      .mockRejectedValueOnce(new AuthApiError('boom', 401, 'invalid_google_token'))
      .mockResolvedValueOnce({
        access_token: 'new-jwt',
        token_type: 'bearer',
        expires_in: 3600,
        user,
      })

    const { result } = renderHook(() => useAuthContext(), { wrapper: AuthProvider })

    await act(async () => {
      await result.current.login('bad-token')
    })
    expect(result.current.loginError).not.toBeNull()

    await act(async () => {
      await result.current.login('good-token')
    })

    expect(result.current.loginError).toBeNull()
    expect(result.current.status).toBe('authenticated')
    expect(loginSpy).toHaveBeenCalledTimes(2)
  })

  it('handleInvalidAccessToken clears the session and flags sessionExpired', async () => {
    storeSession('stored-jwt', user)
    const { result } = renderHook(() => useAuthContext(), { wrapper: AuthProvider })
    await waitFor(() => expect(result.current.status).toBe('authenticated'))

    act(() => {
      result.current.handleInvalidAccessToken()
    })

    expect(result.current.status).toBe('guest')
    expect(result.current.user).toBeNull()
    expect(result.current.sessionExpired).toBe(true)
    expect(getStoredAccessToken()).toBeNull()
  })

  it('retains the guest token across logout', async () => {
    storeSession('stored-jwt', user)
    storeGuestToken('presenting-guest-token')
    const { result } = renderHook(() => useAuthContext(), { wrapper: AuthProvider })
    await waitFor(() => expect(result.current.status).toBe('authenticated'))

    act(() => {
      result.current.logout()
    })

    expect(getStoredGuestToken()).toBe('presenting-guest-token')
  })
})
