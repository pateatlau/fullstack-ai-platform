/* @vitest-environment jsdom */

import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatPage } from './ChatPage'
import { storeSession } from '../auth/tokenStorage'
import { renderWithProviders } from '../test/renderWithProviders'
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

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/**
 * Routes `fetch` calls by URL/method to fixture responses (plan Section 6.2's
 * `GET/POST /api/chat/sessions` and `GET /api/chat/sessions/{id}`), so each
 * test only needs to describe backend responses, not request plumbing.
 */
function createRoutedFetchMock(
  handler: (url: string, method: string) => Response | Promise<Response>,
): ReturnType<typeof vi.fn> {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    const method = init?.method ?? 'GET'
    return handler(url, method)
  })
}

describe('ChatPage session sidebar wiring', () => {
  beforeEach(() => {
    Object.defineProperty(globalThis.HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    })
    window.localStorage.clear()
    storeSession(makeJwt(3600), user)
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    window.localStorage.clear()
  })

  it('lists sessions on mount and auto-resumes the most recently active one', async () => {
    const fetchMock = createRoutedFetchMock((url, method) => {
      if (url.endsWith('/api/chat/sessions') && method === 'GET') {
        return jsonResponse([
          {
            id: 's1',
            title: 'Trip planning',
            last_message_at: '2026-01-01T00:00:00Z',
            created_at: '2026-01-01T00:00:00Z',
          },
          { id: 's2', title: null, last_message_at: null, created_at: '2025-12-01T00:00:00Z' },
        ])
      }
      if (url.endsWith('/api/chat/sessions/s1') && method === 'GET') {
        return jsonResponse({
          id: 's1',
          title: 'Trip planning',
          last_message_at: '2026-01-01T00:00:00Z',
          messages: [
            {
              id: 'm1',
              seq: 1,
              role: 'user',
              content: 'Where should I go?',
              provider: null,
              model: null,
              status: 'complete',
              finish_reason: null,
              created_at: '2026-01-01T00:00:00Z',
            },
            {
              id: 'm2',
              seq: 2,
              role: 'assistant',
              content: 'How about Kyoto?',
              provider: 'openai',
              model: 'gpt-4o-mini',
              status: 'complete',
              finish_reason: 'stop',
              created_at: '2026-01-01T00:00:01Z',
            },
          ],
        })
      }
      throw new Error(`Unexpected fetch: ${method} ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderWithProviders(<ChatPage />)

    await waitFor(() => {
      expect(screen.getByText('Where should I go?')).not.toBeNull()
      expect(screen.getByText('How about Kyoto?')).not.toBeNull()
    })

    expect(screen.getAllByText('Trip planning').length).toBeGreaterThan(0)
    // The untitled second session shows up as a selectable "Saved" entry.
    expect(screen.getByRole('button', { name: /New conversation/ })).not.toBeNull()
  })

  it('clicking the current entry with no active session does not fetch a sentinel session id', async () => {
    // Authenticated with no sessions yet: activeSessionId stays null and the
    // "Current" entry falls back to the local 'unsaved-session' sentinel id.
    const fetchMock = createRoutedFetchMock((url, method) => {
      if (url.endsWith('/api/chat/sessions') && method === 'GET') {
        return jsonResponse([])
      }
      throw new Error(`Unexpected fetch: ${method} ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderWithProviders(<ChatPage />)

    const currentEntry = await screen.findByRole('button', { name: /New conversation/ })
    await userEvent.setup().click(currentEntry)

    // No GET to /api/chat/sessions/unsaved-session (or any other id) and no
    // "not found" error surfaced.
    const sessionDetailCalls = fetchMock.mock.calls.filter(([input]) => {
      const url = typeof input === 'string' ? input : input.toString()
      return /\/api\/chat\/sessions\/[^/]+$/.test(url)
    })
    expect(sessionDetailCalls).toHaveLength(0)
    expect(screen.queryByText(/not found/i)).toBeNull()
  })

  it('switching to a saved session fetches and loads its transcript', async () => {
    const fetchMock = createRoutedFetchMock((url, method) => {
      if (url.endsWith('/api/chat/sessions') && method === 'GET') {
        return jsonResponse([
          {
            id: 's1',
            title: 'First chat',
            last_message_at: '2026-01-02T00:00:00Z',
            created_at: '2026-01-01T00:00:00Z',
          },
          {
            id: 's2',
            title: 'Second chat',
            last_message_at: '2026-01-01T00:00:00Z',
            created_at: '2025-12-01T00:00:00Z',
          },
        ])
      }
      if (url.endsWith('/api/chat/sessions/s1') && method === 'GET') {
        return jsonResponse({ id: 's1', title: 'First chat', last_message_at: null, messages: [] })
      }
      if (url.endsWith('/api/chat/sessions/s2') && method === 'GET') {
        return jsonResponse({
          id: 's2',
          title: 'Second chat',
          last_message_at: '2026-01-01T00:00:00Z',
          messages: [
            {
              id: 'm3',
              seq: 1,
              role: 'user',
              content: 'From the second chat',
              provider: null,
              model: null,
              status: 'complete',
              finish_reason: null,
              created_at: '2026-01-01T00:00:00Z',
            },
          ],
        })
      }
      throw new Error(`Unexpected fetch: ${method} ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderWithProviders(<ChatPage />)

    const secondChatButton = await screen.findByRole('button', { name: /Second chat/ })
    await userEvent.setup().click(secondChatButton)

    await waitFor(() => {
      expect(screen.getByText('From the second chat')).not.toBeNull()
    })
  })

  it('"+ New chat" creates a session via POST and switches to an empty transcript', async () => {
    let createdSessionExists = false
    const fetchMock = createRoutedFetchMock((url, method) => {
      if (url.endsWith('/api/chat/sessions') && method === 'GET') {
        return jsonResponse(
          createdSessionExists
            ? [
                {
                  id: 'new1',
                  title: null,
                  last_message_at: null,
                  created_at: '2026-02-01T00:00:00Z',
                },
              ]
            : [],
        )
      }
      if (url.endsWith('/api/chat/sessions') && method === 'POST') {
        createdSessionExists = true
        return jsonResponse({ id: 'new1', title: null, last_message_at: null, messages: [] }, 201)
      }
      throw new Error(`Unexpected fetch: ${method} ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderWithProviders(<ChatPage />)

    const newChatButton = await screen.findByRole('button', { name: '+ New chat' })
    await userEvent.setup().click(newChatButton)

    await waitFor(() => {
      const postCalls = fetchMock.mock.calls.filter(
        ([, init]) => (init as RequestInit | undefined)?.method === 'POST',
      )
      expect(postCalls.length).toBe(1)
    })

    expect(screen.getByText('Start a conversation to build your first session.')).not.toBeNull()
  })

  it('foreign/unknown session id returns 404, clears the active session, and shows not found', async () => {
    const fetchMock = createRoutedFetchMock((url, method) => {
      if (url.endsWith('/api/chat/sessions') && method === 'GET') {
        return jsonResponse([
          {
            id: 's1',
            title: 'First chat',
            last_message_at: null,
            created_at: '2026-01-01T00:00:00Z',
          },
          {
            id: 'gone',
            title: 'Deleted elsewhere',
            last_message_at: null,
            created_at: '2025-01-01T00:00:00Z',
          },
        ])
      }
      if (url.endsWith('/api/chat/sessions/s1') && method === 'GET') {
        return jsonResponse({
          id: 's1',
          title: 'First chat',
          last_message_at: null,
          messages: [
            {
              id: 'm1',
              seq: 1,
              role: 'user',
              content: 'Hello from the first chat',
              provider: null,
              model: null,
              status: 'complete',
              finish_reason: null,
              created_at: '2026-01-01T00:00:00Z',
            },
          ],
        })
      }
      if (url.endsWith('/api/chat/sessions/gone') && method === 'GET') {
        return jsonResponse(
          { error: { code: 'session_not_found', message: 'Chat session not found.' } },
          404,
        )
      }
      throw new Error(`Unexpected fetch: ${method} ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderWithProviders(<ChatPage />)

    await waitFor(() => {
      expect(screen.getByText('Hello from the first chat')).not.toBeNull()
    })

    const goneButton = await screen.findByRole('button', { name: /Deleted elsewhere/ })
    await userEvent.setup().click(goneButton)

    await waitFor(() => {
      expect(screen.getByText(/that chat session was not found/i)).not.toBeNull()
    })

    // The previous session's transcript must not linger once its session is
    // gone (plan Section 6.6) — LOAD_SESSION clears activeSessionId+messages together.
    expect(screen.queryByText('Hello from the first chat')).toBeNull()
  })
})
