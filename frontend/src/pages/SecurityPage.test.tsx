/* @vitest-environment jsdom */

import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { getStoredAccessToken, storeSession } from '../auth/tokenStorage'
import { AppNav } from '../components/AppNav'
import { ProtectedRoute } from '../components/ProtectedRoute'
import { renderWithProviders } from '../test/renderWithProviders'
import type { AuthenticatedUser } from '../types/auth'
import { SecurityPage } from './SecurityPage'

const currentUser: AuthenticatedUser = {
  id: '11111111-1111-1111-1111-111111111111',
  email: 'admin@example.com',
  display_name: 'Admin User',
  picture_url: null,
}
const targetId = '22222222-2222-2222-2222-222222222222'

function jwt(): string {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))
  const payload = btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 }))
  return `${header}.${payload}.signature`
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function securityFetch(
  options: { enabled?: boolean; roleForbidden?: boolean; tokenInvalid?: boolean } = {},
) {
  let targetRoles = ['member']
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    const method = init?.method ?? 'GET'
    if (url.endsWith('/api/health')) {
      return json({
        status: 'ok',
        provider: 'openai',
        version: 'test',
        chat_streaming_enabled: true,
        tools_enabled: false,
        rag_enabled: false,
        voice_enabled: false,
        memory_enabled: false,
        workflow_engine_enabled: false,
        observability_enabled: false,
        plugins_enabled: false,
        hitl_enabled: false,
        background_jobs_enabled: false,
        security_governance_enabled: options.enabled ?? true,
      })
    }
    if (url.endsWith('/api/security/roles') && method === 'GET') {
      if (options.tokenInvalid) {
        return json({ error: { code: 'invalid_access_token', message: 'Session expired.' } }, 401)
      }
      if (options.roleForbidden) {
        return json({ error: { code: 'permission_denied', message: 'Forbidden' } }, 403)
      }
      return json([
        {
          name: 'member',
          description: 'Authenticated user baseline',
          is_system: true,
          permissions: ['tools:execute'],
        },
        {
          name: 'operator',
          description: 'Operational user',
          is_system: true,
          permissions: ['jobs:view_all'],
        },
      ])
    }
    if (url.includes('/api/security/users?') && method === 'GET') {
      return json({
        items: [
          {
            id: targetId,
            email: 'person@example.com',
            display_name: 'Target Person',
            roles: targetRoles.map((role) => ({
              user_id: targetId,
              role_name: role,
              implicit: role === 'member',
              created_at: null,
            })),
          },
        ],
        total: 1,
        limit: 25,
        offset: 0,
      })
    }
    if (url.endsWith(`/api/security/users/${targetId}/roles`) && method === 'POST') {
      const body = JSON.parse(String(init?.body)) as { role_name: string }
      targetRoles = [...targetRoles, body.role_name]
      return json({ user_id: targetId, role_name: body.role_name, implicit: false })
    }
    if (url.includes(`/api/security/users/${targetId}/roles/`) && method === 'DELETE') {
      const role = url.split('/').pop() ?? ''
      targetRoles = targetRoles.filter((item) => item !== role)
      return json({ user_id: targetId, role_name: role })
    }
    if (url.includes('/api/security/audit') && method === 'GET') {
      const offset = Number(new URL(url, 'http://test').searchParams.get('offset') ?? 0)
      return json({
        items: [
          {
            id: `audit-${offset}`,
            occurred_at: '2026-08-20T10:00:00.000Z',
            actor_user_id: targetId,
            actor_kind: 'user',
            action: 'role.assigned',
            resource_type: 'role',
            resource_id: targetId,
            outcome: 'success',
            metadata: {},
            request_id: null,
            trace_id: null,
            source_ip_hash: null,
            created_at: null,
          },
        ],
        total: 30,
        limit: 25,
        offset,
      })
    }
    if (url.endsWith('/api/security/policies')) {
      return json({
        security_governance_enabled: true,
        rbac_enforcement_enabled: true,
        guardrails_enabled: true,
        role_count: 4,
        permission_count: 12,
        guardrail_rule_count: 5,
        audit_retention_days: 365,
        security_guardrails_mode: 'flag',
        feature_flags: { security_audit_log_enabled: true },
        rate_limits_per_minute: { tool_invocation: 60, approval_decision: 30 },
      })
    }
    return json([])
  })
}

function renderSecurity() {
  return renderWithProviders(
    <Routes>
      <Route path="/" element={<div>Signed out</div>} />
      <Route
        path="/security"
        element={
          <ProtectedRoute>
            <SecurityPage />
          </ProtectedRoute>
        }
      />
    </Routes>,
    { initialRoute: '/security' },
  )
}

