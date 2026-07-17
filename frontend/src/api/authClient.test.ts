/* @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { loginWithGoogle } from './authClient'

function tokenResponse(): Response {
  return new Response(
    JSON.stringify({
      access_token: 'jwt',
      token_type: 'bearer',
      expires_in: 3600,
      user: { id: 'user-1', email: null, display_name: null, picture_url: null },
    }),
    { status: 200, headers: { 'Content-Type': 'application/json' } },
  )
}

describe('authClient.loginWithGoogle', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('omits X-Guest-Token when no guest token is presented', async () => {
    const fetchMock = vi.fn().mockResolvedValue(tokenResponse())
    vi.stubGlobal('fetch', fetchMock)

    await loginWithGoogle('id-token')

    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>
    expect(headers['X-Guest-Token']).toBeUndefined()
  })

  it('sends the guest token as X-Guest-Token when provided', async () => {
    const fetchMock = vi.fn().mockResolvedValue(tokenResponse())
    vi.stubGlobal('fetch', fetchMock)

    await loginWithGoogle('id-token', 'presenting-guest-token')

    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>
    expect(headers['X-Guest-Token']).toBe('presenting-guest-token')
  })
})
