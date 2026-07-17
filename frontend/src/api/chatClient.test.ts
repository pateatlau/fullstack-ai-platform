/* @vitest-environment jsdom */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { sendChat, streamChat } from './chatClient'
import { getStoredGuestToken, storeGuestToken, storeSession } from '../auth/tokenStorage'
import type { AuthenticatedUser } from '../types/auth'

const user: AuthenticatedUser = {
  id: 'user-1',
  email: 'person@example.com',
  display_name: 'Person',
  picture_url: null,
}

function jsonResponse(body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

describe('chatClient Authorization header', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('sendChat omits Authorization when no app JWT is stored', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        id: 'resp_1',
        role: 'assistant',
        content: 'hi',
        model: 'gpt-4o-mini',
        provider: 'openai',
        created_at: 't0',
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await sendChat({ messages: [{ role: 'user', content: 'hi' }] })

    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>
    expect(headers.Authorization).toBeUndefined()
    expect(headers['Content-Type']).toBe('application/json')
  })

  it('sendChat attaches Authorization: Bearer when an app JWT is stored', async () => {
    storeSession('stored-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        id: 'resp_1',
        role: 'assistant',
        content: 'hi',
        model: 'gpt-4o-mini',
        provider: 'openai',
        created_at: 't0',
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await sendChat({ messages: [{ role: 'user', content: 'hi' }] })

    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>
    expect(headers.Authorization).toBe('Bearer stored-jwt')
  })

  it('streamChat attaches Authorization: Bearer when an app JWT is stored', () => {
    storeSession('stream-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    const controller = new AbortController()
    void streamChat({ messages: [{ role: 'user', content: 'hi' }] }, controller.signal)

    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>
    expect(headers.Authorization).toBe('Bearer stream-jwt')
  })
})

describe('chatClient guest-token wiring', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('sendChat sends the stored guest token as X-Guest-Token', async () => {
    storeGuestToken('guest-abc')
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        id: 'resp_1',
        role: 'assistant',
        content: 'hi',
        model: 'gpt-4o-mini',
        provider: 'openai',
        created_at: 't0',
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await sendChat({ messages: [{ role: 'user', content: 'hi' }] })

    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>
    expect(headers['X-Guest-Token']).toBe('guest-abc')
  })

  it('sendChat omits X-Guest-Token when none is stored', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        id: 'resp_1',
        role: 'assistant',
        content: 'hi',
        model: 'gpt-4o-mini',
        provider: 'openai',
        created_at: 't0',
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await sendChat({ messages: [{ role: 'user', content: 'hi' }] })

    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>
    expect(headers['X-Guest-Token']).toBeUndefined()
  })

  it('sendChat captures and persists a minted X-Guest-Token from the response', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(
        {
          id: 'resp_1',
          role: 'assistant',
          content: 'hi',
          model: 'gpt-4o-mini',
          provider: 'openai',
          created_at: 't0',
        },
        { 'X-Guest-Token': 'minted-guest-token' },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    expect(getStoredGuestToken()).toBeNull()

    await sendChat({ messages: [{ role: 'user', content: 'hi' }] })

    expect(getStoredGuestToken()).toBe('minted-guest-token')
  })

  it('streamChat sends the stored guest token and captures a minted one from the response', async () => {
    storeGuestToken('guest-xyz')
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(null, { status: 200, headers: { 'X-Guest-Token': 'new-minted-token' } }),
      )
    vi.stubGlobal('fetch', fetchMock)

    const controller = new AbortController()
    await streamChat({ messages: [{ role: 'user', content: 'hi' }] }, controller.signal)

    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>
    expect(headers['X-Guest-Token']).toBe('guest-xyz')
    expect(getStoredGuestToken()).toBe('new-minted-token')
  })
})
