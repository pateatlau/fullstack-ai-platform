/* @vitest-environment jsdom */

import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import {
  clearAccessToken,
  getStoredAccessToken,
  getStoredGuestToken,
  getStoredUser,
  storeGuestToken,
  storeSession,
} from './tokenStorage'
import type { AuthenticatedUser } from '../types/auth'

const user: AuthenticatedUser = {
  id: 'user-1',
  email: 'person@example.com',
  display_name: 'Person',
  picture_url: null,
}

describe('tokenStorage', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    window.localStorage.clear()
  })

  it('returns null when nothing is stored', () => {
    expect(getStoredAccessToken()).toBeNull()
    expect(getStoredUser()).toBeNull()
  })

  it('round-trips the access token and user through storeSession', () => {
    storeSession('jwt-token', user)

    expect(getStoredAccessToken()).toBe('jwt-token')
    expect(getStoredUser()).toEqual(user)
  })

  it('clears both the token and the user on clearAccessToken', () => {
    storeSession('jwt-token', user)
    clearAccessToken()

    expect(getStoredAccessToken()).toBeNull()
    expect(getStoredUser()).toBeNull()
  })

  it('round-trips the guest token through storeGuestToken', () => {
    expect(getStoredGuestToken()).toBeNull()

    storeGuestToken('guest-token-1')

    expect(getStoredGuestToken()).toBe('guest-token-1')
  })

  it('retains the guest token across clearAccessToken (plan Section 4.3)', () => {
    storeSession('jwt-token', user)
    storeGuestToken('guest-token-1')

    clearAccessToken()

    expect(getStoredAccessToken()).toBeNull()
    expect(getStoredGuestToken()).toBe('guest-token-1')
  })
})
