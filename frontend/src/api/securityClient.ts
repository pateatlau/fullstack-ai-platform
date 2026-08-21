import type {
  AuditOutcome,
  SecurityAuditEntry,
  SecurityAuditListResponse,
  SecurityPolicySummary,
  SecurityRole,
  SecurityRoleName,
  SecurityUserRole,
  SecurityUserListResponse,
} from '../types/security'
import { API_BASE_URL, buildAuthHeaders, captureRequestId, parseErrorEnvelope } from './request'

export class SecurityApiError extends Error {
  status: number
  code?: string

  constructor(message: string, status: number, code?: string) {
    super(message)
    this.name = 'SecurityApiError'
    this.status = status
    this.code = code
  }
}

async function requestSecurity<T>(path: string, init: RequestInit, fallback: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, init)
  captureRequestId(response)
  if (!response.ok) {
    const parsed = await parseErrorEnvelope(response, fallback)
    throw new SecurityApiError(parsed.message, parsed.status, parsed.code)
  }
  return (await response.json()) as T
}

export async function fetchSecurityRoles(): Promise<SecurityRole[]> {
  return requestSecurity(
    '/api/security/roles',
    { method: 'GET', headers: buildAuthHeaders({ json: false }) },
    'Failed to load security roles.',
  )
}

export async function fetchSecurityUsers(
  params: { limit?: number; offset?: number } = {},
): Promise<SecurityUserListResponse> {
  const search = new URLSearchParams()
  if (params.limit !== undefined) search.set('limit', String(params.limit))
  if (params.offset !== undefined) search.set('offset', String(params.offset))
  const query = search.toString()
  return requestSecurity(
    `/api/security/users${query ? `?${query}` : ''}`,
    { method: 'GET', headers: buildAuthHeaders({ json: false }) },
    'Failed to load users and roles.',
  )
}

export async function fetchUserRoles(userId: string): Promise<SecurityUserRole[]> {
  return requestSecurity(
    `/api/security/users/${encodeURIComponent(userId)}/roles`,
    { method: 'GET', headers: buildAuthHeaders({ json: false }) },
    'Failed to load user roles.',
  )
}

export async function assignUserRole(
  userId: string,
  roleName: SecurityRoleName,
): Promise<SecurityUserRole> {
  return requestSecurity(
    `/api/security/users/${encodeURIComponent(userId)}/roles`,
    {
      method: 'POST',
      headers: buildAuthHeaders(),
      body: JSON.stringify({ role_name: roleName }),
    },
    'Failed to assign role.',
  )
}

export async function revokeUserRole(
  userId: string,
  roleName: string,
): Promise<{ user_id: string; role_name: string }> {
  return requestSecurity(
    `/api/security/users/${encodeURIComponent(userId)}/roles/${encodeURIComponent(roleName)}`,
    { method: 'DELETE', headers: buildAuthHeaders({ json: false }) },
    'Failed to revoke role.',
  )
}

export interface FetchAuditParams {
  actor_user_id?: string
  action?: string
  resource_type?: string
  outcome?: AuditOutcome
  since?: string
  until?: string
  limit?: number
  offset?: number
}

export async function fetchSecurityAudit(
  params: FetchAuditParams = {},
): Promise<SecurityAuditListResponse> {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== '') search.set(key, String(value))
  })
  const query = search.toString()
  return requestSecurity(
    `/api/security/audit${query ? `?${query}` : ''}`,
    { method: 'GET', headers: buildAuthHeaders({ json: false }) },
    'Failed to load audit events.',
  )
}

export async function fetchSecurityAuditEntry(auditId: string): Promise<SecurityAuditEntry> {
  return requestSecurity(
    `/api/security/audit/${encodeURIComponent(auditId)}`,
    { method: 'GET', headers: buildAuthHeaders({ json: false }) },
    'Failed to load audit event.',
  )
}

export async function fetchSecurityPolicies(): Promise<SecurityPolicySummary> {
  return requestSecurity(
    '/api/security/policies',
    { method: 'GET', headers: buildAuthHeaders({ json: false }) },
    'Failed to load security policies.',
  )
}
