/* @vitest-environment jsdom */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  deleteChatSession,
  getLastRequestId,
  sendChat,
  setRetryRequestId,
  streamChat,
  REQUEST_ID_HEADER,
} from './chatClient'
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

describe('chatClient request-id retry wiring', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('forwards X-Request-ID on the next request after setRetryRequestId', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse(
          {
            id: 'resp_1',
            role: 'assistant',
            content: 'partial',
            model: 'gpt-4o-mini',
            provider: 'openai',
            created_at: 't0',
          },
          { [REQUEST_ID_HEADER]: '6ba7b810-9dad-11d1-80b4-00c04fd430c8' },
        ),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          id: 'resp_2',
          role: 'assistant',
          content: 'retry ok',
          model: 'gpt-4o-mini',
          provider: 'openai',
          created_at: 't1',
        }),
      )
    vi.stubGlobal('fetch', fetchMock)

    await sendChat({ messages: [{ role: 'user', content: 'hi' }] })
    expect(getLastRequestId()).toBe('6ba7b810-9dad-11d1-80b4-00c04fd430c8')

    setRetryRequestId(getLastRequestId())
    await sendChat({ messages: [{ role: 'user', content: 'hi again' }] })

    const retryHeaders = fetchMock.mock.calls[1][1].headers as Record<string, string>
    expect(retryHeaders[REQUEST_ID_HEADER]).toBe('6ba7b810-9dad-11d1-80b4-00c04fd430c8')
  })

  it('sendChat includes unified chat toggle fields when provided', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        id: 'resp_toggle',
        role: 'assistant',
        content: 'grounded',
        model: 'gpt-4o-mini',
        provider: 'openai',
        created_at: 't0',
        tools_used: ['web_search'],
        retrieved_chunks: [{ chunk_id: 'c1', document_id: 'd1', chunk_index: 0, score: 0.9 }],
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await sendChat({
      messages: [{ role: 'user', content: 'hello' }],
      use_web_search: true,
      use_documents: true,
    })

    const body = JSON.parse(String(fetchMock.mock.calls[0][1].body)) as {
      use_web_search?: boolean
      use_documents?: boolean
    }
    expect(body.use_web_search).toBe(true)
    expect(body.use_documents).toBe(true)
  })
})

describe('chatClient session delete', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('deleteChatSession sends DELETE and succeeds on 204', async () => {
    storeSession('test-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)

    await deleteChatSession('session-123')

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toContain('/api/chat/sessions/session-123')
    expect(init.method).toBe('DELETE')
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer test-jwt')
  })

  it('deleteChatSession throws ChatApiError on failure', async () => {
    storeSession('test-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          error: { code: 'session_not_found', message: 'Chat session not found.' },
        }),
        { status: 404, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(deleteChatSession('missing')).rejects.toMatchObject({
      status: 404,
      code: 'session_not_found',
    })
  })
})
