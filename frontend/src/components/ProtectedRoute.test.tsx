/* @vitest-environment jsdom */

import { cleanup, screen } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ProtectedRoute } from './ProtectedRoute'
import { ChatPage } from '../pages/ChatPage'
import { DocumentsPage } from '../pages/DocumentsPage'
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

function renderAppRoutes(initialRoute: string) {
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

describe('ProtectedRoute', () => {
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

  it('redirects guests from /documents to chat', () => {
    renderAppRoutes('/documents')

    expect(screen.getByPlaceholderText('Ask something…')).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Upload document' })).toBeNull()
    expect(screen.queryByText(/Sign in to continue/i)).toBeNull()
  })

  it('redirects expired JWT from /documents to chat with session-expired banner', () => {
    storeSession(makeJwt(-3600), user)

    renderAppRoutes('/documents')

    expect(screen.getByPlaceholderText('Ask something…')).toBeTruthy()
    expect(screen.getByText(/Your session expired/i)).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Upload document' })).toBeNull()
  })

  it('renders documents page for authenticated users', async () => {
    storeSession(makeJwt(3600), user)
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ documents: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    renderAppRoutes('/documents')

    expect(await screen.findByRole('heading', { name: 'Upload document' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Your documents' })).toBeTruthy()
  })
})
