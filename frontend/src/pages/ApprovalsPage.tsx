import { useCallback, useEffect, useState } from 'react'
import {
  ApprovalsApiError,
  fetchApprovalRevisions,
  fetchApprovals,
  rejectApproval,
  reviseApproval,
  streamApproveApproval,
} from '../api/approvalsClient'
import { approveWorkflowNode, rejectWorkflowNode, WorkflowApiError } from '../api/workflowClient'
import { AppNav } from '../components/AppNav'
import { AuthControls } from '../components/AuthControls'
import { EmptyState } from '../components/EmptyState'
import { LoadingIndicator } from '../components/LoadingIndicator'
import { useAuthContext } from '../context/AuthContext'
import { useChatStreamingEnabled } from '../hooks/useChatStreamingEnabled'
import type { ApprovalAuditEntry, ApprovalRevision, ProposedToolCall } from '../types/approvals'
import { formatApprovalKind, formatApprovalStatus } from '../types/approvals'

type ApprovalsTab = 'pending' | 'history'

function isInvalidAccessTokenError(error: unknown): boolean {
  return (
    (error instanceof ApprovalsApiError || error instanceof WorkflowApiError) &&
    (error.code === 'invalid_access_token' || error.status === 401)
  )
}

function approvalsPageErrorMessage(error: unknown): string {
  if (error instanceof ApprovalsApiError || error instanceof WorkflowApiError) {
    return error.message
  }
  return 'Something went wrong. Please try again.'
}

function formatTimestamp(value: string | null): string {
  if (!value) {
    return '—'
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  try {
    return date.toLocaleString()
  } catch {
    return value
  }
}

function callsToJson(calls: ProposedToolCall[] | null): string {
  if (!calls || calls.length === 0) {
    return '[]'
  }
  return JSON.stringify(calls, null, 2)
}

function parseEditedCalls(raw: string): ProposedToolCall[] {
  const parsed = JSON.parse(raw) as unknown
  if (!Array.isArray(parsed)) {
    throw new Error('Edited calls must be a JSON array.')
  }
  return parsed.map((entry, index) => {
    if (typeof entry !== 'object' || entry === null) {
      throw new Error(`Entry ${index} must be an object.`)
    }
    const record = entry as Record<string, unknown>
    if (typeof record.name !== 'string' || typeof record.call_id !== 'string') {
      throw new Error(`Entry ${index} requires name and call_id.`)
    }
    const args = record.arguments
    return {
      name: record.name,
      call_id: record.call_id,
      arguments:
        typeof args === 'object' && args !== null && !Array.isArray(args)
          ? (args as Record<string, unknown>)
          : {},
    }
  })
}

function parseEditedArguments(raw: string): Record<string, unknown> {
  const parsed = JSON.parse(raw) as unknown
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    throw new Error('Edited arguments must be a JSON object.')
  }
  return parsed as Record<string, unknown>
}

function ApprovalsUnavailableNotice() {
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-3 py-8 sm:px-4">
      <section className="rounded-chat border border-shell-800/15 bg-white p-5 shadow-chat-card">
        <h2 className="text-base font-semibold text-shell-950">Approvals are not available</h2>
        <p className="mt-2 text-sm text-shell-700">
          Human-in-the-loop approvals are disabled on this server. Your chat experience is
          unchanged.
        </p>
        <a
          href="/"
          className="mt-4 inline-flex text-sm font-semibold text-brand-600 underline-offset-2 hover:underline"
        >
          Return to chat
        </a>
      </section>
    </div>
  )
}

interface RevisionHistoryProps {
  approvalId: string
}

