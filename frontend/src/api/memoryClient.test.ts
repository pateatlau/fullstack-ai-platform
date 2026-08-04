/* @vitest-environment jsdom */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  clearSessionSummary,
  deleteMemoryRecord,
  deletePreference,
  listMemoryRecords,
  listPreferences,
  upsertPreference,
} from './memoryClient'
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

describe('memoryClient Authorization header', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('listMemoryRecords sends Bearer token and memory_type query', async () => {
    storeSession('memory-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ records: [] }))
    vi.stubGlobal('fetch', fetchMock)

    await listMemoryRecords({ memoryType: 'user' })

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toContain('/api/memory/records?memory_type=user')
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer memory-jwt')
  })

  it('listMemoryRecords includes session_id for project memories', async () => {
    storeSession('memory-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ records: [] }))
    vi.stubGlobal('fetch', fetchMock)

    await listMemoryRecords({ memoryType: 'project', sessionId: 'sess-1' })

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toContain('memory_type=project')
    expect(url).toContain('session_id=sess-1')
  })

  it('upsertPreference sends JSON body with Bearer token', async () => {
    storeSession('memory-jwt', user)
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ key: 'tone', value: { style: 'concise' } }))
    vi.stubGlobal('fetch', fetchMock)

    await upsertPreference('tone', { value: { style: 'concise' } })

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toContain('/api/memory/preferences/tone')
    expect(init.method).toBe('PUT')
    expect(init.body).toBe(JSON.stringify({ value: { style: 'concise' } }))
  })

  it('deleteMemoryRecord and deletePreference attach Bearer token', async () => {
    storeSession('memory-jwt', user)
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)

    await deleteMemoryRecord('rec-1')
    await deletePreference('tone')

    expect(fetchMock).toHaveBeenCalledTimes(2)
    const deleteRecordHeaders = fetchMock.mock.calls[0][1].headers as Record<string, string>
    const deletePrefHeaders = fetchMock.mock.calls[1][1].headers as Record<string, string>
    expect(deleteRecordHeaders.Authorization).toBe('Bearer memory-jwt')
    expect(deletePrefHeaders.Authorization).toBe('Bearer memory-jwt')
  })

  it('clearSessionSummary calls DELETE on session summary endpoint', async () => {
    storeSession('memory-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)

    await clearSessionSummary('sess-abc')

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toContain('/api/memory/sessions/sess-abc/summary')
    expect(init.method).toBe('DELETE')
  })

  it('listPreferences surfaces 503 feature_disabled', async () => {
    storeSession('memory-jwt', user)
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse(
          { error: { code: 'feature_disabled', message: 'Memory is not enabled on this server.' } },
          503,
        ),
      )
    vi.stubGlobal('fetch', fetchMock)

    await expect(listPreferences()).rejects.toMatchObject({
      status: 503,
      code: 'feature_disabled',
    })
  })

  it('captures X-Request-ID from responses', async () => {
    storeSession('memory-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ preferences: [] }, 200, {
        [REQUEST_ID_HEADER]: '6ba7b810-9dad-11d1-80b4-00c04fd430c8',
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await listPreferences()
    expect(getLastRequestId()).toBe('6ba7b810-9dad-11d1-80b4-00c04fd430c8')

    setRetryRequestId(getLastRequestId())
    fetchMock.mockResolvedValueOnce(jsonResponse({ preferences: [] }))
    await listPreferences()

    const retryHeaders = fetchMock.mock.calls[1][1].headers as Record<string, string>
    expect(retryHeaders[REQUEST_ID_HEADER]).toBe('6ba7b810-9dad-11d1-80b4-00c04fd430c8')
  })

  it('deleteMemoryRecord treats 404 as already deleted', async () => {
    storeSession('memory-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 404 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(deleteMemoryRecord('missing')).resolves.toBeUndefined()
  })
})
