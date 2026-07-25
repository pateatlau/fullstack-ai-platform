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

describe('RagAskPanel citations', () => {
  beforeEach(() => {
    window.localStorage.clear()
    storeSession('rag-jwt', user)
  })

  afterEach(() => {
    cleanup()
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('keeps retrieved-chunk summary and renders citations when present', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          answer: 'You may work remotely three days per week.',
          retrieved_chunks: [
            {
              chunk_id: '11111111-1111-1111-1111-111111111111',
              document_id: '22222222-2222-2222-2222-222222222222',
              chunk_index: 0,
              score: 0.91,
            },
          ],
          truncated: false,
          model: 'gpt-4o-mini',
          provider: 'openai',
          citations: [
            {
              index: 1,
              chunk_id: '11111111-1111-1111-1111-111111111111',
              document_id: '22222222-2222-2222-2222-222222222222',
              snippet: 'Remote work is allowed three days per week.',
              score: 0.91,
              filename: 'policy.pdf',
              source: null,
              page: 3,
            },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    render(<RagAskPanel />)

    await userEvent.type(
      screen.getByLabelText(/Question about your documents/i),
      'What is the remote work policy?',
    )
    await userEvent.click(screen.getByRole('button', { name: 'Ask' }))

    expect(await screen.findByText('You may work remotely three days per week.')).toBeTruthy()
    expect(screen.getByText('Retrieved 1 chunk from your documents.')).toBeTruthy()
    expect(screen.getByLabelText('Citations')).toBeTruthy()
    expect(screen.getByText('[1] policy.pdf · p. 3')).toBeTruthy()
    expect(screen.getByText('Remote work is allowed three days per week.')).toBeTruthy()
  })

  it('does not render a citation list when citations are null', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          answer: 'Legacy answer.',
          retrieved_chunks: [],
          truncated: false,
          model: 'gpt-4o-mini',
          provider: 'openai',
          citations: null,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    render(<RagAskPanel />)

    await userEvent.type(screen.getByLabelText(/Question about your documents/i), 'Anything?')
    await userEvent.click(screen.getByRole('button', { name: 'Ask' }))

    expect(await screen.findByText('Legacy answer.')).toBeTruthy()
    expect(screen.queryByLabelText('Citations')).toBeNull()
  })
})
