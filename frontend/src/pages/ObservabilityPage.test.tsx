/* @vitest-environment jsdom */

import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ProtectedRoute } from '../components/ProtectedRoute'
import { storeSession } from '../auth/tokenStorage'
import { renderWithProviders } from '../test/renderWithProviders'
import { jsonHealthResponse } from '../test/chatFetchStubs'
import type { AuthenticatedUser } from '../types/auth'
import { ChatPage } from './ChatPage'
import { ObservabilityPage } from './ObservabilityPage'

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

const sampleSummaryByDay = {
  since: '2026-07-01',
  until: '2026-07-31',
  group_by: 'day',
  rows: [
    {
      day: '2026-07-15',
      provider: null,
      model: null,
      request_count: 2,
      prompt_tokens: 100,
      completion_tokens: 50,
      total_tokens: 150,
      cost_usd: 0.0012,
    },
  ],
}

const sampleSummaryByProvider = {
  since: '2026-07-01',
  until: '2026-07-31',
  group_by: 'provider',
  rows: [
    {
      day: null,
      provider: 'openai',
      model: null,
      request_count: 2,
      prompt_tokens: 100,
      completion_tokens: 50,
      total_tokens: 150,
      cost_usd: 0.0012,
    },
  ],
}

const sampleSummaryByModel = {
  since: '2026-07-01',
  until: '2026-07-31',
  group_by: 'model',
  rows: [
    {
      day: null,
      provider: 'openai',
      model: 'gpt-4o-mini',
      request_count: 2,
      prompt_tokens: 100,
      completion_tokens: 50,
      total_tokens: 150,
      cost_usd: 0.0012,
    },
  ],
}

function createObservabilityFetchMock(options?: {
  observabilityEnabled?: boolean
  summary?: typeof sampleSummaryByDay
  summaryError?: { status: number; code: string; message: string }
}) {
  const observabilityEnabled = options?.observabilityEnabled ?? true
  const summary = options?.summary ?? sampleSummaryByDay

  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    const method = init?.method ?? 'GET'

    if (url.endsWith('/api/health') && method === 'GET') {
      return jsonHealthResponse(true, false, false, false, false, false, observabilityEnabled)
    }

    if (url.includes('/api/observability/usage') && method === 'GET') {
      if (options?.summaryError) {
        const { status, code, message } = options.summaryError
        return jsonResponse({ error: { code, message } }, status)
      }

      if (url.includes('group_by=provider')) {
        return jsonResponse(sampleSummaryByProvider)
      }
      if (url.includes('group_by=model')) {
        return jsonResponse(sampleSummaryByModel)
      }
      return jsonResponse(summary)
    }

    if (url.endsWith('/api/chat/sessions') && method === 'GET') {
      return jsonResponse([])
    }

    return jsonResponse([])
  })
}

function renderObservabilityRoute(initialRoute = '/observability') {
  return renderWithProviders(
    <Routes>
      <Route path="/" element={<ChatPage />} />
      <Route
        path="/observability"
        element={
          <ProtectedRoute>
            <ObservabilityPage />
          </ProtectedRoute>
        }
      />
    </Routes>,
    { initialRoute },
  )
}

describe('ObservabilityPage', () => {
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

  it('shows unavailable notice when observability_enabled is false', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal('fetch', createObservabilityFetchMock({ observabilityEnabled: false }))

    renderObservabilityRoute()

    expect(
      await screen.findByRole('heading', { name: 'Observability is not available' }),
    ).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Usage summary' })).toBeNull()
  })

  it('renders usage summary grouped by day', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal('fetch', createObservabilityFetchMock({ observabilityEnabled: true }))

    renderObservabilityRoute()

    const summaryHeading = await screen.findByRole('heading', { name: 'Usage summary' })
    const summaryRegion = summaryHeading.closest('section')
    expect(summaryRegion).toBeTruthy()

    await waitFor(() => {
      const table = within(summaryRegion as HTMLElement).getByRole('table')
      expect(within(table).getByText('2026-07-15')).toBeTruthy()
      const bodyRows = table.querySelectorAll('tbody tr')
      expect(bodyRows.length).toBe(1)
      expect(bodyRows[0]?.textContent).toContain('2')
    })
  })

  it('loads provider grouping when group by changes', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal('fetch', createObservabilityFetchMock({ observabilityEnabled: true }))

    renderObservabilityRoute()
    await screen.findByRole('heading', { name: 'Usage summary' })

    await userEvent.selectOptions(screen.getByLabelText('Group by'), 'provider')

    await waitFor(() => {
      expect(screen.getByText('openai')).toBeTruthy()
    })
  })

  it('shows empty state when no usage rows are returned', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal(
      'fetch',
      createObservabilityFetchMock({
        observabilityEnabled: true,
        summary: { ...sampleSummaryByDay, rows: [] },
      }),
    )

    renderObservabilityRoute()

    expect(await screen.findByRole('heading', { name: 'No usage recorded' })).toBeTruthy()
  })

  it('shows API error banner on failure', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal(
      'fetch',
      createObservabilityFetchMock({
        observabilityEnabled: true,
        summaryError: {
          status: 500,
          code: 'internal_error',
          message: 'Unable to load usage summary.',
        },
      }),
    )

    renderObservabilityRoute()

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('Unable to load usage summary.')
  })
})

describe('AppNav observability link visibility', () => {
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

  it('hides Observability nav link when observability_enabled is false', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal('fetch', createObservabilityFetchMock({ observabilityEnabled: false }))

    renderWithProviders(<ChatPage />, { withChatProvider: true })

    await waitFor(() => {
      expect(screen.queryByRole('link', { name: 'Observability' })).toBeNull()
    })
  })

  it('shows Observability nav link when observability_enabled is true', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal('fetch', createObservabilityFetchMock({ observabilityEnabled: true }))

    renderWithProviders(<ChatPage />, { withChatProvider: true })

    const link = await screen.findByRole('link', { name: 'Observability' })
    expect(link.getAttribute('href')).toBe('/observability')
  })
})

describe('ObservabilityPage accessibility', () => {
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
    vi.stubGlobal('fetch', createObservabilityFetchMock({ observabilityEnabled: true }))

    renderObservabilityRoute()

    expect(await screen.findByRole('heading', { name: 'Observability status' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Usage filters' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Usage summary' })).toBeTruthy()
    await waitFor(() => {
      expect(screen.getByRole('table')).toBeTruthy()
    })
    expect(screen.getByLabelText('From')).toBeTruthy()
    expect(screen.getByLabelText('To')).toBeTruthy()
    expect(screen.getByLabelText('Group by')).toBeTruthy()
  })
})
