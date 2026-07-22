/* @vitest-environment jsdom */

import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatPage } from '../pages/ChatPage'
import { storeSession } from '../auth/tokenStorage'
import { renderWithProviders } from '../test/renderWithProviders'
import { withChatPageFetchStubs } from '../test/chatFetchStubs'
import type { AuthenticatedUser } from '../types/auth'

const authenticatedUser: AuthenticatedUser = {
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

/** Provider/model switching is authenticated-only (plan Section 3.2); seed a
 * valid session so switcher-focused tests exercise that tier by default. */
function signInAsAuthenticatedUser(): void {
  storeSession(makeJwt(3600), authenticatedUser)
}

/** Authenticated `ChatPage` fetches `GET /api/chat/sessions` on mount (Phase 2
 * sidebar wiring). Answers that transparently with an empty list so it never
 * consumes or counts against a test's chat/stream-focused `fetchMock`. */
function withSessionsListStub(
  chatFetchMock: (input: RequestInfo | URL, init?: RequestInit) => unknown,
): ReturnType<typeof vi.fn> {
  return withChatPageFetchStubs(chatFetchMock)
}

function createStreamResponse(chunks: string[], chunkDelayMs = 0): Response {
  const encoder = new TextEncoder()

  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        const pushChunk = (index: number) => {
          if (index >= chunks.length) {
            controller.close()
            return
          }

          controller.enqueue(encoder.encode(chunks[index]))
          setTimeout(() => pushChunk(index + 1), chunkDelayMs)
        }

        pushChunk(0)
      },
    }),
    {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    },
  )
}

function createAbortableStreamResponse(
  chunks: string[],
  chunkDelayMs: number,
  signal?: AbortSignal,
): Response {
  const encoder = new TextEncoder()
  let aborted = signal?.aborted ?? false

  const abort = () => {
    aborted = true
  }

  signal?.addEventListener('abort', abort, { once: true })

  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        const maybePush = (index: number) => {
          if (aborted) {
            controller.close()
            return
          }

          if (index >= chunks.length) {
            controller.close()
            return
          }

          controller.enqueue(encoder.encode(chunks[index]))
          setTimeout(() => maybePush(index + 1), chunkDelayMs)
        }

        maybePush(0)
      },
      cancel() {
        aborted = true
        signal?.removeEventListener('abort', abort)
        return undefined
      },
    }),
    {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    },
  )
}

