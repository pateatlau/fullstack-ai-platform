/* @vitest-environment jsdom */

import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ProtectedRoute } from '../components/ProtectedRoute'
import { ChatPage } from './ChatPage'
import { MemorySettingsPage } from './MemorySettingsPage'
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

const sampleMemoryRecord = {
  id: 'mem-1',
  title: 'Favorite language',
  content: 'User prefers TypeScript.',
  memory_type: 'user',
  session_id: null,
  created_at: '2026-08-01T10:00:00.000Z',
  updated_at: '2026-08-01T10:00:00.000Z',
}

function createMemoryFetchMock(options?: { memoryEnabled?: boolean }) {
  const memoryEnabled = options?.memoryEnabled ?? true
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    const method = init?.method ?? 'GET'

    if (url.endsWith('/api/health') && method === 'GET') {
      return jsonHealthResponse(true, false, false, false, memoryEnabled)
    }

    if (url.endsWith('/api/chat/sessions') && method === 'GET') {
      return jsonResponse([
        {
          id: 'sess-1',
          title: 'Project chat',
          last_message_at: '2026-08-01T12:00:00.000Z',
          created_at: '2026-08-01T11:00:00.000Z',
        },
      ])
    }

    if (url.includes('/api/memory/records?memory_type=user') && method === 'GET') {
      return jsonResponse({ records: [sampleMemoryRecord] })
    }

    if (url.includes('/api/memory/records?memory_type=project') && method === 'GET') {
      return jsonResponse({ records: [] })
    }

    if (url.endsWith('/api/memory/preferences') && method === 'GET') {
      return jsonResponse({
        preferences: [{ key: 'tone', value: { style: 'concise' } }],
      })
    }

    if (url.includes('/api/memory/preferences/') && method === 'DELETE') {
      return new Response(null, { status: 204 })
    }

    if (url.includes('/api/memory/records/') && method === 'DELETE') {
      return new Response(null, { status: 204 })
    }

    if (url.includes('/api/memory/sessions/') && url.endsWith('/summary') && method === 'DELETE') {
      return new Response(null, { status: 204 })
    }

    if (url.includes('/api/memory/preferences/') && method === 'PUT') {
      return jsonResponse({ key: 'response_style', value: { tone: 'friendly' } })
    }

    if (url.endsWith('/api/chat/sessions') && method === 'POST') {
      return jsonResponse({ id: 'new', title: null, last_message_at: null, messages: [] })
    }

    return jsonResponse([])
  })
}

function renderMemoryRoute(initialRoute = '/settings/memory') {
  return renderWithProviders(
    <Routes>
      <Route path="/" element={<ChatPage />} />
      <Route
        path="/settings/memory"
        element={
          <ProtectedRoute>
            <MemorySettingsPage />
          </ProtectedRoute>
        }
      />
    </Routes>,
    { initialRoute, withChatProvider: true },
  )
}

describe('MemorySettingsPage guest redirect', () => {
  beforeEach(() => {
    Object.defineProperty(globalThis.HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    })
  })

  afterEach(() => {
    cleanup()
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('redirects guests to chat instead of showing memory controls', () => {
    renderMemoryRoute('/settings/memory')

    expect(screen.getByPlaceholderText('Ask something…')).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Memory' })).toBeNull()
  })
})

describe('MemorySettingsPage authenticated', () => {
  beforeEach(() => {
    Object.defineProperty(globalThis.HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    })
    storeSession(makeJwt(3600), user)
  })

  afterEach(() => {
    cleanup()
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('renders memory sections when memory_enabled is true', async () => {
    vi.stubGlobal('fetch', createMemoryFetchMock({ memoryEnabled: true }))

    renderMemoryRoute()

    expect(await screen.findByRole('heading', { name: 'Memory status' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'User preferences' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Long-term memories' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Project memories' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Conversation summaries' })).toBeTruthy()
    expect(screen.getByText('Favorite language')).toBeTruthy()
    expect(screen.getByText('tone')).toBeTruthy()
  })

  it('shows unavailable notice when memory_enabled is false', async () => {
    vi.stubGlobal('fetch', createMemoryFetchMock({ memoryEnabled: false }))

    renderMemoryRoute()

    expect(await screen.findByText('Memory is not available')).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'User preferences' })).toBeNull()
  })

  it('deletes a memory record and refreshes the list', async () => {
    const fetchMock = createMemoryFetchMock()
    vi.stubGlobal('fetch', fetchMock)

    renderMemoryRoute()
    expect(await screen.findByText('Favorite language')).toBeTruthy()

    const deleteButton = screen.getByRole('button', { name: /Delete memory Favorite language/i })
    await userEvent.click(deleteButton)

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url, init]) => {
          const resolvedUrl = typeof url === 'string' ? url : url.toString()
          return resolvedUrl.includes('/api/memory/records/mem-1') && init?.method === 'DELETE'
        }),
      ).toBe(true)
    })

    expect(await screen.findByText('Memory deleted.')).toBeTruthy()
  })

  it('validates preference key before save', async () => {
    vi.stubGlobal('fetch', createMemoryFetchMock())

    renderMemoryRoute()
    expect(await screen.findByRole('heading', { name: 'User preferences' })).toBeTruthy()

    await userEvent.type(screen.getByLabelText('Key'), 'INVALID KEY')
    await userEvent.click(screen.getByRole('button', { name: 'Save preference' }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/lowercase letters/i)
  })

  it('confirms before clearing a conversation summary', async () => {
    const fetchMock = createMemoryFetchMock()
    vi.stubGlobal('fetch', fetchMock)

    renderMemoryRoute()
    expect(await screen.findByRole('heading', { name: 'Conversation summaries' })).toBeTruthy()

    await userEvent.click(screen.getByRole('button', { name: /Clear summary for Project chat/i }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/Clear conversation summary/i)).toBeTruthy()

    await userEvent.click(within(dialog).getByRole('button', { name: 'Clear summary' }))

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url, init]) => {
          const resolvedUrl = typeof url === 'string' ? url : url.toString()
          return (
            resolvedUrl.includes('/api/memory/sessions/sess-1/summary') && init?.method === 'DELETE'
          )
        }),
      ).toBe(true)
    })
  })

  it('removes a preference when delete is clicked', async () => {
    const fetchMock = createMemoryFetchMock()
    vi.stubGlobal('fetch', fetchMock)

    renderMemoryRoute()
    expect(await screen.findByText('tone')).toBeTruthy()

    await userEvent.click(screen.getByRole('button', { name: 'Remove preference tone' }))

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url, init]) => {
          const resolvedUrl = typeof url === 'string' ? url : url.toString()
          return resolvedUrl.includes('/api/memory/preferences/tone') && init?.method === 'DELETE'
        }),
      ).toBe(true)
    })
  })
})

describe('AppNav memory link visibility', () => {
  beforeEach(() => {
    Object.defineProperty(globalThis.HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    })
  })

  afterEach(() => {
    cleanup()
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('hides Memory nav link when memory_enabled is false', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal('fetch', createMemoryFetchMock({ memoryEnabled: false }))

    renderWithProviders(<ChatPage />, { withChatProvider: true })

    await waitFor(() => {
      expect(screen.queryByRole('link', { name: 'Memory' })).toBeNull()
    })
  })

  it('shows Memory nav link when memory_enabled is true', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal('fetch', createMemoryFetchMock({ memoryEnabled: true }))

    renderWithProviders(<ChatPage />, { withChatProvider: true })

    const memoryLink = await screen.findByRole('link', { name: 'Memory' })
    expect(memoryLink.getAttribute('href')).toBe('/settings/memory')
  })
})
