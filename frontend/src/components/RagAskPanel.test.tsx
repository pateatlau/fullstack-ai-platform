/* @vitest-environment jsdom */

import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { storeSession } from '../auth/tokenStorage'
import { RAG_DISABLED_MESSAGE, RAG_FEATURE_DISABLED_CODE } from '../api/ragClient'
import { RagAskPanel } from './RagAskPanel'
import type { AuthenticatedUser } from '../types/auth'

const user: AuthenticatedUser = {
  id: 'user-1',
  email: 'person@example.com',
  display_name: 'Person',
  picture_url: null,
}

describe('RagAskPanel 503 handling', () => {
  beforeEach(() => {
    window.localStorage.clear()
    storeSession('rag-jwt', user)
  })

  afterEach(() => {
    cleanup()
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('shows disabled message when RAG returns feature_disabled', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          error: { code: RAG_FEATURE_DISABLED_CODE, message: 'RAG disabled' },
        }),
        { status: 503, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    render(<RagAskPanel />)

    await userEvent.type(
      screen.getByLabelText(/Question about your documents/i),
      'What is in my docs?',
    )
    await userEvent.click(screen.getByRole('button', { name: 'Ask' }))

    const banner = await screen.findByRole('status')
    expect(banner.textContent).toContain(RAG_DISABLED_MESSAGE)
    expect((screen.getByRole('button', { name: 'Ask' }) as HTMLButtonElement).disabled).toBe(true)
  })
})
