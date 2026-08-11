/* @vitest-environment jsdom */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchPluginInventory, PluginsApiError } from './pluginsClient'
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

const sampleInventory = {
  plugins: [
    {
      plugin_id: 'com.example.echo',
      name: 'Echo Reference Plugin',
      version: '1.0.0',
      api_version: '1',
      status: 'loaded',
      contributions: ['tool'],
      load_duration_ms: 12.5,
      author: 'Example Corp',
      homepage: 'https://example.com',
    },
  ],
}

describe('pluginsClient Authorization header', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('fetchPluginInventory sends Bearer token', async () => {
    storeSession('plugins-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(sampleInventory))
    vi.stubGlobal('fetch', fetchMock)

    const result = await fetchPluginInventory()

    expect(result.plugins).toHaveLength(1)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/plugins',
      expect.objectContaining({
        method: 'GET',
        headers: expect.objectContaining({ Authorization: 'Bearer plugins-jwt' }),
      }),
    )
  })

  it('fetchPluginInventory throws PluginsApiError on feature_disabled', async () => {
    storeSession('plugins-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(
        {
          error: {
            code: 'feature_disabled',
            message: 'Plugins are not enabled on this server.',
          },
        },
        503,
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchPluginInventory()).rejects.toMatchObject({
      name: 'PluginsApiError',
      code: 'feature_disabled',
      status: 503,
    } satisfies Partial<PluginsApiError>)
  })
})
