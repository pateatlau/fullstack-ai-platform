/* @vitest-environment jsdom */

import { afterEach, expect, it, vi } from 'vitest'
import { storeSession } from '../auth/tokenStorage'
import type { AuthenticatedUser } from '../types/auth'
import {
  assignUserRole,
  fetchSecurityAudit,
  fetchSecurityAuditEntry,
  fetchSecurityPolicies,
  fetchSecurityRoles,
  fetchSecurityUsers,
  fetchUserRoles,
  revokeUserRole,
  SecurityApiError,
} from './securityClient'

const user: AuthenticatedUser = {
  id: 'user-1',
  email: 'admin@example.com',
  display_name: 'Admin',
  picture_url: null,
}

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  window.localStorage.clear()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

it('calls all role endpoints with auth and encoded paths', async () => {
  storeSession('security-jwt', user)
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(response([]))
    .mockResolvedValueOnce(response({ items: [], total: 0, limit: 25, offset: 25 }))
    .mockResolvedValueOnce(response([]))
    .mockResolvedValueOnce(response({ user_id: 'user/2', role_name: 'operator', implicit: false }))
    .mockResolvedValueOnce(response({ user_id: 'user/2', role_name: 'operator' }))
  vi.stubGlobal('fetch', fetchMock)

  await fetchSecurityRoles()
  await fetchSecurityUsers({ limit: 25, offset: 25 })
  await fetchUserRoles('user/2')
  await assignUserRole('user/2', 'operator')
  await revokeUserRole('user/2', 'operator')

  expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
    ['/api/security/roles', 'GET'],
    ['/api/security/users?limit=25&offset=25', 'GET'],
    ['/api/security/users/user%2F2/roles', 'GET'],
    ['/api/security/users/user%2F2/roles', 'POST'],
    ['/api/security/users/user%2F2/roles/operator', 'DELETE'],
  ])
  expect(fetchMock.mock.calls[0][1]?.headers).toMatchObject({
    Authorization: 'Bearer security-jwt',
  })
  expect(fetchMock.mock.calls[3][1]?.body).toBe('{"role_name":"operator"}')
})

it('builds audit filters and loads audit detail and policies', async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(response({ items: [], total: 0, limit: 25, offset: 0 }))
    .mockResolvedValueOnce(response({ id: 'audit-1' }))
    .mockResolvedValueOnce(response({ role_count: 4 }))
  vi.stubGlobal('fetch', fetchMock)

  await fetchSecurityAudit({
    actor_user_id: 'actor-1',
    outcome: 'denied',
    since: '2026-08-01T00:00:00.000Z',
    limit: 25,
  })
  await fetchSecurityAuditEntry('audit/1')
  await fetchSecurityPolicies()

  expect(fetchMock.mock.calls[0][0]).toBe(
    '/api/security/audit?actor_user_id=actor-1&outcome=denied&since=2026-08-01T00%3A00%3A00.000Z&limit=25',
  )
  expect(fetchMock.mock.calls[1][0]).toBe('/api/security/audit/audit%2F1')
  expect(fetchMock.mock.calls[2][0]).toBe('/api/security/policies')
})

it('preserves disabled and forbidden error codes', async () => {
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockResolvedValue(
        response({ error: { code: 'feature_disabled', message: 'Security is disabled.' } }, 503),
      ),
  )

  await expect(fetchSecurityRoles()).rejects.toMatchObject({
    name: 'SecurityApiError',
    status: 503,
    code: 'feature_disabled',
  } satisfies Partial<SecurityApiError>)
})
