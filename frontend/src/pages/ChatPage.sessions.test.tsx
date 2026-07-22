/* @vitest-environment jsdom */

import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatPage } from './ChatPage'
import { storeSession } from '../auth/tokenStorage'
import { renderWithProviders } from '../test/renderWithProviders'
import { jsonHealthResponse } from '../test/chatFetchStubs'
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

function createControllableSseResponse(): {
  response: Response
  enqueue: (chunk: string) => void
  close: () => void
} {
  const encoder = new TextEncoder()
  let streamController: ReadableStreamDefaultController<Uint8Array> | null = null

  const response = new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller
      },
    }),
    {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    },
  )

  return {
    response,
    enqueue: (chunk: string) => {
      streamController?.enqueue(encoder.encode(chunk))
    },
    close: () => {
      streamController?.close()
    },
  }
}

/**
 * Routes `fetch` calls by URL/method to fixture responses (plan Section 6.2's
 * `GET/POST /api/chat/sessions` and `GET /api/chat/sessions/{id}`), so each
 * test only needs to describe backend responses, not request plumbing.
 */
function createRoutedFetchMock(
  handler: (url: string, method: string) => Response | Promise<Response>,
  options?: { chatStreamingEnabled?: boolean; toolsEnabled?: boolean; ragEnabled?: boolean },
): ReturnType<typeof vi.fn> {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    const method = init?.method ?? 'GET'
    if (url.endsWith('/api/health') && method === 'GET') {
      return jsonHealthResponse(
        options?.chatStreamingEnabled ?? true,
        options?.toolsEnabled ?? false,
        options?.ragEnabled ?? false,
      )
    }
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
    expect(screen.getByRole('button', { name: 'New chat' })).not.toBeNull()
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

    const currentEntry = await screen.findByRole('button', { name: 'New chat' })
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

    const secondChatButton = await screen.findByRole('button', { name: 'Second chat' })
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

    const goneButton = await screen.findByRole('button', { name: 'Deleted elsewhere' })
    await userEvent.setup().click(goneButton)

    await waitFor(() => {
      expect(screen.getByText(/that chat session was not found/i)).not.toBeNull()
    })

    // The previous session's transcript must not linger once its session is
    // gone (plan Section 6.6) — LOAD_SESSION clears activeSessionId+messages together.
    expect(screen.queryByText('Hello from the first chat')).toBeNull()
  })

  it('uses non-streaming POST /api/chat when chat_streaming_enabled is false', async () => {
    const fetchMock = createRoutedFetchMock(
      (url, method) => {
        if (url.endsWith('/api/chat/sessions') && method === 'GET') {
          return jsonResponse([])
        }
        if (url.endsWith('/api/chat') && method === 'POST') {
          return jsonResponse({
            id: 'resp_nonstream',
            role: 'assistant',
            content: 'Full reply at once',
            model: 'gpt-4o-mini',
            provider: 'openai',
            created_at: '2026-01-01T00:00:00Z',
          })
        }
        throw new Error(`Unexpected fetch: ${method} ${url}`)
      },
      { chatStreamingEnabled: false },
    )
    vi.stubGlobal('fetch', fetchMock)

    renderWithProviders(<ChatPage />)

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled()
    })

    const user = userEvent.setup()
    await user.type(screen.getByPlaceholderText('Ask something…'), 'Hello without streaming')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => {
      expect(screen.getByText('Full reply at once')).not.toBeNull()
    })

    const chatCall = fetchMock.mock.calls.find(([input, init]) => {
      const url = typeof input === 'string' ? input : input.toString()
      return url.endsWith('/api/chat') && (init?.method ?? 'GET') === 'POST'
    })
    expect(chatCall).toBeTruthy()
    expect(
      fetchMock.mock.calls.some(([input]) => {
        const url = typeof input === 'string' ? input : input.toString()
        return url.endsWith('/api/chat/stream')
      }),
    ).toBe(false)
  })

  it('shows searching web while waiting on non-streaming tool-enabled chat', async () => {
    const completePayload = {
      id: 'resp_nonstream',
      role: 'assistant',
      content: 'Here is the news',
      model: 'gpt-4o-mini',
      provider: 'openai',
      created_at: '2026-01-01T00:00:00Z',
    }
    const ndjsonBody =
      '{"type":"activity","phase":"web_search"}\n' +
      `{"type":"complete","response":${JSON.stringify(completePayload)}}\n`

    const fetchMock = createRoutedFetchMock(
      (url, method) => {
        if (url.endsWith('/api/chat/sessions') && method === 'GET') {
          return jsonResponse([])
        }
        if (url.endsWith('/api/chat') && method === 'POST') {
          return new Response(ndjsonBody, {
            status: 200,
            headers: { 'Content-Type': 'application/x-ndjson' },
          })
        }
        throw new Error(`Unexpected fetch: ${method} ${url}`)
      },
      { chatStreamingEnabled: false, toolsEnabled: true },
    )
    vi.stubGlobal('fetch', fetchMock)

    renderWithProviders(<ChatPage />)

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled()
    })

    const user = userEvent.setup()
    await user.click(screen.getByRole('checkbox', { name: 'Web search' }))
    await user.type(screen.getByPlaceholderText('Ask something…'), 'Latest news?')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => {
      expect(screen.getByText('Here is the news')).not.toBeNull()
    })

    const chatCall = fetchMock.mock.calls.find(([input, init]) => {
      const url = typeof input === 'string' ? input : input.toString()
      return url.endsWith('/api/chat') && (init?.method ?? 'GET') === 'POST'
    })
    expect(chatCall).toBeTruthy()
    const chatInit = chatCall?.[1] as RequestInit
    expect((chatInit.headers as Record<string, string>).Accept).toBe('application/x-ndjson')
  })

  it('shows typing while waiting when no web search activity is reported', async () => {
    let resolveChat!: (value: Response) => void
    const chatResponse = new Promise<Response>((resolve) => {
      resolveChat = resolve
    })

    const fetchMock = createRoutedFetchMock(
      (url, method) => {
        if (url.endsWith('/api/chat/sessions') && method === 'GET') {
          return jsonResponse([])
        }
        if (url.endsWith('/api/chat') && method === 'POST') {
          return chatResponse
        }
        throw new Error(`Unexpected fetch: ${method} ${url}`)
      },
      { chatStreamingEnabled: false, toolsEnabled: true },
    )
    vi.stubGlobal('fetch', fetchMock)

    renderWithProviders(<ChatPage />)

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled()
    })

    const user = userEvent.setup()
    await user.type(screen.getByPlaceholderText('Ask something…'), 'thanks')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByLabelText('Assistant is typing')).not.toBeNull()
    expect(screen.queryByLabelText('Assistant is searching the web')).toBeNull()

    resolveChat(
      jsonResponse({
        id: 'resp_nonstream',
        role: 'assistant',
        content: 'You are welcome',
        model: 'gpt-4o-mini',
        provider: 'openai',
        created_at: '2026-01-01T00:00:00Z',
      }),
    )

    await waitFor(() => {
      expect(screen.getByText('You are welcome')).not.toBeNull()
    })
  })

  it('shows searching documents while streaming document-grounded chat is in progress', async () => {
    const retrievalCompleteSse =
      'event: retrieval_complete\n' +
      'data: {"type":"retrieval_complete","id":"resp_docs","chunk_count":1,"timestamp":"t0"}\n\n'
    const remainingSse =
      'event: start\n' +
      'data: {"type":"start","id":"resp_docs","timestamp":"t1"}\n\n' +
      'event: delta\n' +
      'data: {"type":"delta","id":"resp_docs","content":"From your documents: fixture content.","timestamp":"t2"}\n\n' +
      'event: end\n' +
      'data: {"type":"end","id":"resp_docs","finish_reason":"stop","timestamp":"t3"}\n\n'

    const { response: streamResponse, enqueue, close } = createControllableSseResponse()

    const fetchMock = createRoutedFetchMock(
      (url, method) => {
        if (url.endsWith('/api/chat/sessions') && method === 'GET') {
          return jsonResponse([])
        }
        if (url.endsWith('/api/chat/stream') && method === 'POST') {
          return streamResponse
        }
        throw new Error(`Unexpected fetch: ${method} ${url}`)
      },
      { chatStreamingEnabled: true, toolsEnabled: false, ragEnabled: true },
    )
    vi.stubGlobal('fetch', fetchMock)

    renderWithProviders(<ChatPage />)

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled()
    })

    const user = userEvent.setup()
    await user.click(screen.getByRole('checkbox', { name: 'My documents' }))
    await user.type(screen.getByPlaceholderText('Ask something…'), 'What is in my docs?')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => {
      expect(screen.getByLabelText('Assistant is searching docs')).not.toBeNull()
    })
    expect(screen.queryByLabelText('Assistant is typing')).toBeNull()

    enqueue(retrievalCompleteSse)
    enqueue(remainingSse)
    close()

    await waitFor(() => {
      expect(screen.getByText('From your documents: fixture content.')).not.toBeNull()
    })

    const streamCall = fetchMock.mock.calls.find(([input, init]) => {
      const url = typeof input === 'string' ? input : input.toString()
      return url.endsWith('/api/chat/stream') && (init?.method ?? 'GET') === 'POST'
    })
    expect(streamCall).toBeTruthy()
    const streamInit = streamCall?.[1] as RequestInit
    const body = JSON.parse(String(streamInit.body)) as { use_documents?: boolean }
    expect(body.use_documents).toBe(true)
  })

  it('shows searching documents while document-grounded chat is in progress (non-streaming fallback)', async () => {
    const completePayload = {
      id: 'resp_docs',
      role: 'assistant',
      content: 'From your documents: fixture content.',
      model: 'gpt-4o-mini',
      provider: 'openai',
      created_at: '2026-01-01T00:00:00Z',
      retrieved_chunks: [{ chunk_id: 'c1', document_id: 'd1', chunk_index: 0, score: 0.9 }],
    }
    const ndjsonBody =
      '{"type":"activity","phase":"document_retrieval"}\n' +
      '{"type":"activity","phase":"thinking"}\n' +
      `{"type":"complete","response":${JSON.stringify(completePayload)}}\n`

    const fetchMock = createRoutedFetchMock(
      (url, method) => {
        if (url.endsWith('/api/chat/sessions') && method === 'GET') {
          return jsonResponse([])
        }
        if (url.endsWith('/api/chat') && method === 'POST') {
          return new Response(ndjsonBody, {
            status: 200,
            headers: { 'Content-Type': 'application/x-ndjson' },
          })
        }
        throw new Error(`Unexpected fetch: ${method} ${url}`)
      },
      { chatStreamingEnabled: false, toolsEnabled: false, ragEnabled: true },
    )
    vi.stubGlobal('fetch', fetchMock)

    renderWithProviders(<ChatPage />)

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled()
    })

    const user = userEvent.setup()
    await user.click(screen.getByRole('checkbox', { name: 'My documents' }))
    await user.type(screen.getByPlaceholderText('Ask something…'), 'What is in my docs?')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => {
      expect(screen.getByText('From your documents: fixture content.')).not.toBeNull()
    })

    const chatCall = fetchMock.mock.calls.find(([input, init]) => {
      const url = typeof input === 'string' ? input : input.toString()
      return url.endsWith('/api/chat') && (init?.method ?? 'GET') === 'POST'
    })
    expect(chatCall).toBeTruthy()
    const chatInit = chatCall?.[1] as RequestInit
    expect((chatInit.headers as Record<string, string>).Accept).toBe('application/x-ndjson')
    const body = JSON.parse(String(chatInit.body)) as { use_documents?: boolean }
    expect(body.use_documents).toBe(true)
  })

  it('deleting a saved session refreshes the list but keeps the active transcript', async () => {
    let secondSessionDeleted = false
    const fetchMock = createRoutedFetchMock((url, method) => {
      if (url.endsWith('/api/chat/sessions') && method === 'GET') {
        return jsonResponse(
          secondSessionDeleted
            ? [
                {
                  id: 's1',
                  title: 'First chat',
                  last_message_at: '2026-01-02T00:00:00Z',
                  created_at: '2026-01-01T00:00:00Z',
                },
              ]
            : [
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
              ],
        )
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
              content: 'Active chat message',
              provider: null,
              model: null,
              status: 'complete',
              finish_reason: null,
              created_at: '2026-01-01T00:00:00Z',
            },
          ],
        })
      }
      if (url.endsWith('/api/chat/sessions/s2') && method === 'GET') {
        return jsonResponse({
          id: 's2',
          title: 'Second chat',
          last_message_at: null,
          messages: [
            {
              id: 'm2',
              seq: 1,
              role: 'user',
              content: 'Second chat message',
              provider: null,
              model: null,
              status: 'complete',
              finish_reason: null,
              created_at: '2026-01-01T00:00:00Z',
            },
          ],
        })
      }
      if (url.endsWith('/api/chat/sessions/s2') && method === 'DELETE') {
        secondSessionDeleted = true
        return new Response(null, { status: 204 })
      }
      throw new Error(`Unexpected fetch: ${method} ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderWithProviders(<ChatPage />)

    await waitFor(() => {
      expect(screen.getByText('Active chat message')).not.toBeNull()
    })

    await userEvent.setup().click(screen.getByRole('button', { name: 'Delete Second chat' }))
    await userEvent
      .setup()
      .click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }))

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([input, init]) => {
          const url = typeof input === 'string' ? input : input.toString()
          return url.endsWith('/api/chat/sessions/s2') && init?.method === 'DELETE'
        }),
      ).toBe(true)
      expect(screen.getByText('Active chat message')).not.toBeNull()
    })

    const s1DetailCalls = fetchMock.mock.calls.filter(([input, init]) => {
      const url = typeof input === 'string' ? input : input.toString()
      return url.endsWith('/api/chat/sessions/s1') && (init?.method ?? 'GET') === 'GET'
    })
    // Mount auto-resume loads s1 once; deleting non-active s2 must not re-fetch s1.
    expect(s1DetailCalls).toHaveLength(1)
  })

  it('canceling delete confirmation does not call DELETE', async () => {
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
      throw new Error(`Unexpected fetch: ${method} ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderWithProviders(<ChatPage />)

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Delete Second chat' })).not.toBeNull()
    })

    await userEvent.setup().click(screen.getByRole('button', { name: 'Delete Second chat' }))
    await userEvent.setup().click(screen.getByRole('button', { name: 'Cancel' }))

    const deleteCalls = fetchMock.mock.calls.filter(([, init]) => init?.method === 'DELETE')
    expect(deleteCalls).toHaveLength(0)
  })

  it('deleting the active session loads the next remaining session transcript', async () => {
    let firstSessionDeleted = false
    const fetchMock = createRoutedFetchMock((url, method) => {
      if (url.endsWith('/api/chat/sessions') && method === 'GET') {
        return jsonResponse(
          firstSessionDeleted
            ? [
                {
                  id: 's2',
                  title: 'Second chat',
                  last_message_at: '2026-01-01T00:00:00Z',
                  created_at: '2025-12-01T00:00:00Z',
                },
              ]
            : [
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
              ],
        )
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
              content: 'Active first chat message',
              provider: null,
              model: null,
              status: 'complete',
              finish_reason: null,
              created_at: '2026-01-01T00:00:00Z',
            },
          ],
        })
      }
      if (url.endsWith('/api/chat/sessions/s2') && method === 'GET') {
        return jsonResponse({
          id: 's2',
          title: 'Second chat',
          last_message_at: null,
          messages: [
            {
              id: 'm2',
              seq: 1,
              role: 'user',
              content: 'Fallback second chat message',
              provider: null,
              model: null,
              status: 'complete',
              finish_reason: null,
              created_at: '2026-01-01T00:00:00Z',
            },
          ],
        })
      }
      if (url.endsWith('/api/chat/sessions/s1') && method === 'DELETE') {
        firstSessionDeleted = true
        return new Response(null, { status: 204 })
      }
      throw new Error(`Unexpected fetch: ${method} ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderWithProviders(<ChatPage />)

    await waitFor(() => {
      expect(screen.getByText('Active first chat message')).not.toBeNull()
    })

    await userEvent.setup().click(screen.getByRole('button', { name: 'Delete First chat' }))
    await userEvent
      .setup()
      .click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }))

    await waitFor(() => {
      expect(screen.getByText('Fallback second chat message')).not.toBeNull()
      expect(screen.queryByText('Active first chat message')).toBeNull()
    })
  })

  it('deleting the last remaining active session creates a new empty session', async () => {
    let onlySessionDeleted = false
    let createdSessionExists = false
    const fetchMock = createRoutedFetchMock((url, method) => {
      if (url.endsWith('/api/chat/sessions') && method === 'GET') {
        if (createdSessionExists) {
          return jsonResponse([
            {
              id: 'new-after-delete',
              title: null,
              last_message_at: null,
              created_at: '2026-02-01T00:00:00Z',
            },
          ])
        }
        if (onlySessionDeleted) {
          return jsonResponse([])
        }
        return jsonResponse([
          {
            id: 'only',
            title: 'Only chat',
            last_message_at: null,
            created_at: '2026-01-01T00:00:00Z',
          },
        ])
      }
      if (url.endsWith('/api/chat/sessions/only') && method === 'GET') {
        return jsonResponse({
          id: 'only',
          title: 'Only chat',
          last_message_at: null,
          messages: [
            {
              id: 'm1',
              seq: 1,
              role: 'user',
              content: 'Only session message',
              provider: null,
              model: null,
              status: 'complete',
              finish_reason: null,
              created_at: '2026-01-01T00:00:00Z',
            },
          ],
        })
      }
      if (url.endsWith('/api/chat/sessions/only') && method === 'DELETE') {
        onlySessionDeleted = true
        return new Response(null, { status: 204 })
      }
      if (url.endsWith('/api/chat/sessions') && method === 'POST') {
        createdSessionExists = true
        return jsonResponse(
          { id: 'new-after-delete', title: null, last_message_at: null, messages: [] },
          201,
        )
      }
      throw new Error(`Unexpected fetch: ${method} ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderWithProviders(<ChatPage />)

    await waitFor(() => {
      expect(screen.getByText('Only session message')).not.toBeNull()
    })

    await userEvent.setup().click(screen.getByRole('button', { name: 'Delete Only chat' }))
    await userEvent
      .setup()
      .click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }))

    await waitFor(() => {
      const postCalls = fetchMock.mock.calls.filter(
        ([, init]) => (init as RequestInit | undefined)?.method === 'POST',
      )
      expect(postCalls.length).toBe(1)
      expect(screen.getByText('Start a conversation to build your first session.')).not.toBeNull()
    })
  })

  it('deleting a non-active saved session keeps the active transcript visible', async () => {
    let sessionsAfterDelete = false
    const fetchMock = createRoutedFetchMock((url, method) => {
      if (url.endsWith('/api/chat/sessions') && method === 'GET') {
        return jsonResponse(
          sessionsAfterDelete
            ? [
                {
                  id: 's1',
                  title: 'First chat',
                  last_message_at: '2026-01-02T00:00:00Z',
                  created_at: '2026-01-01T00:00:00Z',
                },
              ]
            : [
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
              ],
        )
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
              content: 'First chat message',
              provider: null,
              model: null,
              status: 'complete',
              finish_reason: null,
              created_at: '2026-01-01T00:00:00Z',
            },
          ],
        })
      }
      if (url.endsWith('/api/chat/sessions/s2') && method === 'DELETE') {
        sessionsAfterDelete = true
        return new Response(null, { status: 204 })
      }
      throw new Error(`Unexpected fetch: ${method} ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderWithProviders(<ChatPage />)

    await waitFor(() => {
      expect(screen.getByText('First chat message')).not.toBeNull()
    })

    await userEvent.setup().click(screen.getByRole('button', { name: 'Delete Second chat' }))
    await userEvent
      .setup()
      .click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }))

    await waitFor(() => {
      expect(screen.getByText('First chat message')).not.toBeNull()
      expect(screen.queryByRole('button', { name: 'Delete Second chat' })).toBeNull()
    })
  })
})
