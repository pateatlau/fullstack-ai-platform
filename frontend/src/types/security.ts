export type SecurityRoleName = 'member' | 'operator' | 'admin' | 'owner'
export type AuditOutcome = 'success' | 'denied' | 'error'

export interface SecurityRole {
  name: SecurityRoleName
  description: string
  is_system: boolean
  permissions: string[]
}

export interface SecurityUserRole {
  user_id: string
  role_name: string
  implicit: boolean
  created_at: string | null
}

export interface SecurityUserSummary {
  id: string
  email: string | null
  display_name: string | null
  roles: SecurityUserRole[]
}

export interface SecurityUserListResponse {
  items: SecurityUserSummary[]
  total: number
  limit: number
  offset: number
}

export interface SecurityAuditEntry {
  id: string
  occurred_at: string
  actor_user_id: string | null
  actor_kind: string
  action: string
  resource_type: string | null
  resource_id: string | null
  outcome: AuditOutcome
  metadata: Record<string, unknown>
  request_id: string | null
  trace_id: string | null
  source_ip_hash: string | null
  created_at: string | null
}

export interface SecurityAuditListResponse {
  items: SecurityAuditEntry[]
  total: number
  limit: number
  offset: number
}

export interface SecurityPolicySummary {
  security_governance_enabled: boolean
  rbac_enforcement_enabled: boolean
  guardrails_enabled: boolean
  role_count: number
  permission_count: number
  guardrail_rule_count: number
  audit_retention_days: number
  security_guardrails_mode: string
  feature_flags: Record<string, boolean>
  rate_limits_per_minute: Record<string, number>
}

export function formatSecurityLabel(value: string): string {
  return value.replace(/[._:]/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

export function formatSecurityTimestamp(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}