function RevisionHistory({ approvalId }: RevisionHistoryProps) {
  const [revisions, setRevisions] = useState<ApprovalRevision[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const items = await fetchApprovalRevisions(approvalId)
      setRevisions(items)
    } catch (apiError) {
      setError(apiError instanceof ApprovalsApiError ? apiError.message : 'Could not load history.')
    } finally {
      setLoading(false)
    }
  }, [approvalId])

  return (
    <details
      className="mt-2"
      onToggle={(event) => {
        if ((event.target as HTMLDetailsElement).open && revisions === null) {
          void load()
        }
      }}
    >
      <summary className="cursor-pointer text-xs font-semibold text-brand-700">
        View revision history
      </summary>
      {loading ? <p className="mt-2 text-xs text-shell-600">Loading revisions…</p> : null}
      {error ? (
        <p className="mt-2 text-xs text-danger-600" role="alert">
          {error}
        </p>
      ) : null}
      {revisions && revisions.length === 0 ? (
        <p className="mt-2 text-xs text-shell-600">No revisions recorded.</p>
      ) : null}
      {revisions && revisions.length > 0 ? (
        <ol className="mt-2 flex flex-col gap-2">
          {revisions.map((revision) => (
            <li
              key={revision.id}
              className="rounded-lg border border-shell-800/15 bg-shell-50 p-2 text-xs text-shell-800"
            >
              <p className="font-semibold">
                Revision #{revision.revision_number} — {formatTimestamp(revision.edited_at)}
              </p>
              {revision.note ? <p className="mt-1 text-shell-600">Note: {revision.note}</p> : null}
              <pre className="mt-1 overflow-x-auto rounded bg-white p-2 font-mono text-[11px]">
                {JSON.stringify(revision.edited_payload, null, 2)}
              </pre>
            </li>
          ))}
        </ol>
      ) : null}
    </details>
  )
}

interface PendingApprovalCardProps {
  entry: ApprovalAuditEntry
  onChanged: () => void
  onApiError: (error: unknown) => boolean
}