afterEach(() => {
  cleanup()
  window.localStorage.clear()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

it('lists users and supports role assign and revoke actions', async () => {
  storeSession(jwt(), currentUser)
  const fetchMock = securityFetch()
  vi.stubGlobal('fetch', fetchMock)
  renderSecurity()

  const userName = await screen.findByText('Target Person')
  const row = userName.closest('tr') as HTMLElement
  await userEvent.selectOptions(
    within(row).getByLabelText('Role to assign to Target Person'),
    'admin',
  )
  await userEvent.click(within(row).getByRole('button', { name: 'Assign role' }))
  expect(await screen.findByText('Admin role assigned.')).toBeTruthy()
  await waitFor(() =>
    expect(within(row).getByRole('button', { name: 'Revoke Admin' })).toBeTruthy(),
  )
  await userEvent.click(within(row).getByRole('button', { name: 'Revoke Admin' }))
  expect(await screen.findByText('Admin role revoked.')).toBeTruthy()
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true)
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(true)
})

it('shows friendly disabled and role permission states', async () => {
  storeSession(jwt(), currentUser)
  vi.stubGlobal('fetch', securityFetch({ enabled: false }))
  const view = renderSecurity()
  expect(
    await screen.findByRole('heading', { name: 'Security & Governance is not available' }),
  ).toBeTruthy()
  view.unmount()

  vi.stubGlobal('fetch', securityFetch({ roleForbidden: true }))
  renderSecurity()
  expect(
    await screen.findByRole('heading', { name: 'Role management access required' }),
  ).toBeTruthy()
  expect(screen.queryByRole('button', { name: /Assign/ })).toBeNull()
})

it('filters the audit log and renders non-sensitive policy aggregates', async () => {
  storeSession(jwt(), currentUser)
  const fetchMock = securityFetch()
  vi.stubGlobal('fetch', fetchMock)
  renderSecurity()

  await screen.findByText('Target Person')
  await userEvent.click(screen.getByRole('tab', { name: 'Audit Log' }))
  expect(screen.getByRole('tab', { name: 'Audit Log' }).getAttribute('aria-controls')).toBe(
    'security-panel-audit',
  )
  expect(screen.getByRole('tabpanel').getAttribute('aria-labelledby')).toBe('security-tab-audit')
  expect(await screen.findByText('role.assigned')).toBeTruthy()
  await userEvent.keyboard('{ArrowRight}')
  const policiesTab = screen.getByRole('tab', { name: 'Policies' })
  expect(policiesTab.getAttribute('aria-selected')).toBe('true')
  expect(document.activeElement).toBe(policiesTab)
  await userEvent.keyboard('{Home}')
  const rolesTab = screen.getByRole('tab', { name: 'Roles' })
  expect(rolesTab.getAttribute('aria-selected')).toBe('true')
  expect(document.activeElement).toBe(rolesTab)
  await userEvent.click(screen.getByRole('tab', { name: 'Audit Log' }))
  await userEvent.click(screen.getByRole('button', { name: 'Next' }))
  await waitFor(() =>
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('offset=25'))).toBe(true),
  )
  await userEvent.click(screen.getByRole('button', { name: 'Previous' }))
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.filter(([url]) => String(url).includes('offset=0')).length,
    ).toBeGreaterThan(1),
  )
  await userEvent.type(screen.getByLabelText('Actor user ID'), targetId)
  await userEvent.selectOptions(screen.getByLabelText('Outcome'), 'denied')
  await userEvent.click(screen.getByRole('button', { name: 'Apply filters' }))
  await waitFor(() => {
    expect(
      fetchMock.mock.calls.some(
        ([url]) =>
          String(url).includes(`actor_user_id=${targetId}`) &&
          String(url).includes('outcome=denied'),
      ),
    ).toBe(true)
  })

  await userEvent.click(screen.getByRole('tab', { name: 'Policies' }))
  expect(await screen.findByText('12')).toBeTruthy()
  expect(screen.getByText('Tool Invocation')).toBeTruthy()
  expect(screen.queryByText(/regex/i)).toBeNull()
  expect(screen.queryByText(/bootstrap/i)).toBeNull()
})

it('clears an invalid token and redirects to the public route', async () => {
  storeSession(jwt(), currentUser)
  vi.stubGlobal('fetch', securityFetch({ tokenInvalid: true }))

  renderSecurity()

  expect(await screen.findByText('Signed out')).toBeTruthy()
  expect(getStoredAccessToken()).toBeNull()
})

it('shows the Security nav link only when the health flag is enabled', async () => {
  storeSession(jwt(), currentUser)
  vi.stubGlobal('fetch', securityFetch({ enabled: true }))
  const view = renderWithProviders(<AppNav current="chat" />)
  expect(await screen.findByRole('link', { name: 'Security' })).toBeTruthy()
  view.unmount()

  vi.stubGlobal('fetch', securityFetch({ enabled: false }))
  renderWithProviders(<AppNav current="chat" />)
  await waitFor(() => expect(screen.queryByRole('link', { name: 'Security' })).toBeNull())
})
