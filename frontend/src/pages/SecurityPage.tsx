import { useCallback, useEffect, useState } from 'react'
import {
  assignUserRole,
  fetchSecurityAudit,
  fetchSecurityPolicies,
  fetchSecurityRoles,
  fetchSecurityUsers,
  revokeUserRole,
  SecurityApiError,
  type FetchAuditParams,
} from '../api/securityClient'
import { AppNav } from '../components/AppNav'
import { AuthControls } from '../components/AuthControls'
import { EmptyState } from '../components/EmptyState'
import { LoadingIndicator } from '../components/LoadingIndicator'
import { useAuthContext } from '../context/AuthContext'
import { useChatStreamingEnabled } from '../hooks/useChatStreamingEnabled'
import type {
  AuditOutcome,
  SecurityAuditEntry,
  SecurityPolicySummary,
  SecurityRole,
  SecurityRoleName,
  SecurityUserSummary,
} from '../types/security'
import { formatSecurityLabel, formatSecurityTimestamp } from '../types/security'

type SecurityTab = 'roles' | 'audit' | 'policies'
const ASSIGNABLE_ROLES: SecurityRoleName[] = ['operator', 'admin', 'owner']
const AUDIT_PAGE_SIZE = 25

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof SecurityApiError ? error.message : fallback
}

function isInvalidToken(error: unknown): boolean {
  return (
    error instanceof SecurityApiError &&
    (error.code === 'invalid_access_token' || error.status === 401)
  )
}

function StatusMessage({ message, success = false }: { message: string; success?: boolean }) {
  return (
    <div
      role={success ? 'status' : 'alert'}
      className={`rounded-lg border px-3 py-2 text-sm ${
        success
          ? 'border-brand-500/30 bg-brand-500/10 text-brand-800'
          : 'border-danger-600/30 bg-danger-100 text-danger-700'
      }`}
    >
      {message}
    </div>
  )
}

function PermissionNotice({ area }: { area: string }) {
  return (
    <EmptyState
      title={`${area} access required`}
      description={`Your account does not have permission to view ${area.toLowerCase()}.`}
      className="border-shell-800/20 bg-shell-50/80 [&_h3]:text-shell-950 [&_p]:text-shell-700"
    />
  )
}

function SecurityUnavailableNotice() {
  return (
    <div className="mx-auto w-full max-w-5xl px-3 py-8 sm:px-4">
      <section className="rounded-chat border border-shell-800/15 bg-white p-5 shadow-chat-card">
        <h2 className="text-base font-semibold text-shell-950">
          Security &amp; Governance is not available
        </h2>
        <p className="mt-2 text-sm text-shell-700">
          Security governance is disabled on this server.
        </p>
        <a
          href="/"
          className="mt-4 inline-flex text-sm font-semibold text-brand-700 underline-offset-2 hover:underline"
        >
          Return to chat
        </a>
      </section>
    </div>
  )
}

interface TabProps {
  onGlobalError: (error: unknown) => boolean
}

