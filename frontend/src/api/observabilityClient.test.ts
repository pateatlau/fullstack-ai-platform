/* @vitest-environment jsdom */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchUsageSummary, ObservabilityApiError } from './observabilityClient'
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

const sampleSummary = {
  since: '2026-07-01',
  until: '2026-07-31',
  group_by: 'day',
  rows: [
    {
      day: '2026-07-15',
      provider: null,
      model: null,
      request_count: 3,
      prompt_tokens: 120,
      completion_tokens: 45,
      total_tokens: 165,
      cost_usd: 0.0025,
    },
  ],
}

describe('observabilityClient Authorization header', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('fetchUsageSummary sends Bearer token and query params', async () => {
    storeSession('obs-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(sampleSummary))
    vi.stubGlobal('fetch', fetchMock)

    const result = await fetchUsageSummary({
      since: '2026-07-01',
      until: '2026-07-31',
      group_by: 'provider',
    })

    expect(result.rows).toHaveLength(1)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/observability/usage?since=2026-07-01&until=2026-07-31&group_by=provider',
      expect.objectContaining({
        method: 'GET',
        headers: expect.objectContaining({ Authorization: 'Bearer obs-jwt' }),
      }),
    )
  })

  it('fetchUsageSummary omits query string when params are empty', async () => {
    storeSession('obs-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(sampleSummary))
    vi.stubGlobal('fetch', fetchMock)

    await fetchUsageSummary()

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/observability/usage',
      expect.objectContaining({ method: 'GET' }),
    )
  })

  it('fetchUsageSummary throws ObservabilityApiError on feature_disabled', async () => {
    storeSession('obs-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(
        {
          error: {
            code: 'feature_disabled',
            message: 'Observability is not enabled on this server.',
          },
        },
        503,
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchUsageSummary()).rejects.toMatchObject({
      name: 'ObservabilityApiError',
      code: 'feature_disabled',
      status: 503,
    } satisfies Partial<ObservabilityApiError>)
  })
})
