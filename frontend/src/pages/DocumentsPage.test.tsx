/* @vitest-environment jsdom */

import { cleanup, screen } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ProtectedRoute } from '../components/ProtectedRoute'
import { ChatPage } from './ChatPage'
import { DocumentsPage } from './DocumentsPage'
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

function renderDocumentsRoute(initialRoute: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/" element={<ChatPage />} />
      <Route
        path="/documents"
        element={
          <ProtectedRoute>
            <DocumentsPage />
          </ProtectedRoute>
        }
      />
    </Routes>,
    { initialRoute, withChatProvider: true },
  )
}

describe('DocumentsPage guest redirect', () => {
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

  it('redirects guests to chat instead of showing a login prompt', () => {
    renderDocumentsRoute('/documents')

    expect(screen.getByPlaceholderText('Ask something…')).toBeTruthy()
    expect(screen.queryByText(/Sign in to continue/i)).toBeNull()
    expect(screen.queryByLabelText(/Choose a document file/i)).toBeNull()
  })
})

describe('DocumentsPage authenticated layout', () => {
  afterEach(() => {
    cleanup()
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('renders upload, list, and ask sections for authenticated users', async () => {
    storeSession(makeJwt(3600), user)
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ documents: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    renderWithProviders(
      <Routes>
        <Route
          path="/documents"
          element={
            <ProtectedRoute>
              <DocumentsPage />
            </ProtectedRoute>
          }
        />
      </Routes>,
      { initialRoute: '/documents' },
    )

    expect(await screen.findByRole('heading', { name: 'Upload document' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Your documents' })).toBeTruthy()
    expect(screen.getByText(/Ask about your documents in chat/i)).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Go to chat' })).toBeTruthy()
  })

  it('calls handleInvalidAccessToken when listDocuments returns invalid_access_token', async () => {
    storeSession(makeJwt(3600), user)
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: 'invalid_access_token',
            message: 'The provided access token is invalid or expired.',
          },
        }),
        {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    renderDocumentsRoute('/documents')

    expect(await screen.findByPlaceholderText('Ask something…')).toBeTruthy()
    expect(screen.getByText(/Your session expired/i)).toBeTruthy()
    expect(fetchMock).toHaveBeenCalled()
  })
})

describe('AppNav link visibility', () => {
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

  it('hides Documents nav link for guests on chat page', () => {
    renderWithProviders(<ChatPage />)

    expect(screen.queryByRole('link', { name: 'Documents' })).toBeNull()
  })

  it('shows Documents nav link for authenticated users on chat page', () => {
    storeSession(makeJwt(3600), user)
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    renderWithProviders(<ChatPage />)

    expect(screen.getByRole('link', { name: 'Documents' })).toBeTruthy()
  })
})