function RolesTab({ onGlobalError }: TabProps) {
  const [roles, setRoles] = useState<SecurityRole[]>([])
  const [users, setUsers] = useState<SecurityUserSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [forbidden, setForbidden] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [pending, setPending] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [roleRows, userRows] = await Promise.all([fetchSecurityRoles(), fetchSecurityUsers()])
      setRoles(roleRows)
      setUsers(userRows)
      setForbidden(false)
    } catch (apiError) {
      if (!onGlobalError(apiError)) {
        if (apiError instanceof SecurityApiError && apiError.status === 403) {
          setForbidden(true)
        } else {
          setError(errorMessage(apiError, 'Unable to load roles.'))
        }
      }
    } finally {
      setLoading(false)
    }
  }, [onGlobalError])

  useEffect(() => {
    let cancelled = false
    void Promise.all([fetchSecurityRoles(), fetchSecurityUsers()])
      .then(([roleRows, userRows]) => {
        if (!cancelled) {
          setRoles(roleRows)
          setUsers(userRows)
          setForbidden(false)
        }
      })
      .catch((apiError: unknown) => {
        if (cancelled || onGlobalError(apiError)) return
        if (apiError instanceof SecurityApiError && apiError.status === 403) {
          setForbidden(true)
        } else {
          setError(errorMessage(apiError, 'Unable to load roles.'))
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [onGlobalError])

  const mutateRole = async (
    user: SecurityUserSummary,
    roleName: SecurityRoleName,
    action: 'assign' | 'revoke',
  ) => {
    const key = `${user.id}:${roleName}`
    setPending(key)
    setError(null)
    setSuccess(null)
    try {
      if (action === 'assign') await assignUserRole(user.id, roleName)
      else await revokeUserRole(user.id, roleName)
      setSuccess(
        `${formatSecurityLabel(roleName)} role ${action === 'assign' ? 'assigned' : 'revoked'}.`,
      )
      await load()
    } catch (apiError) {
      if (!onGlobalError(apiError)) {
        setError(errorMessage(apiError, `Unable to ${action} role.`))
      }
    } finally {
      setPending(null)
    }
  }

  if (loading) return <LoadingIndicator variant="inline" label="Loading roles…" />
  if (forbidden) return <PermissionNotice area="Role management" />

  return (
    <div className="flex flex-col gap-5">
      {error ? <StatusMessage message={error} /> : null}
      {success ? <StatusMessage message={success} success /> : null}

      <section aria-labelledby="role-definitions-heading">
        <h2 id="role-definitions-heading" className="text-base font-semibold text-shell-950">
          System roles
        </h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {roles.map((role) => (
            <div key={role.name} className="border-l-2 border-brand-500 bg-shell-50 px-3 py-2">
              <div className="font-semibold text-shell-950">{formatSecurityLabel(role.name)}</div>
              <div className="mt-1 text-xs text-shell-700">{role.description}</div>
              <div className="mt-2 text-xs text-shell-600">
                {role.permissions.length} permission{role.permissions.length === 1 ? '' : 's'}
              </div>
            </div>
          ))}
        </div>
      </section>

      <section aria-labelledby="user-roles-heading">
        <h2 id="user-roles-heading" className="text-base font-semibold text-shell-950">
          User assignments
        </h2>
        {users.length === 0 ? (
          <EmptyState
            title="No users found"
            description="No authenticated users are available for role assignment."
            className="mt-3 border-shell-800/20 bg-shell-50/80"
          />
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="min-w-full text-left text-sm text-shell-900">
              <thead className="border-b border-shell-800/15 text-xs uppercase text-shell-700">
                <tr>
                  <th className="px-2 py-2" scope="col">
                    User
                  </th>
                  <th className="px-2 py-2" scope="col">
                    Roles
                  </th>
                  <th className="px-2 py-2" scope="col">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-shell-800/10">
                {users.map((user) => {
                  const heldRoles = new Set(user.roles.map((role) => role.role_name))
                  const nextRole = ASSIGNABLE_ROLES.find((role) => !heldRoles.has(role))
                  return (
                    <tr key={user.id}>
                      <td className="px-2 py-3 align-top">
                        <div className="font-medium">
                          {user.display_name || user.email || 'Unnamed user'}
                        </div>
                        <div className="mt-0.5 text-xs text-shell-600">{user.email || user.id}</div>
                      </td>
                      <td className="px-2 py-3 align-top">
                        <div className="flex flex-wrap gap-1.5">
                          {user.roles.map((role) => (
                            <span
                              key={role.role_name}
                              className="inline-flex rounded-full border border-shell-300 bg-shell-100 px-2 py-0.5 text-xs font-medium"
                            >
                              {formatSecurityLabel(role.role_name)}
                              {role.implicit ? ' (baseline)' : ''}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td className="px-2 py-3 align-top">
                        <div className="flex flex-wrap gap-2">
                          {nextRole ? (
                            <button
                              type="button"
                              disabled={pending !== null}
                              onClick={() => void mutateRole(user, nextRole, 'assign')}
                              className="rounded-lg border border-brand-500/40 bg-brand-500/10 px-2 py-1 text-xs font-semibold text-brand-800 disabled:opacity-50"
                            >
                              Assign {formatSecurityLabel(nextRole)}
                            </button>
                          ) : null}
                          {user.roles
                            .filter((role) => !role.implicit)
                            .map((role) => (
                              <button
                                key={role.role_name}
                                type="button"
                                disabled={pending !== null}
                                onClick={() => void mutateRole(user, role.role_name, 'revoke')}
                                className="rounded-lg border border-danger-600/30 px-2 py-1 text-xs font-semibold text-danger-700 disabled:opacity-50"
                              >
                                Revoke {formatSecurityLabel(role.role_name)}
                              </button>
                            ))}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}

function toIso(value: string): string | undefined {
  if (!value) return undefined
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? undefined : parsed.toISOString()
}

function AuditTab({ onGlobalError }: TabProps) {
  const [events, setEvents] = useState<SecurityAuditEntry[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [filters, setFilters] = useState<FetchAuditParams>({})
  const [draft, setDraft] = useState({
    actor: '',
    action: '',
    resource: '',
    outcome: '',
    since: '',
    until: '',
  })
  const [loading, setLoading] = useState(true)
  const [forbidden, setForbidden] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    void fetchSecurityAudit({ ...filters, limit: AUDIT_PAGE_SIZE, offset })
      .then((result) => {
        if (!cancelled) {
          setEvents(result.items)
          setTotal(result.total)
          setForbidden(false)
        }
      })
      .catch((apiError: unknown) => {
        if (cancelled || onGlobalError(apiError)) return
        if (apiError instanceof SecurityApiError && apiError.status === 403) setForbidden(true)
        else setError(errorMessage(apiError, 'Unable to load audit events.'))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [filters, offset, onGlobalError])

  if (forbidden) return <PermissionNotice area="Audit log" />

  return (
    <div>
      <form
        className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"
        onSubmit={(event) => {
          event.preventDefault()
          setOffset(0)
          setFilters({
            actor_user_id: draft.actor || undefined,
            action: draft.action || undefined,
            resource_type: draft.resource || undefined,
            outcome: (draft.outcome || undefined) as AuditOutcome | undefined,
            since: toIso(draft.since),
            until: toIso(draft.until),
          })
        }}
      >
        {[
          ['actor', 'Actor user ID'],
          ['action', 'Action'],
          ['resource', 'Resource type'],
        ].map(([name, label]) => (
          <label key={name} className="flex flex-col gap-1 text-xs font-medium text-shell-700">
            {label}
            <input
              value={draft[name as 'actor' | 'action' | 'resource']}
              onChange={(event) =>
                setDraft((current) => ({ ...current, [name]: event.target.value }))
              }
              className="rounded-lg border border-shell-800/20 bg-white px-2 py-1.5 text-sm"
            />
          </label>
        ))}
        <label className="flex flex-col gap-1 text-xs font-medium text-shell-700">
          Outcome
          <select
            value={draft.outcome}
            onChange={(event) =>
              setDraft((current) => ({ ...current, outcome: event.target.value }))
            }
            className="rounded-lg border border-shell-800/20 bg-white px-2 py-1.5 text-sm"
          >
            <option value="">All outcomes</option>
            <option value="success">Success</option>
            <option value="denied">Denied</option>
            <option value="error">Error</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium text-shell-700">
          Since
          <input
            type="datetime-local"
            value={draft.since}
            onChange={(event) => setDraft((current) => ({ ...current, since: event.target.value }))}
            className="rounded-lg border border-shell-800/20 px-2 py-1.5 text-sm"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium text-shell-700">
          Until
          <input
            type="datetime-local"
            value={draft.until}
            onChange={(event) => setDraft((current) => ({ ...current, until: event.target.value }))}
            className="rounded-lg border border-shell-800/20 px-2 py-1.5 text-sm"
          />
        </label>
        <button
          type="submit"
          className="self-end rounded-lg bg-shell-900 px-3 py-2 text-sm font-semibold text-white"
        >
          Apply filters
        </button>
      </form>

      {error ? (
        <div className="mt-4">
          <StatusMessage message={error} />
        </div>
      ) : null}
      {loading ? (
        <LoadingIndicator variant="inline" label="Loading audit events…" className="mt-5" />
      ) : events.length === 0 ? (
        <EmptyState
          title="No audit events found"
          description="No events match the current filters."
          className="mt-5 border-shell-800/20 bg-shell-50/80"
        />
      ) : (
        <div className="mt-5 overflow-x-auto">
          <table className="min-w-full text-left text-sm text-shell-900">
            <thead className="border-b border-shell-800/15 text-xs uppercase text-shell-700">
              <tr>
                <th className="px-2 py-2">Time</th>
                <th className="px-2 py-2">Actor</th>
                <th className="px-2 py-2">Action</th>
                <th className="px-2 py-2">Resource</th>
                <th className="px-2 py-2">Outcome</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-shell-800/10">
              {events.map((entry) => (
                <tr key={entry.id}>
                  <td className="whitespace-nowrap px-2 py-2">
                    {formatSecurityTimestamp(entry.occurred_at)}
                  </td>
                  <td className="px-2 py-2">
                    <div>{formatSecurityLabel(entry.actor_kind)}</div>
                    <div className="text-xs text-shell-600">{entry.actor_user_id || 'System'}</div>
                  </td>
                  <td className="px-2 py-2 font-medium">{entry.action}</td>
                  <td className="px-2 py-2">{entry.resource_type || '—'}</td>
                  <td className="px-2 py-2">
                    <span className="rounded-full border border-shell-300 bg-shell-100 px-2 py-0.5 text-xs font-medium">
                      {formatSecurityLabel(entry.outcome)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="mt-4 flex items-center justify-between text-sm text-shell-700">
        <span>
          {total} event{total === 1 ? '' : 's'}
        </span>
        <div className="flex gap-2">
          <button
            type="button"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - AUDIT_PAGE_SIZE))}
            className="rounded-lg border px-2 py-1 disabled:opacity-40"
          >
            Previous
          </button>
          <button
            type="button"
            disabled={offset + AUDIT_PAGE_SIZE >= total}
            onClick={() => setOffset(offset + AUDIT_PAGE_SIZE)}
            className="rounded-lg border px-2 py-1 disabled:opacity-40"
          >
            Next
          </button>
        </div>
      </div>
    </div>
  )
}

function PoliciesTab({ onGlobalError }: TabProps) {
  const [summary, setSummary] = useState<SecurityPolicySummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [forbidden, setForbidden] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    void fetchSecurityPolicies()
      .then((result) => {
        if (!cancelled) setSummary(result)
      })
      .catch((apiError: unknown) => {
        if (cancelled || onGlobalError(apiError)) return
        if (apiError instanceof SecurityApiError && apiError.status === 403) setForbidden(true)
        else setError(errorMessage(apiError, 'Unable to load policy summary.'))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [onGlobalError])

  if (loading) return <LoadingIndicator variant="inline" label="Loading policies…" />
  if (forbidden) return <PermissionNotice area="Policy summary" />
  if (error) return <StatusMessage message={error} />
  if (!summary) return null

  const counts = [
    ['Roles', summary.role_count],
    ['Permissions', summary.permission_count],
    ['Guardrail rules', summary.guardrail_rule_count],
    ['Audit retention', `${summary.audit_retention_days} days`],
  ]
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <section aria-labelledby="policy-counts-heading">
        <h2 id="policy-counts-heading" className="text-base font-semibold">
          Policy inventory
        </h2>
        <dl className="mt-3 grid grid-cols-2 gap-px overflow-hidden border border-shell-800/15 bg-shell-800/15">
          {counts.map(([label, value]) => (
            <div key={label} className="bg-white p-3">
              <dt className="text-xs text-shell-600">{label}</dt>
              <dd className="mt-1 text-xl font-semibold">{value}</dd>
            </div>
          ))}
        </dl>
      </section>
      <section aria-labelledby="rate-limits-heading">
        <h2 id="rate-limits-heading" className="text-base font-semibold">
          Active per-minute limits
        </h2>
        <dl className="mt-3 divide-y divide-shell-800/10 border-y border-shell-800/15">
          {Object.entries(summary.rate_limits_per_minute).map(([name, value]) => (
            <div key={name} className="flex justify-between gap-4 py-2 text-sm">
              <dt>{formatSecurityLabel(name)}</dt>
              <dd className="font-semibold">{value}</dd>
            </div>
          ))}
        </dl>
      </section>
      <section aria-labelledby="controls-heading" className="lg:col-span-2">
        <h2 id="controls-heading" className="text-base font-semibold">
          Control status
        </h2>
        <div className="mt-3 flex flex-wrap gap-2">
          {Object.entries(summary.feature_flags).map(([name, enabled]) => (
            <span
              key={name}
              className="rounded-full border border-shell-300 bg-shell-100 px-2 py-1 text-xs font-medium"
            >
              {formatSecurityLabel(name)}: {enabled ? 'On' : 'Off'}
            </span>
          ))}
          <span className="rounded-full border border-shell-300 bg-shell-100 px-2 py-1 text-xs font-medium">
            Guardrail mode: {formatSecurityLabel(summary.security_guardrails_mode)}
          </span>
        </div>
      </section>
    </div>
  )
}

function SecurityContent() {
  const { handleInvalidAccessToken } = useAuthContext()
  const [activeTab, setActiveTab] = useState<SecurityTab>('roles')
  const [disabled, setDisabled] = useState(false)
  const handleGlobalError = useCallback(
    (error: unknown): boolean => {
      if (isInvalidToken(error)) {
        handleInvalidAccessToken()
        return true
      }
      if (error instanceof SecurityApiError && error.code === 'feature_disabled') {
        setDisabled(true)
        return true
      }
      return false
    },
    [handleInvalidAccessToken],
  )

  if (disabled) return <SecurityUnavailableNotice />
  return (
    <main className="mx-auto flex w-full max-w-6xl flex-col gap-5 px-3 py-4 sm:px-4 sm:py-6">
      <section className="border-b border-shell-800/15 pb-4">
        <h2 className="text-lg font-semibold text-shell-950">Security dashboard</h2>
        <div className="mt-4 flex gap-2" role="tablist" aria-label="Security dashboard views">
          {(['roles', 'audit', 'policies'] as SecurityTab[]).map((tab) => (
            <button
              key={tab}
              type="button"
              role="tab"
              aria-selected={activeTab === tab}
              onClick={() => setActiveTab(tab)}
              className={`rounded-lg border px-3 py-1.5 text-sm font-medium ${activeTab === tab ? 'border-brand-500 bg-brand-500/10 text-brand-800' : 'border-shell-800/20 bg-white text-shell-800'}`}
            >
              {tab === 'audit' ? 'Audit Log' : formatSecurityLabel(tab)}
            </button>
          ))}
        </div>
      </section>
      <section className="rounded-chat border border-shell-800/15 bg-white p-4 shadow-chat-card sm:p-5">
        {activeTab === 'roles' ? (
          <RolesTab onGlobalError={handleGlobalError} />
        ) : activeTab === 'audit' ? (
          <AuditTab onGlobalError={handleGlobalError} />
        ) : (
          <PoliciesTab onGlobalError={handleGlobalError} />
        )}
      </section>
    </main>
  )
}

export function SecurityPage() {
  const { securityGovernanceEnabled, healthLoading } = useChatStreamingEnabled()
  return (
    <div className="min-h-dvh bg-linear-to-b from-shell-50 via-shell-100 to-[#ebeff6]">
      <header className="sticky top-0 z-20 border-b border-shell-800/15 bg-shell-50/90 px-3 py-2 backdrop-blur sm:px-4">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <AppNav current="security" />
            <h1 className="truncate text-sm font-semibold text-shell-900 sm:text-base">Security</h1>
          </div>
          <AuthControls />
        </div>
      </header>
      {healthLoading ? (
        <div className="flex justify-center py-12">
          <LoadingIndicator variant="inline" label="Loading security…" />
        </div>
      ) : securityGovernanceEnabled ? (
        <SecurityContent />
      ) : (
        <SecurityUnavailableNotice />
      )}
    </div>
  )
}
