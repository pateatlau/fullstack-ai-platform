/* @vitest-environment jsdom */

import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatPage } from './ChatPage'
import { storeSession } from '../auth/tokenStorage'
import { renderWithProviders } from '../test/renderWithProviders'
import type { AuthenticatedUser } from '../types/auth'

function createStreamResponse(chunks: string[]): Response {
  const encoder = new TextEncoder()

  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) {
          controller.enqueue(encoder.encode(chunk))
        }
        controller.close()
      },
    }),
    { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
  )
}

const user: AuthenticatedUser = {
  id: 'user-1',
  email: 'person@example.com',
  display_name: 'Person',
  picture_url: null,
}

// isJwtExpired() fails closed on malformed tokens, so this test needs a real
// (unsigned, but well-formed) JWT shape to pre-seed an authenticated session.
function makeJwt(expSecondsFromNow: number): string {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))
  const exp = Math.floor(Date.now() / 1000) + expSecondsFromNow
  const payload = btoa(JSON.stringify({ exp }))
  return `${header}.${payload}.signature`
}

/** Authenticated `ChatPage` fetches `GET /api/chat/sessions` on mount (Phase 2
 * sidebar wiring). Answers that transparently with an empty list so it never
 * consumes/counts against this test's chat/stream-focused `fetchMock` queue. */
function withSessionsListStub(
  chatFetchMock: (input: RequestInfo | URL, init?: RequestInit) => unknown,
): ReturnType<typeof vi.fn> {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    if (url.endsWith('/api/chat/sessions') && (init?.method ?? 'GET') === 'GET') {
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    return chatFetchMock(input, init)
  })
}

describe('ChatPage session-expiry UX', () => {
  beforeEach(() => {
    Object.defineProperty(globalThis.HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    })
    window.localStorage.clear()
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    window.localStorage.clear()
  })

  it('clears the session and shows a dismissible re-login prompt on an invalid_access_token stream error, and chat keeps working', async () => {
    storeSession(makeJwt(3600), user)

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        createStreamResponse([
          'event: error\ndata: {"type":"error","id":"resp_1","code":"invalid_access_token","message":"The provided access token is invalid or expired.","timestamp":"t0"}\n\n',
        ]),
      )
      .mockResolvedValueOnce(
        createStreamResponse([
          'event: start\ndata: {"type":"start","id":"resp_2","timestamp":"t0"}\n\n',
          'event: delta\ndata: {"type":"delta","id":"resp_2","content":"Still works","timestamp":"t1"}\n\n',
          'event: end\ndata: {"type":"end","id":"resp_2","finish_reason":"stop","timestamp":"t2"}\n\n',
        ]),
      )
    vi.stubGlobal('fetch', withSessionsListStub(fetchMock))

    renderWithProviders(<ChatPage />)

    await waitFor(() => {
      expect(screen.getByText('Person')).not.toBeNull()
    })

    const userEventInstance = userEvent.setup()
    await userEventInstance.type(screen.getByPlaceholderText('Ask something…'), 'Hello')
    await userEventInstance.click(screen.getByRole('button', { name: 'Send' }))

    const banner = await screen.findByRole('status')
    expect(banner.textContent).toContain('Your session expired')

    // No stale/duplicate chat error banner for the same invalid_access_token
    // event — the session-expired banner already communicates it.
    expect(screen.queryByRole('alert')).toBeNull()

    // Reverted to guest UI (no more authenticated user indicator).
    expect(screen.queryByText('Person')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Log out' })).toBeNull()

    await userEventInstance.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(screen.queryByRole('status')).toBeNull()

    // Chat keeps working as guest after the expiry.
    await userEventInstance.type(screen.getByPlaceholderText('Ask something…'), 'Still there?')
    await userEventInstance.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => {
      expect(screen.getByText('Still works')).not.toBeNull()
    })
  })
})
