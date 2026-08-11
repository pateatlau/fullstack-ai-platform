/* @vitest-environment jsdom */

import { cleanup, screen, waitFor, within } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ProtectedRoute } from '../components/ProtectedRoute'
import { storeSession } from '../auth/tokenStorage'
import { renderWithProviders } from '../test/renderWithProviders'
import { jsonHealthResponse } from '../test/chatFetchStubs'
import type { AuthenticatedUser } from '../types/auth'
import { ChatPage } from './ChatPage'
import { PluginsPage } from './PluginsPage'

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

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
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
    {
      plugin_id: null,
      name: null,
      version: null,
      api_version: null,
      status: 'failed',
      contributions: [],
      load_duration_ms: 3.2,
      failure: {
        code: 'unsupported_api_version',
        message: 'Plugin API version is not supported.',
        expected_api_versions: ['1'],
        manifest_api_version: '99',
      },
    },
  ],
}

function createPluginsFetchMock(options?: {
  pluginsEnabled?: boolean
  inventory?: typeof sampleInventory
  inventoryError?: { status: number; code: string; message: string }
}) {
  const pluginsEnabled = options?.pluginsEnabled ?? true
  const inventory = options?.inventory ?? sampleInventory

  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    const method = init?.method ?? 'GET'

    if (url.endsWith('/api/health') && method === 'GET') {
      return jsonHealthResponse(true, false, false, false, false, false, false, pluginsEnabled)
    }

    if (url.endsWith('/api/plugins') && method === 'GET') {
      if (options?.inventoryError) {
        const { status, code, message } = options.inventoryError
        return jsonResponse({ error: { code, message } }, status)
      }
      return jsonResponse(inventory)
    }

    if (url.endsWith('/api/chat/sessions') && method === 'GET') {
      return jsonResponse([])
    }

    return jsonResponse([])
  })
}

function renderPluginsRoute(initialRoute = '/plugins') {
  return renderWithProviders(
    <Routes>
      <Route path="/" element={<ChatPage />} />
      <Route
        path="/plugins"
        element={
          <ProtectedRoute>
            <PluginsPage />
          </ProtectedRoute>
        }
      />
    </Routes>,
    { initialRoute },
  )
}

describe('PluginsPage', () => {
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

  it('shows unavailable notice when plugins_enabled is false', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal('fetch', createPluginsFetchMock({ pluginsEnabled: false }))

    renderPluginsRoute()

    expect(await screen.findByRole('heading', { name: 'Plugins are not available' })).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Loaded plugins' })).toBeNull()
  })

  it('shows unavailable notice when plugins API returns feature_disabled', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal(
      'fetch',
      createPluginsFetchMock({
        pluginsEnabled: true,
        inventoryError: {
          status: 503,
          code: 'feature_disabled',
          message: 'Plugins are not enabled on this server.',
        },
      }),
    )

    renderPluginsRoute()

    expect(await screen.findByRole('heading', { name: 'Plugins are not available' })).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'No plugins loaded' })).toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('renders plugin inventory table', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal('fetch', createPluginsFetchMock({ pluginsEnabled: true }))

    renderPluginsRoute()

    const listHeading = await screen.findByRole('heading', { name: 'Loaded plugins' })
    const listRegion = listHeading.closest('section')
    expect(listRegion).toBeTruthy()

    await waitFor(() => {
      const table = within(listRegion as HTMLElement).getByRole('table')
      expect(within(table).getByText('Echo Reference Plugin')).toBeTruthy()
      expect(within(table).getByText('com.example.echo')).toBeTruthy()
      expect(within(table).getByText('Example Corp')).toBeTruthy()
      expect(within(table).getAllByText('Unknown plugin')).toHaveLength(2)
      expect(within(table).getByText(/unsupported_api_version/)).toBeTruthy()
    })
  })

  it('shows empty state when no plugins are returned', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal(
      'fetch',
      createPluginsFetchMock({
        pluginsEnabled: true,
        inventory: { plugins: [] },
      }),
    )

    renderPluginsRoute()

    expect(await screen.findByRole('heading', { name: 'No plugins loaded' })).toBeTruthy()
  })

  it('shows API error banner on failure', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal(
      'fetch',
      createPluginsFetchMock({
        pluginsEnabled: true,
        inventoryError: {
          status: 500,
          code: 'internal_error',
          message: 'Unable to load plugin inventory.',
        },
      }),
    )

    renderPluginsRoute()

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('Unable to load plugin inventory.')
  })
})

describe('AppNav plugins link visibility', () => {
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

  it('hides Plugins nav link when plugins_enabled is false', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal('fetch', createPluginsFetchMock({ pluginsEnabled: false }))

    renderWithProviders(<ChatPage />, { withChatProvider: true })

    await waitFor(() => {
      expect(screen.queryByRole('link', { name: 'Plugins' })).toBeNull()
    })
  })

  it('shows Plugins nav link when plugins_enabled is true', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal('fetch', createPluginsFetchMock({ pluginsEnabled: true }))

    renderWithProviders(<ChatPage />, { withChatProvider: true })

    const link = await screen.findByRole('link', { name: 'Plugins' })
    expect(link.getAttribute('href')).toBe('/plugins')
  })
})

describe('PluginsPage accessibility', () => {
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

  it('exposes labelled sections and table semantics', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal('fetch', createPluginsFetchMock({ pluginsEnabled: true }))

    renderPluginsRoute()

    expect(await screen.findByRole('heading', { name: 'Plugin inventory' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Loaded plugins' })).toBeTruthy()
    await waitFor(() => {
      expect(screen.getByRole('table')).toBeTruthy()
    })
  })
})
