/* @vitest-environment jsdom */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { deleteDocument, listDocuments, uploadDocument } from './documentsClient'
import { getLastRequestId, REQUEST_ID_HEADER, setRetryRequestId } from './request'
import { storeSession } from '../auth/tokenStorage'
import type { AuthenticatedUser } from '../types/auth'

const user: AuthenticatedUser = {
  id: 'user-1',
  email: 'person@example.com',
  display_name: 'Person',
  picture_url: null,
}

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

describe('documentsClient Authorization header', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('uploadDocument sends multipart FormData with Bearer token', async () => {
    storeSession('stored-jwt', user)
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ document_id: 'doc-1', status: 'ready' }))
    vi.stubGlobal('fetch', fetchMock)

    const file = new File(['hello'], 'notes.txt', { type: 'text/plain' })
    await uploadDocument(file)

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toContain('/api/documents/upload')
    expect(init.method).toBe('POST')
    const headers = init.headers as Record<string, string>
    expect(headers.Authorization).toBe('Bearer stored-jwt')
    expect(headers['Content-Type']).toBeUndefined()
    expect(init.body).toBeInstanceOf(FormData)
  })

  it('listDocuments and deleteDocument attach Bearer token', async () => {
    storeSession('list-jwt', user)
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ documents: [] }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)

    await listDocuments()
    await deleteDocument('doc-1')

    const listHeaders = fetchMock.mock.calls[0][1].headers as Record<string, string>
    const deleteHeaders = fetchMock.mock.calls[1][1].headers as Record<string, string>
    expect(listHeaders.Authorization).toBe('Bearer list-jwt')
    expect(deleteHeaders.Authorization).toBe('Bearer list-jwt')
  })

  it('surfaces 401 errors', async () => {
    storeSession('expired-jwt', user)
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ error: { code: 'unauthorized', message: 'Unauthorized' } }, 401),
      )
    vi.stubGlobal('fetch', fetchMock)

    await expect(listDocuments()).rejects.toMatchObject({ status: 401 })
  })

  it('surfaces 413 on oversized upload', async () => {
    storeSession('stored-jwt', user)
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ error: { code: 'payload_too_large', message: 'Too large' } }, 413),
      )
    vi.stubGlobal('fetch', fetchMock)

    const file = new File(['x'], 'big.pdf', { type: 'application/pdf' })
    await expect(uploadDocument(file)).rejects.toMatchObject({ status: 413 })
  })

  it('captures X-Request-ID from responses', async () => {
    storeSession('stored-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ documents: [] }, 200, {
        [REQUEST_ID_HEADER]: '6ba7b810-9dad-11d1-80b4-00c04fd430c8',
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await listDocuments()
    expect(getLastRequestId()).toBe('6ba7b810-9dad-11d1-80b4-00c04fd430c8')

    setRetryRequestId(getLastRequestId())
    fetchMock.mockResolvedValueOnce(jsonResponse({ documents: [] }))
    await listDocuments()

    const retryHeaders = fetchMock.mock.calls[1][1].headers as Record<string, string>
    expect(retryHeaders[REQUEST_ID_HEADER]).toBe('6ba7b810-9dad-11d1-80b4-00c04fd430c8')
  })

  it('deleteDocument treats 404 as already deleted', async () => {
    storeSession('stored-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 404 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(deleteDocument('missing')).resolves.toBeUndefined()
  })
})