function PendingApprovalCard({ entry, onChanged, onApiError }: PendingApprovalCardProps) {
  const [editedJson, setEditedJson] = useState(() => callsToJson(entry.tool_calls))
  const [editedArgumentsJson, setEditedArgumentsJson] = useState('{}')
  const [reason, setReason] = useState('')
  const [localError, setLocalError] = useState<string | null>(null)
  const [isBusy, setIsBusy] = useState(false)

  const handleAgentSave = async () => {
    setLocalError(null)
    let editedCalls: ProposedToolCall[]
    try {
      editedCalls = parseEditedCalls(editedJson)
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Invalid JSON.')
      return
    }
    setIsBusy(true)
    try {
      await reviseApproval(entry.id, {
        edited_calls: editedCalls,
        note: reason.trim() || null,
      })
      onChanged()
    } catch (error) {
      if (!onApiError(error)) {
        setLocalError(error instanceof ApprovalsApiError ? error.message : 'Save failed.')
      }
    } finally {
      setIsBusy(false)
    }
  }

  const handleAgentApprove = async () => {
    setLocalError(null)
    let editedCalls: ProposedToolCall[]
    try {
      editedCalls = parseEditedCalls(editedJson)
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Invalid JSON.')
      return
    }
    setIsBusy(true)
    try {
      await streamApproveApproval(
        entry.id,
        { edited_calls: editedCalls, reason: reason.trim() || null },
        {
          onEnd: () => onChanged(),
          onError: (streamError) => {
            if (streamError instanceof ApprovalsApiError) {
              setLocalError(streamError.message)
            } else if ('type' in streamError && streamError.type === 'error') {
              setLocalError(streamError.message)
            } else if (streamError instanceof Error) {
              setLocalError(streamError.message)
            }
          },
        },
      )
    } catch (error) {
      if (!onApiError(error)) {
        setLocalError(error instanceof ApprovalsApiError ? error.message : 'Approve failed.')
      }
    } finally {
      setIsBusy(false)
    }
  }

  const handleAgentReject = async () => {
    setLocalError(null)
    setIsBusy(true)
    try {
      await rejectApproval(entry.id, reason.trim() || null)
      onChanged()
    } catch (error) {
      if (!onApiError(error)) {
        setLocalError(error instanceof ApprovalsApiError ? error.message : 'Reject failed.')
      }
    } finally {
      setIsBusy(false)
    }
  }

  const handleWorkflowApprove = async () => {
    if (!entry.workflow_run_id) {
      setLocalError('Missing workflow run id.')
      return
    }
    setLocalError(null)
    let editedArguments: Record<string, unknown> | undefined
    try {
      const parsed = parseEditedArguments(editedArgumentsJson)
      if (Object.keys(parsed).length > 0) {
        editedArguments = parsed
      }
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : 'Invalid JSON.')
      return
    }
    setIsBusy(true)
    try {
      await approveWorkflowNode(entry.workflow_run_id, entry.id, {
        edited_arguments: editedArguments ?? null,
        reason: reason.trim() || null,
      })
      onChanged()
    } catch (error) {
      if (!onApiError(error)) {
        setLocalError(error instanceof WorkflowApiError ? error.message : 'Approve failed.')
      }
    } finally {
      setIsBusy(false)
    }
  }

  const handleWorkflowReject = async () => {
    if (!entry.workflow_run_id) {
      setLocalError('Missing workflow run id.')
      return
    }
    setLocalError(null)
    setIsBusy(true)
    try {
      await rejectWorkflowNode(entry.workflow_run_id, entry.id, {
        reason: reason.trim() || null,
      })
      onChanged()
    } catch (error) {
      if (!onApiError(error)) {
        setLocalError(error instanceof WorkflowApiError ? error.message : 'Reject failed.')
      }
    } finally {
      setIsBusy(false)
    }
  }

  return (
    <li className="rounded-lg border border-amber-300 bg-amber-50/70 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-shell-950">{formatApprovalKind(entry.kind)}</h3>
        <span className="text-xs capitalize text-amber-800">
          {formatApprovalStatus(entry.status)}
        </span>
      </div>
      <p className="mt-1 text-xs text-shell-700">Requested {formatTimestamp(entry.requested_at)}</p>
      {entry.workflow_node_id ? (
        <p className="mt-1 text-xs text-shell-600">Node: {entry.workflow_node_id}</p>
      ) : null}

      {entry.kind === 'agent_tool' ? (
        <>
          <label
            className="mt-3 block text-xs font-medium text-shell-800"
            htmlFor={`calls-${entry.id}`}
          >
            Proposed tool calls
          </label>
          <textarea
            id={`calls-${entry.id}`}
            className="mt-1 w-full min-h-28 rounded-lg border border-shell-800/20 bg-white px-2 py-1.5 font-mono text-xs"
            value={editedJson}
            onChange={(event) => setEditedJson(event.target.value)}
            disabled={isBusy}
          />
        </>
      ) : (
        <>
          <label
            className="mt-3 block text-xs font-medium text-shell-800"
            htmlFor={`args-${entry.id}`}
          >
            Edited arguments (JSON object, optional)
          </label>
          <textarea
            id={`args-${entry.id}`}
            className="mt-1 w-full min-h-20 rounded-lg border border-shell-800/20 bg-white px-2 py-1.5 font-mono text-xs"
            value={editedArgumentsJson}
            onChange={(event) => setEditedArgumentsJson(event.target.value)}
            disabled={isBusy}
          />
        </>
      )}

      <label
        className="mt-3 block text-xs font-medium text-shell-800"
        htmlFor={`reason-${entry.id}`}
      >
        Reason (optional)
      </label>
      <textarea
        id={`reason-${entry.id}`}
        className="mt-1 w-full min-h-16 rounded-lg border border-shell-800/20 bg-white px-2 py-1.5 text-xs"
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        disabled={isBusy}
        maxLength={2000}
      />

      {localError ? (
        <p className="mt-2 text-xs font-medium text-danger-600" role="alert">
          {localError}
        </p>
      ) : null}

      <div className="mt-3 flex flex-wrap gap-2">
        {entry.kind === 'agent_tool' ? (
          <>
            <button
              type="button"
              className="rounded-chip border border-shell-800/20 bg-white px-3 py-1.5 text-xs font-semibold"
              onClick={() => void handleAgentSave()}
              disabled={isBusy}
            >
              Save edits
            </button>
            <button
              type="button"
              className="rounded-chip bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white"
              onClick={() => void handleAgentApprove()}
              disabled={isBusy}
            >
              Approve
            </button>
            <button
              type="button"
              className="rounded-chip bg-danger-600 px-3 py-1.5 text-xs font-semibold text-white"
              onClick={() => void handleAgentReject()}
              disabled={isBusy}
            >
              Reject
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              className="rounded-chip bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white"
              onClick={() => void handleWorkflowApprove()}
              disabled={isBusy}
            >
              Approve
            </button>
            <button
              type="button"
              className="rounded-chip bg-danger-600 px-3 py-1.5 text-xs font-semibold text-white"
              onClick={() => void handleWorkflowReject()}
              disabled={isBusy}
            >
              Reject
            </button>
          </>
        )}
      </div>
    </li>
  )
}

