/* @vitest-environment jsdom */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { askRag, RAG_DISABLED_MESSAGE, RAG_FEATURE_DISABLED_CODE, RagApiError } from './ragClient'
import { getLastRequestId, REQUEST_ID_HEADER } from './request'
import { storeSession } from '../auth/tokenStorage'
import type { AuthenticatedUser } from '../types/auth'

const user: AuthenticatedUser = {
  id: 'user-1',
  email: 'person@example.com',
  display_name: 'Person',
  picture_url: null,
}

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

describe('ragClient', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('askRag attaches Bearer token and JSON body', async () => {
    storeSession('rag-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        answer: 'The policy covers remote work.',
        retrieved_chunks: [],
        truncated: false,
        model: 'gpt-4o-mini',
        provider: 'openai',
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await askRag('What is the remote work policy?')

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toContain('/api/rag/ask')
    expect(init.method).toBe('POST')
    const headers = init.headers as Record<string, string>
    expect(headers.Authorization).toBe('Bearer rag-jwt')
    expect(headers['Content-Type']).toBe('application/json')
    expect(JSON.parse(init.body as string)).toEqual({
      question: 'What is the remote work policy?',
    })
  })

  it('surfaces 401 errors', async () => {
    storeSession('expired-jwt', user)
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ error: { code: 'unauthorized', message: 'Unauthorized' } }, 401),
      )
    vi.stubGlobal('fetch', fetchMock)

    await expect(askRag('test')).rejects.toMatchObject({ status: 401 })
  })

  it('maps 503 feature_disabled to RagApiError with friendly message', async () => {
    storeSession('rag-jwt', user)
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ error: { code: RAG_FEATURE_DISABLED_CODE, message: 'RAG disabled' } }, 503),
      )
    vi.stubGlobal('fetch', fetchMock)

    await expect(askRag('test')).rejects.toSatisfy((error: unknown) => {
      expect(error).toBeInstanceOf(RagApiError)
      const ragError = error as RagApiError
      expect(ragError.status).toBe(503)
      expect(ragError.code).toBe(RAG_FEATURE_DISABLED_CODE)
      expect(ragError.message).toBe(RAG_DISABLED_MESSAGE)
      return true
    })
  })

  it('captures X-Request-ID from responses', async () => {
    storeSession('rag-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(
        {
          answer: 'ok',
          retrieved_chunks: [],
          truncated: false,
          model: 'gpt-4o-mini',
          provider: 'openai',
        },
        200,
        { [REQUEST_ID_HEADER]: 'req-rag-123' },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    await askRag('hello')
    expect(getLastRequestId()).toBe('req-rag-123')
  })
})
