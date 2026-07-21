/* @vitest-environment jsdom */

import { cleanup, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
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

describe('DocumentsPage guest gate', () => {
  afterEach(() => {
    cleanup()
    window.localStorage.clear()
  })

  it('shows login prompt and hides upload UI for guests', () => {
    renderWithProviders(<DocumentsPage />, { initialRoute: '/documents' })

    expect(screen.getByText(/Sign in to continue/i)).toBeTruthy()
    expect(screen.getByText(/Documents & Knowledge Base/i)).toBeTruthy()
    expect(screen.queryByLabelText(/Choose a document file/i)).toBeNull()
    expect(screen.queryByText(/Ask your documents/i)).toBeNull()
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

    renderWithProviders(<DocumentsPage />, { initialRoute: '/documents' })

    expect(await screen.findByRole('heading', { name: 'Upload document' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Your documents' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Ask your documents' })).toBeTruthy()
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