function HistoryApprovalCard({ entry }: { entry: ApprovalAuditEntry }) {
  return (
    <li className="rounded-lg border border-shell-800/15 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-shell-950">{formatApprovalKind(entry.kind)}</h3>
        <span className="text-xs capitalize text-shell-700">
          {formatApprovalStatus(entry.status)}
        </span>
      </div>
      <dl className="mt-2 grid gap-1 text-xs text-shell-700 sm:grid-cols-2">
        <div>
          <dt className="font-medium text-shell-600">Decision</dt>
          <dd>{entry.decision ?? '—'}</dd>
        </div>
        <div>
          <dt className="font-medium text-shell-600">Edited</dt>
          <dd>{entry.edited ? 'Yes' : 'No'}</dd>
        </div>
        <div>
          <dt className="font-medium text-shell-600">Decided at</dt>
          <dd>{formatTimestamp(entry.decided_at)}</dd>
        </div>
        <div>
          <dt className="font-medium text-shell-600">Reason</dt>
          <dd>{entry.reason?.trim() ? entry.reason : '—'}</dd>
        </div>
      </dl>
      {entry.tool_calls && entry.tool_calls.length > 0 ? (
        <pre className="mt-2 overflow-x-auto rounded bg-shell-50 p-2 font-mono text-[11px] text-shell-800">
          {callsToJson(entry.tool_calls)}
        </pre>
      ) : null}
      {entry.revision_count > 0 ? <RevisionHistory approvalId={entry.id} /> : null}
    </li>
  )
}

