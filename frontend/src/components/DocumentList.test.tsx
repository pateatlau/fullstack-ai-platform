/* @vitest-environment jsdom */

import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { storeSession } from '../auth/tokenStorage'
import { DocumentList } from './DocumentList'
import type { AuthenticatedUser } from '../types/auth'

const user: AuthenticatedUser = {
  id: 'user-1',
  email: 'person@example.com',
  display_name: 'Person',
  picture_url: null,
}

describe('DocumentList delete', () => {
  beforeEach(() => {
    window.localStorage.clear()
    storeSession('doc-jwt', user)
  })

  afterEach(() => {
    cleanup()
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('calls delete client and refreshes list', async () => {
    const onChanged = vi.fn()
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)

    render(
      <DocumentList
        documents={[
          {
            id: 'doc-1',
            filename: 'notes.txt',
            mime_type: 'text/plain',
            status: 'ready',
            created_at: '2026-01-01T00:00:00Z',
            updated_at: '2026-01-01T00:00:00Z',
          },
        ]}
        isLoading={false}
        onChanged={onChanged}
      />,
    )

    await userEvent.click(screen.getByRole('button', { name: /Delete notes\.txt/i }))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled()
      expect(onChanged).toHaveBeenCalled()
    })
  })
})