describe('Composer behavior', () => {
  beforeEach(() => {
    window.localStorage.clear()
    signInAsAuthenticatedUser()
    Object.defineProperty(globalThis.HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    })
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    window.localStorage.clear()
  })

  it('exposes accessible shell landmarks and primary controls', () => {
    renderWithProviders(<ChatPage />)

    expect(screen.getByLabelText('Chat sessions')).not.toBeNull()
    expect(screen.getByLabelText('Conversation')).not.toBeNull()
    expect(screen.getByLabelText('Message thread')).not.toBeNull()
    expect(screen.getByLabelText('Message composer')).not.toBeNull()
    expect(screen.getByRole('button', { name: '+ New chat' })).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Send' })).not.toBeNull()
    expect(screen.getByLabelText('Message input')).not.toBeNull()
    expect(screen.getByLabelText('Provider')).not.toBeNull()
    expect(screen.getByLabelText('Model')).not.toBeNull()
    expect(screen.getByDisplayValue('OpenAI')).not.toBeNull()
    expect(screen.getByDisplayValue('gpt-4o-mini')).not.toBeNull()
    expect(
      screen
        .getByRole('button', { name: 'New chat', current: 'page' })
        .getAttribute('aria-current'),
    ).toBe('page')
  })

  it('collapses provider and model controls behind a mobile summary toggle', async () => {
    renderWithProviders(<ChatPage />)

    const toggle = screen.getByRole('button', { name: /Provider & model/i })
    const settings = screen.getByLabelText('Provider').closest('#provider-model-settings')

    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(settings?.className).toContain('hidden')

    const user = userEvent.setup()
    await user.click(toggle)

    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(settings?.className).not.toContain('hidden')
  })

  it('collapses provider settings again after changing provider on mobile', async () => {
    renderWithProviders(<ChatPage />)

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /Provider & model/i }))

    const toggle = screen.getByRole('button', { name: /Provider & model/i })
    expect(toggle.getAttribute('aria-expanded')).toBe('true')

    await user.selectOptions(screen.getByLabelText('Provider'), 'groq')

    expect(toggle.getAttribute('aria-expanded')).toBe('false')
  })

  it('sends the selected provider and model with the chat request', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        createStreamResponse([
          'event: start\ndata: {"type":"start","id":"resp_3","timestamp":"t0"}\n\n',
          'event: delta\ndata: {"type":"delta","id":"resp_3","content":"Hello","timestamp":"t1"}\n\n',
          'event: end\ndata: {"type":"end","id":"resp_3","finish_reason":"stop","timestamp":"t2"}\n\n',
        ]),
      )
    vi.stubGlobal('fetch', withSessionsListStub(fetchMock))

    renderWithProviders(<ChatPage />)

    const user = userEvent.setup()
    await user.selectOptions(screen.getByLabelText('Provider'), 'groq')
    expect((screen.getByLabelText('Model') as HTMLSelectElement).value).toBe('openai/gpt-oss-20b')
    await user.type(screen.getByPlaceholderText('Ask something…'), 'Use Groq')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1)
    })

    const requestInit = fetchMock.mock.calls[0]?.[1] as RequestInit
    const body = JSON.parse(String(requestInit.body)) as {
      provider?: string
      model?: string
      messages: Array<{ role: string; content: string }>
    }

    expect(body.provider).toBe('groq')
    expect(body.model).toBe('openai/gpt-oss-20b')
    expect(body.messages.at(-1)?.content).toBe('Use Groq')
  })

  it('streams assistant tokens after selecting Anthropic', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        createStreamResponse([
          'event: start\ndata: {"type":"start","id":"resp_4","timestamp":"t0"}\n\n',
          'event: delta\ndata: {"type":"delta","id":"resp_4","content":"Anthropic","timestamp":"t1"}\n\n',
          'event: delta\ndata: {"type":"delta","id":"resp_4","content":" works","timestamp":"t2"}\n\n',
          'event: end\ndata: {"type":"end","id":"resp_4","finish_reason":"stop","timestamp":"t3"}\n\n',
        ]),
      )
    vi.stubGlobal('fetch', withSessionsListStub(fetchMock))

    renderWithProviders(<ChatPage />)

    const user = userEvent.setup()
    await user.selectOptions(screen.getByLabelText('Provider'), 'anthropic')
    await user.type(screen.getByPlaceholderText('Ask something…'), 'Hello Anthropic')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1)
      expect(screen.getByText('Anthropic works')).not.toBeNull()
    })

    const requestInit = fetchMock.mock.calls[0]?.[1] as RequestInit
    const body = JSON.parse(String(requestInit.body)) as {
      provider?: string
      model?: string
    }

    expect(body.provider).toBe('anthropic')
    expect(body.model).toBe('claude-haiku-4-5-20251001')
  })

  it('uses newly selected provider/model on a second send after switching providers', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        createStreamResponse([
          'event: start\ndata: {"type":"start","id":"resp_5","timestamp":"t0"}\n\n',
          'event: delta\ndata: {"type":"delta","id":"resp_5","content":"Groq first","timestamp":"t1"}\n\n',
          'event: end\ndata: {"type":"end","id":"resp_5","finish_reason":"stop","timestamp":"t2"}\n\n',
        ]),
      )
      .mockResolvedValueOnce(
        createStreamResponse([
          'event: start\ndata: {"type":"start","id":"resp_6","timestamp":"t3"}\n\n',
          'event: delta\ndata: {"type":"delta","id":"resp_6","content":"Anthropic second","timestamp":"t4"}\n\n',
          'event: end\ndata: {"type":"end","id":"resp_6","finish_reason":"stop","timestamp":"t5"}\n\n',
        ]),
      )
    vi.stubGlobal('fetch', withSessionsListStub(fetchMock))

    renderWithProviders(<ChatPage />)

    const user = userEvent.setup()

    await user.selectOptions(screen.getByLabelText('Provider'), 'groq')
    await user.type(screen.getByPlaceholderText('Ask something…'), 'First request')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1)
      expect(screen.getByText('Groq first')).not.toBeNull()
    })

    await user.selectOptions(screen.getByLabelText('Provider'), 'anthropic')
    await user.type(screen.getByPlaceholderText('Ask something…'), 'Second request')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2)
      expect(screen.getByText('Anthropic second')).not.toBeNull()
    })

    const firstRequestInit = fetchMock.mock.calls[0]?.[1] as RequestInit
    const firstBody = JSON.parse(String(firstRequestInit.body)) as {
      provider?: string
      model?: string
    }
    expect(firstBody.provider).toBe('groq')
    expect(firstBody.model).toBe('openai/gpt-oss-20b')

    const secondRequestInit = fetchMock.mock.calls[1]?.[1] as RequestInit
    const secondBody = JSON.parse(String(secondRequestInit.body)) as {
      provider?: string
      model?: string
    }
    expect(secondBody.provider).toBe('anthropic')
    expect(secondBody.model).toBe('claude-haiku-4-5-20251001')
  })

  it('streams assistant tokens into the chat page after send', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        createStreamResponse([
          'event: start\ndata: {"type":"start","id":"resp_1","timestamp":"t0"}\n\n',
          'event: delta\ndata: {"type":"delta","id":"resp_1","content":"Fast","timestamp":"t1"}\n\n',
          'event: delta\ndata: {"type":"delta","id":"resp_1","content":"API","timestamp":"t2"}\n\n',
          'event: end\ndata: {"type":"end","id":"resp_1","finish_reason":"stop","timestamp":"t3"}\n\n',
        ]),
      )
    vi.stubGlobal('fetch', withSessionsListStub(fetchMock))

    renderWithProviders(<ChatPage />)

    const user = userEvent.setup()
    expect(screen.getAllByText('Waiting for input').length).toBeGreaterThan(0)
    await user.type(screen.getByPlaceholderText('Ask something…'), 'Hello there')
    expect(screen.getAllByText('Ready to send').length).toBeGreaterThan(0)
    await user.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1)
      expect(screen.getByText('Hello there')).not.toBeNull()
      expect(screen.getByText('FastAPI')).not.toBeNull()
    })
  })

  it('stops the stream and preserves partial content when Stop is pressed', async () => {
    const fetchMock = vi
      .fn()
      .mockImplementation(async (_input: RequestInfo | URL, init?: RequestInit) => {
        return createAbortableStreamResponse(
          [
            'event: start\ndata: {"type":"start","id":"resp_2","timestamp":"t0"}\n\n',
            'event: delta\ndata: {"type":"delta","id":"resp_2","content":"Partial","timestamp":"t1"}\n\n',
            'event: delta\ndata: {"type":"delta","id":"resp_2","content":" answer","timestamp":"t2"}\n\n',
            'event: end\ndata: {"type":"end","id":"resp_2","finish_reason":"stop","timestamp":"t3"}\n\n',
          ],
          120,
          init?.signal ?? undefined,
        )
      })
    vi.stubGlobal('fetch', withSessionsListStub(fetchMock))

    renderWithProviders(<ChatPage />)

    const user = userEvent.setup()
    await user.type(screen.getByPlaceholderText('Ask something…'), 'Stop early')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Stop' })).not.toBeNull()
      expect(screen.getByText('Partial')).not.toBeNull()
    })

    await user.click(screen.getByRole('button', { name: 'Stop' }))

    await waitFor(() => {
      expect(screen.getByText('Stopped.')).not.toBeNull()
    })

    expect(screen.getByText(/Partial/)).not.toBeNull()
  })
})

