/* @vitest-environment jsdom */

import { cleanup, screen, waitFor } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ProtectedRoute } from '../components/ProtectedRoute'
import { storeSession } from '../auth/tokenStorage'
import { renderWithProviders } from '../test/renderWithProviders'
import { jsonHealthResponse } from '../test/chatFetchStubs'
import type { AuthenticatedUser } from '../types/auth'
import { ApprovalsPage } from './ApprovalsPage'
import { ChatPage } from './ChatPage'

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

const pendingApproval = {
  id: 'approval-1',
  kind: 'agent_tool',
  approval_correlation_id: 'corr-1',
  status: 'pending',
  tool_calls: [{ name: 'send_notification', arguments: { message: 'hi' }, call_id: 'c1' }],
  workflow_run_id: null,
  workflow_node_id: null,
  session_id: 'session-1',
  requested_at: '2026-08-12T00:00:00Z',
  decided_at: null,
  decided_by: null,
  decision: null,
  reason: null,
  edited: false,
  revision_count: 0,
  decide_url: '/api/approvals/approval-1/decide',
}

function createApprovalsFetchMock(options?: { hitlEnabled?: boolean }) {
  const hitlEnabled = options?.hitlEnabled ?? true

  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    const method = init?.method ?? 'GET'

    if (url.endsWith('/api/health') && method === 'GET') {
      return jsonHealthResponse(true, false, false, false, false, false, false, false, hitlEnabled)
    }

    if (url.includes('/api/approvals?') && method === 'GET') {
      if (url.includes('status=pending')) {
        return jsonResponse({ approvals: [pendingApproval], limit: 50, offset: 0, total: 1 })
      }
      return jsonResponse({ approvals: [], limit: 100, offset: 0, total: 0 })
    }

    if (url.endsWith('/api/chat/sessions') && method === 'GET') {
      return jsonResponse([])
    }

    return jsonResponse([])
  })
}

function renderApprovalsRoute() {
  return renderWithProviders(
    <Routes>
      <Route path="/" element={<ChatPage />} />
      <Route
        path="/approvals"
        element={
          <ProtectedRoute>
            <ApprovalsPage />
          </ProtectedRoute>
        }
      />
    </Routes>,
    { initialRoute: '/approvals', withChatProvider: true },
  )
}

describe('ApprovalsPage', () => {
  beforeEach(() => {
    Object.defineProperty(globalThis.HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    })
    storeSession(makeJwt(3600), user)
  })

  afterEach(() => {
    cleanup()
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('shows unavailable notice when HITL is disabled', async () => {
    vi.stubGlobal('fetch', createApprovalsFetchMock({ hitlEnabled: false }))

    renderApprovalsRoute()

    expect(await screen.findByText(/Approvals are not available/i)).toBeTruthy()
  })

  it('lists pending approvals when HITL is enabled', async () => {
    vi.stubGlobal('fetch', createApprovalsFetchMock({ hitlEnabled: true }))

    renderApprovalsRoute()

    await waitFor(() => {
      expect(screen.getByText(/Agent tool call/i)).toBeTruthy()
    })
    expect(screen.getByLabelText(/Proposed tool calls/i)).toBeTruthy()
    expect(screen.getByRole('button', { name: /Approve/i })).toBeTruthy()
  })

  it('handles feature_disabled from approvals API', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString()

      if (url.endsWith('/api/health')) {
        return jsonHealthResponse(true, false, false, false, false, false, false, false, true)
      }

      if (url.includes('/api/approvals')) {
        return jsonResponse(
          {
            error: {
              code: 'feature_disabled',
              message: 'Human-in-the-loop approvals are not enabled on this server.',
            },
          },
          503,
        )
      }

      if (url.endsWith('/api/chat/sessions')) {
        return jsonResponse([])
      }

      return jsonResponse([])
    })
    vi.stubGlobal('fetch', fetchMock)

    renderApprovalsRoute()

    expect(await screen.findByText(/Approvals are not available/i)).toBeTruthy()
  })
})