function ApprovalsContent() {
  const { handleInvalidAccessToken } = useAuthContext()
  const [tab, setTab] = useState<ApprovalsTab>('pending')
  const [pending, setPending] = useState<ApprovalAuditEntry[]>([])
  const [history, setHistory] = useState<ApprovalAuditEntry[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [featureDisabled, setFeatureDisabled] = useState(false)

  const handleApiError = useCallback(
    (apiError: unknown): boolean => {
      if (isInvalidAccessTokenError(apiError)) {
        handleInvalidAccessToken()
        return true
      }
      if (apiError instanceof ApprovalsApiError && apiError.code === 'feature_disabled') {
        setFeatureDisabled(true)
        return true
      }
      return false
    },
    [handleInvalidAccessToken],
  )

  const reload = useCallback(async () => {
    setIsLoading(true)
    setError(null)
    setFeatureDisabled(false)
    try {
      const [pendingResponse, allResponse] = await Promise.all([
        fetchApprovals({ status: 'pending', limit: 50 }),
        fetchApprovals({ limit: 100 }),
      ])
      setPending(pendingResponse.approvals)
      setHistory(allResponse.approvals.filter((entry) => entry.status !== 'pending'))
    } catch (apiError) {
      if (!handleApiError(apiError)) {
        setError(approvalsPageErrorMessage(apiError))
      }
      setPending([])
      setHistory([])
    } finally {
      setIsLoading(false)
    }
  }, [handleApiError])

  useEffect(() => {
    let cancelled = false

    void (async () => {
      setIsLoading(true)
      setError(null)
      setFeatureDisabled(false)
      try {
        const [pendingResponse, allResponse] = await Promise.all([
          fetchApprovals({ status: 'pending', limit: 50 }),
          fetchApprovals({ limit: 100 }),
        ])
        if (!cancelled) {
          setPending(pendingResponse.approvals)
          setHistory(allResponse.approvals.filter((entry) => entry.status !== 'pending'))
        }
      } catch (apiError) {
        if (!cancelled) {
          if (!handleApiError(apiError)) {
            setError(approvalsPageErrorMessage(apiError))
          }
          setPending([])
          setHistory([])
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false)
        }
      }
    })()

    return () => {
      cancelled = true
    }
  }, [handleApiError])

  if (featureDisabled) {
    return <ApprovalsUnavailableNotice />
  }

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-6 px-3 py-4 sm:px-4 sm:py-6">
      {error ? (
        <div
          className="rounded-lg border border-danger-600/30 bg-danger-100 px-3 py-2 text-sm text-danger-600"
          role="alert"
        >
          {error}
        </div>
      ) : null}

      <section className="rounded-chat border border-shell-800/15 bg-white p-4 shadow-chat-card sm:p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-base font-semibold text-shell-950">Approval inbox</h2>
          <button
            type="button"
            className="rounded-lg border border-shell-800/20 px-3 py-1.5 text-xs font-semibold text-shell-800 hover:bg-shell-50"
            onClick={() => void reload()}
            disabled={isLoading}
          >
            Refresh
          </button>
        </div>
        <div className="mt-4 flex gap-2" role="tablist" aria-label="Approval views">
          {(['pending', 'history'] as const).map((value) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={tab === value}
              className={`rounded-lg px-3 py-1.5 text-xs font-semibold capitalize ${
                tab === value
                  ? 'bg-brand-600 text-white'
                  : 'border border-shell-800/20 text-shell-800 hover:bg-shell-50'
              }`}
              onClick={() => setTab(value)}
            >
              {value}
            </button>
          ))}
        </div>

        {isLoading ? (
          <LoadingIndicator variant="inline" label="Loading approvals…" className="mt-4" />
        ) : tab === 'pending' ? (
          pending.length === 0 ? (
            <EmptyState
              className="mt-4 border-shell-800/20 bg-shell-50/80"
              title="No pending approvals"
              description="When a tool call or workflow node needs your decision, it will appear here."
            />
          ) : (
            <ul className="mt-4 flex flex-col gap-3" aria-label="Pending approvals">
              {pending.map((entry) => (
                <PendingApprovalCard
                  key={entry.id}
                  entry={entry}
                  onChanged={() => void reload()}
                  onApiError={handleApiError}
                />
              ))}
            </ul>
          )
        ) : history.length === 0 ? (
          <EmptyState
            className="mt-4 border-shell-800/20 bg-shell-50/80"
            title="No approval history yet"
            description="Decided approvals from agent and workflow surfaces appear here."
          />
        ) : (
          <ul className="mt-4 flex flex-col gap-3" aria-label="Approval history">
            {history.map((entry) => (
              <HistoryApprovalCard key={entry.id} entry={entry} />
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}

export function ApprovalsPage() {
  const { hitlEnabled, healthLoading } = useChatStreamingEnabled()

  return (
    <div className="min-h-dvh bg-linear-to-b from-shell-50 via-shell-100 to-[#ebeff6]">
      <header className="sticky top-0 z-20 border-b border-shell-800/15 bg-shell-50/90 px-3 py-2 backdrop-blur sm:px-4">
        <div className="mx-auto flex max-w-4xl items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <AppNav current="approvals" />
            <h1 className="min-w-0 truncate text-sm font-semibold tracking-wide text-shell-900 sm:text-base">
              Approvals
            </h1>
          </div>
          <div className="shrink-0">
            <AuthControls />
          </div>
        </div>
      </header>

      {healthLoading ? (
        <div className="mx-auto flex w-full max-w-4xl justify-center px-3 py-12 sm:px-4">
          <LoadingIndicator variant="inline" label="Loading approvals…" />
        </div>
      ) : hitlEnabled ? (
        <ApprovalsContent />
      ) : (
        <ApprovalsUnavailableNotice />
      )}
    </div>
  )
}