describe('Composer guest gating and quota UX', () => {
  beforeEach(() => {
    window.localStorage.clear() // stay on the default guest tier
    Object.defineProperty(globalThis.HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    })
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    window.localStorage.clear()
  })

  it('hides the provider/model switcher for guests', () => {
    renderWithProviders(<ChatPage />)

    expect(screen.queryByLabelText('Provider')).toBeNull()
    expect(screen.queryByLabelText('Model')).toBeNull()
  })

  it('hides the + New chat control for guests and shows a login affordance instead', () => {
    renderWithProviders(<ChatPage />)

    expect(screen.queryByRole('button', { name: '+ New chat' })).toBeNull()
    expect(screen.getByText(/sign in above to start additional chats/i)).not.toBeNull()
  })

  it('omits provider/model from the request when sent as a guest', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        createStreamResponse([
          'event: start\ndata: {"type":"start","id":"resp_7","timestamp":"t0"}\n\n',
          'event: delta\ndata: {"type":"delta","id":"resp_7","content":"Default reply","timestamp":"t1"}\n\n',
          'event: end\ndata: {"type":"end","id":"resp_7","finish_reason":"stop","timestamp":"t2"}\n\n',
        ]),
      )
    vi.stubGlobal('fetch', withChatPageFetchStubs(fetchMock))

    renderWithProviders(<ChatPage />)

    const user = userEvent.setup()
    await user.type(screen.getByPlaceholderText('Ask something…'), 'Hi as a guest')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1)
    })

    const requestInit = fetchMock.mock.calls[0]?.[1] as RequestInit
    const body = JSON.parse(String(requestInit.body)) as {
      provider?: string
      model?: string
      client_message_id?: string
    }

    expect(body.provider).toBeUndefined()
    expect(body.model).toBeUndefined()
    expect(body.client_message_id).toBeTruthy()
  })

  it('blocks the composer and shows a login prompt on 429 quota_exceeded', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ error: { code: 'quota_exceeded', message: 'limit reached' } }),
        {
          status: 429,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    )
    vi.stubGlobal('fetch', withChatPageFetchStubs(fetchMock))

    renderWithProviders(<ChatPage />)

    const user = userEvent.setup()
    await user.type(screen.getByPlaceholderText('Ask something…'), 'One too many')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => {
      expect(screen.getByText(/reached today.s guest message limit/i)).not.toBeNull()
    })

    expect((screen.getByPlaceholderText('Ask something…') as HTMLTextAreaElement).disabled).toBe(
      true,
    )
    expect((screen.getByRole('button', { name: 'Send' }) as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('Composer unified chat toggles', () => {
  beforeEach(() => {
    signInAsAuthenticatedUser()
  })

  afterEach(() => {
    cleanup()
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('hides web search and document toggles for guests', () => {
    window.localStorage.clear()
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', withChatPageFetchStubs(fetchMock))

    renderWithProviders(<ChatPage />)

    expect(screen.queryByRole('checkbox', { name: 'Web search' })).toBeNull()
    expect(screen.queryByRole('checkbox', { name: 'My documents' })).toBeNull()
  })

  it('includes toggle fields in the chat request when enabled', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: 'resp_toggle',
          role: 'assistant',
          content: 'ok',
          model: 'gpt-4o-mini',
          provider: 'openai',
          created_at: 't0',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    vi.stubGlobal(
      'fetch',
      withChatPageFetchStubs(fetchMock, {
        chatStreamingEnabled: false,
        toolsEnabled: true,
        ragEnabled: true,
      }),
    )

    renderWithProviders(<ChatPage />)

    const user = userEvent.setup()
    await user.click(screen.getByRole('checkbox', { name: 'Web search' }))
    await user.click(screen.getByRole('checkbox', { name: 'My documents' }))
    await user.type(screen.getByPlaceholderText('Ask something…'), 'Use my docs and search')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled()
    })

    const chatCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/api/chat'))
    expect(chatCall).toBeTruthy()
    const body = JSON.parse(String((chatCall?.[1] as RequestInit).body)) as {
      use_web_search?: boolean
      use_documents?: boolean
    }
    expect(body.use_web_search).toBe(true)
    expect(body.use_documents).toBe(true)
  })
})
