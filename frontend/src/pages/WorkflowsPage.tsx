import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  approveWorkflowNode,
  cancelWorkflowRun,
  createWorkflowDefinition,
  getWorkflowRun,
  listWorkflowDefinitions,
  listWorkflowRuns,
  rejectWorkflowNode,
  resumeWorkflowRun,
  startWorkflowRun,
  WorkflowApiError,
} from '../api/workflowClient'
import { AppNav } from '../components/AppNav'
import { AuthControls } from '../components/AuthControls'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { EmptyState } from '../components/EmptyState'
import { LoadingIndicator } from '../components/LoadingIndicator'
import { useAuthContext } from '../context/AuthContext'
import { useChatStreamingEnabled } from '../hooks/useChatStreamingEnabled'
import type {
  WorkflowDefinition,
  WorkflowDefinitionCreateRequest,
  WorkflowNodeExecution,
  WorkflowRun,
  WorkflowRunDetail,
} from '../types/workflow'
import { formatWorkflowJson } from '../types/workflow'

const SAMPLE_DEFINITION_JSON = `{
  "name": "Sample Workflow",
  "status": "active",
  "entry_node_id": "start",
  "nodes": [
    { "id": "start", "type": "task", "config": {} },
    { "id": "end", "type": "terminal", "config": {} }
  ],
  "edges": [
    { "id": "e1", "from_node_id": "start", "to_node_id": "end" }
  ]
}`

function isInvalidAccessTokenError(error: unknown): boolean {
  return (
    error instanceof WorkflowApiError &&
    (error.code === 'invalid_access_token' || error.status === 401)
  )
}

function formatRunStatus(status: WorkflowRun['status']): string {
  return status.replace(/_/g, ' ')
}

function runStatusClass(status: WorkflowRun['status']): string {
  switch (status) {
    case 'waiting_approval':
      return 'bg-amber-100 text-amber-800 border-amber-300'
    case 'completed':
      return 'bg-brand-100 text-brand-800 border-brand-300'
    case 'failed':
      return 'bg-danger-100 text-danger-700 border-danger-300'
    case 'cancelled':
      return 'bg-shell-200 text-shell-700 border-shell-400'
    case 'running':
      return 'bg-blue-100 text-blue-800 border-blue-300'
    default:
      return 'bg-shell-100 text-shell-800 border-shell-300'
  }
}

interface StatusBannerProps {
  tone: 'success' | 'error'
  message: string
}

function StatusBanner({ tone, message }: StatusBannerProps) {
  const toneClass =
    tone === 'success'
      ? 'border-brand-500/30 bg-brand-500/10 text-brand-700'
      : 'border-danger-600/30 bg-danger-100 text-danger-600'

  return (
    <div
      className={`rounded-lg border px-3 py-2 text-sm ${toneClass}`}
      role={tone === 'error' ? 'alert' : 'status'}
      aria-live="polite"
    >
      {message}
    </div>
  )
}

function WorkflowUnavailableNotice() {
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-3 py-8 sm:px-4">
      <section className="rounded-chat border border-shell-800/15 bg-white p-5 shadow-chat-card">
        <h2 className="text-base font-semibold text-shell-950">Workflows are not available</h2>
        <p className="mt-2 text-sm text-shell-700">
          The Workflow Engine is disabled on this server. Your chat experience is unchanged.
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

function findPendingApprovalExecution(
  executions: WorkflowNodeExecution[],
): WorkflowNodeExecution | undefined {
  return executions.find((execution) => execution.status === 'waiting_approval')
}

function WorkflowPendingApprovalPanel({
  execution,
  runId,
  isActingOnRun,
  onApprove,
  onRejectClick,
  onValidationError,
}: {
  execution: WorkflowNodeExecution
  runId: string
  isActingOnRun: boolean
  onApprove: (
    runId: string,
    executionId: string,
    body: { edited_arguments: Record<string, unknown>; reason: string | null },
  ) => Promise<void>
  onRejectClick: (reason: string | null) => void
  onValidationError: (message: string) => void
}) {
  const [approvalReason, setApprovalReason] = useState('')
  const [approvalEditedArgumentsJson, setApprovalEditedArgumentsJson] = useState(() =>
    execution.input && typeof execution.input === 'object'
      ? JSON.stringify(execution.input, null, 2)
      : '{}',
  )

  const handleApproveClick = async () => {
    try {
      const parsed = JSON.parse(approvalEditedArgumentsJson) as unknown
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
        onValidationError('Edited arguments must be a JSON object.')
        return
      }
      await onApprove(runId, execution.id, {
        edited_arguments: parsed as Record<string, unknown>,
        reason: approvalReason.trim() || null,
      })
    } catch (error) {
      onValidationError(error instanceof Error ? error.message : 'Invalid edited arguments JSON.')
    }
  }

  return (
    <div
      className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-4"
      role="region"
      aria-labelledby="pending-approval-heading"
    >
      <h3 id="pending-approval-heading" className="text-sm font-semibold text-amber-900">
        Pending approval — {execution.node_id}
      </h3>
      <pre className="mt-2 overflow-x-auto rounded bg-white/80 p-2 text-xs text-shell-800">
        {formatWorkflowJson(execution.input)}
      </pre>
      <label
        className="mt-3 block text-xs font-medium text-amber-900"
        htmlFor="workflow-approval-edited-args"
      >
        Edited arguments (JSON, optional)
      </label>
      <textarea
        id="workflow-approval-edited-args"
        className="mt-1 w-full min-h-24 rounded-lg border border-amber-200 bg-white px-2 py-1.5 font-mono text-xs text-shell-900"
        value={approvalEditedArgumentsJson}
        onChange={(event) => setApprovalEditedArgumentsJson(event.target.value)}
        disabled={isActingOnRun}
      />
      <label
        className="mt-3 block text-xs font-medium text-amber-900"
        htmlFor="workflow-approval-reason"
      >
        Reason (optional)
      </label>
      <textarea
        id="workflow-approval-reason"
        className="mt-1 w-full min-h-16 rounded-lg border border-amber-200 bg-white px-2 py-1.5 text-xs text-shell-900"
        value={approvalReason}
        onChange={(event) => setApprovalReason(event.target.value)}
        disabled={isActingOnRun}
        maxLength={2000}
      />
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          className="rounded-chat bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-brand-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:opacity-60"
          onClick={() => void handleApproveClick()}
          disabled={isActingOnRun}
        >
          Approve
        </button>
        <button
          type="button"
          className="rounded-chat bg-danger-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-danger-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger-500 disabled:opacity-60"
          onClick={() => onRejectClick(approvalReason.trim() || null)}
          disabled={isActingOnRun}
        >
          Reject
        </button>
      </div>
    </div>
  )
}

function WorkflowsContent() {
  const { handleInvalidAccessToken } = useAuthContext()
  const [definitions, setDefinitions] = useState<WorkflowDefinition[]>([])
  const [runs, setRuns] = useState<WorkflowRun[]>([])
  const [selectedDefinitionId, setSelectedDefinitionId] = useState<string | null>(null)
  const [selectedRunDetail, setSelectedRunDetail] = useState<WorkflowRunDetail | null>(null)
  const [definitionJson, setDefinitionJson] = useState(SAMPLE_DEFINITION_JSON)
  const [jsonValidationError, setJsonValidationError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isRefreshingRun, setIsRefreshingRun] = useState(false)
  const [isCreatingDefinition, setIsCreatingDefinition] = useState(false)
  const [isStartingRun, setIsStartingRun] = useState(false)
  const [isActingOnRun, setIsActingOnRun] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [rejectConfirmOpen, setRejectConfirmOpen] = useState(false)
  const [pendingRejectReason, setPendingRejectReason] = useState<string | null>(null)

  const definitionNameById = useMemo(() => {
    const map = new Map<string, string>()
    for (const definition of definitions) {
      map.set(definition.id, definition.name)
    }
    return map
  }, [definitions])

  const filteredRuns = useMemo(() => {
    if (!selectedDefinitionId) {
      return runs
    }
    return runs.filter((run) => run.workflow_definition_id === selectedDefinitionId)
  }, [runs, selectedDefinitionId])

  const pendingApprovalExecution = selectedRunDetail
    ? findPendingApprovalExecution(selectedRunDetail.node_executions)
    : undefined

  const handleApiError = useCallback(
    (apiError: unknown): boolean => {
      if (isInvalidAccessTokenError(apiError)) {
        handleInvalidAccessToken()
        return true
      }
      if (apiError instanceof WorkflowApiError) {
        if (apiError.code === 'feature_disabled') {
          setError('The Workflow Engine is not enabled on this server.')
          return true
        }
        if (apiError.code === 'workflow_validation_error') {
          setError(apiError.message)
          return true
        }
        setError(apiError.message)
        return true
      }
      setError('Something went wrong. Please try again.')
      return true
    },
    [handleInvalidAccessToken],
  )

  const loadDefinitionsAndRuns = useCallback(async () => {
    const [definitionList, runList] = await Promise.all([
      listWorkflowDefinitions(),
      listWorkflowRuns(),
    ])
    setDefinitions(definitionList.definitions)
    setRuns(runList.runs)
  }, [])

  const refreshRunDetail = useCallback(async (runId: string) => {
    setIsRefreshingRun(true)
    try {
      const detail = await getWorkflowRun(runId)
      setSelectedRunDetail(detail)
      setRuns((current) =>
        current.map((run) => (run.id === detail.id ? { ...run, ...detail } : run)),
      )
    } finally {
      setIsRefreshingRun(false)
    }
  }, [])

  const selectRun = useCallback(
    async (runId: string) => {
      setError(null)
      try {
        await refreshRunDetail(runId)
      } catch (apiError) {
        handleApiError(apiError)
      }
    },
    [handleApiError, refreshRunDetail],
  )

  useEffect(() => {
    let cancelled = false

    void (async () => {
      setIsLoading(true)
      setError(null)
      try {
        await loadDefinitionsAndRuns()
      } catch (apiError) {
        if (!cancelled) {
          handleApiError(apiError)
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
  }, [handleApiError, loadDefinitionsAndRuns])

  const parseDefinitionPayload = (): WorkflowDefinitionCreateRequest | null => {
    try {
      const parsed = JSON.parse(definitionJson) as unknown
      if (typeof parsed !== 'object' || parsed === null) {
        setJsonValidationError('Definition JSON must be an object.')
        return null
      }
      const payload = parsed as WorkflowDefinitionCreateRequest
      if (!payload.name || !payload.entry_node_id || !Array.isArray(payload.nodes)) {
        setJsonValidationError('JSON must include name, entry_node_id, and nodes.')
        return null
      }
      setJsonValidationError(null)
      return payload
    } catch {
      setJsonValidationError('Invalid JSON. Check syntax and try again.')
      return null
    }
  }

  const handleCreateDefinition = async () => {
    const payload = parseDefinitionPayload()
    if (!payload) {
      return
    }

    setIsCreatingDefinition(true)
    setError(null)
    setSuccess(null)
    try {
      const created = await createWorkflowDefinition(payload)
      setSuccess(`Created workflow "${created.name}".`)
      await loadDefinitionsAndRuns()
    } catch (apiError) {
      handleApiError(apiError)
    } finally {
      setIsCreatingDefinition(false)
    }
  }

  const handleStartRun = async (definitionId: string) => {
    setIsStartingRun(true)
    setError(null)
    setSuccess(null)
    try {
      const run = await startWorkflowRun(definitionId, {
        idempotency_key: crypto.randomUUID(),
        trigger_input: {},
      })
      setSuccess('Workflow run started.')
      await loadDefinitionsAndRuns()
      await selectRun(run.id)
    } catch (apiError) {
      handleApiError(apiError)
    } finally {
      setIsStartingRun(false)
    }
  }

  const handleCancelRun = async () => {
    if (!selectedRunDetail) {
      return
    }
    setIsActingOnRun(true)
    setError(null)
    setSuccess(null)
    try {
      await cancelWorkflowRun(selectedRunDetail.id)
      setSuccess('Workflow run cancelled.')
      await loadDefinitionsAndRuns()
      await refreshRunDetail(selectedRunDetail.id)
    } catch (apiError) {
      handleApiError(apiError)
    } finally {
      setIsActingOnRun(false)
    }
  }

  const handleResumeRun = async () => {
    if (!selectedRunDetail) {
      return
    }
    setIsActingOnRun(true)
    setError(null)
    setSuccess(null)
    try {
      await resumeWorkflowRun(selectedRunDetail.id)
      setSuccess('Workflow run resume requested.')
      await loadDefinitionsAndRuns()
      await refreshRunDetail(selectedRunDetail.id)
    } catch (apiError) {
      handleApiError(apiError)
    } finally {
      setIsActingOnRun(false)
    }
  }

  const handleApprove = async (
    runId: string,
    nodeExecutionId: string,
    body: { edited_arguments: Record<string, unknown>; reason: string | null },
  ) => {
    setIsActingOnRun(true)
    setError(null)
    setSuccess(null)
    try {
      await approveWorkflowNode(runId, nodeExecutionId, {
        edited_arguments: body.edited_arguments,
        reason: body.reason,
      })
      setSuccess('Approval submitted.')
      await loadDefinitionsAndRuns()
      await refreshRunDetail(runId)
    } catch (apiError) {
      handleApiError(apiError)
    } finally {
      setIsActingOnRun(false)
    }
  }

  const handleReject = async (runId: string, nodeExecutionId: string, reason: string | null) => {
    setIsActingOnRun(true)
    setError(null)
    setSuccess(null)
    try {
      await rejectWorkflowNode(runId, nodeExecutionId, { reason })
      setSuccess('Rejection submitted.')
      await loadDefinitionsAndRuns()
      await refreshRunDetail(runId)
    } catch (apiError) {
      handleApiError(apiError)
    } finally {
      setIsActingOnRun(false)
    }
  }

  const handleRejectConfirmed = async () => {
    if (!selectedRunDetail || !pendingApprovalExecution) {
      return
    }
    setRejectConfirmOpen(false)
    await handleReject(selectedRunDetail.id, pendingApprovalExecution.id, pendingRejectReason)
    setPendingRejectReason(null)
  }

  const canCancelRun =
    selectedRunDetail?.status === 'running' || selectedRunDetail?.status === 'waiting_approval'
  const canResumeRun = selectedRunDetail?.status === 'running'

  if (isLoading) {
    return (
      <div className="mx-auto flex w-full max-w-3xl justify-center px-3 py-12 sm:px-4">
        <LoadingIndicator variant="inline" label="Loading workflows…" />
      </div>
    )
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-3 py-6 sm:px-4">
      {error ? <StatusBanner tone="error" message={error} /> : null}
      {success ? <StatusBanner tone="success" message={success} /> : null}

      <section
        aria-labelledby="workflow-definitions-heading"
        className="rounded-chat border border-shell-800/15 bg-white p-5 shadow-chat-card"
      >
        <h2 id="workflow-definitions-heading" className="text-base font-semibold text-shell-950">
          Definitions
        </h2>
        <p className="mt-1 text-sm text-shell-700">
          Create workflow graphs from JSON. No visual builder in v1.
        </p>

        <label htmlFor="definition-json" className="mt-4 block text-sm font-medium text-shell-900">
          Graph JSON
        </label>
        <textarea
          id="definition-json"
          className="mt-1 w-full rounded-lg border border-shell-800/20 bg-shell-50 px-3 py-2 font-mono text-xs text-shell-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
          rows={10}
          value={definitionJson}
          onChange={(event) => {
            setDefinitionJson(event.target.value)
            setJsonValidationError(null)
          }}
          aria-invalid={jsonValidationError !== null}
          aria-describedby={jsonValidationError ? 'definition-json-error' : undefined}
        />
        {jsonValidationError ? (
          <p id="definition-json-error" className="mt-1 text-sm text-danger-600" role="alert">
            {jsonValidationError}
          </p>
        ) : null}
        <button
          type="button"
          className="mt-3 rounded-chat bg-brand-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-brand-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:opacity-60"
          onClick={() => void handleCreateDefinition()}
          disabled={isCreatingDefinition}
        >
          {isCreatingDefinition ? 'Creating…' : 'Create definition'}
        </button>

        {definitions.length === 0 ? (
          <EmptyState
            className="mt-4"
            title="No definitions yet"
            description="Create a workflow definition to start orchestrating multi-step runs."
          />
        ) : (
          <ul className="mt-4 flex flex-col gap-2" aria-label="Workflow definitions">
            {definitions.map((definition) => {
              const isSelected = selectedDefinitionId === definition.id
              return (
                <li
                  key={definition.id}
                  className={[
                    'rounded-lg border px-3 py-2',
                    isSelected ? 'border-brand-400 bg-brand-50' : 'border-shell-800/15 bg-shell-50',
                  ].join(' ')}
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <button
                      type="button"
                      className="text-left text-sm font-semibold text-shell-950 underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                      onClick={() => setSelectedDefinitionId(isSelected ? null : definition.id)}
                      aria-pressed={isSelected}
                    >
                      {definition.name}
                    </button>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-xs text-shell-600">
                        v{definition.version} · {definition.status}
                      </span>
                      <button
                        type="button"
                        className="rounded-lg border border-brand-500/40 px-2 py-1 text-xs font-semibold text-brand-700 transition hover:bg-brand-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:opacity-60"
                        onClick={() => void handleStartRun(definition.id)}
                        disabled={isStartingRun}
                      >
                        Start run
                      </button>
                    </div>
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </section>

      <section
        aria-labelledby="workflow-runs-heading"
        className="rounded-chat border border-shell-800/15 bg-white p-5 shadow-chat-card"
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="workflow-runs-heading" className="text-base font-semibold text-shell-950">
            Runs
            {selectedDefinitionId ? (
              <span className="ml-2 text-sm font-normal text-shell-600">
                (filtered by {definitionNameById.get(selectedDefinitionId) ?? 'definition'})
              </span>
            ) : null}
          </h2>
          {selectedDefinitionId ? (
            <button
              type="button"
              className="text-xs font-semibold text-brand-600 underline-offset-2 hover:underline"
              onClick={() => setSelectedDefinitionId(null)}
            >
              Show all runs
            </button>
          ) : null}
        </div>

        {filteredRuns.length === 0 ? (
          <EmptyState
            className="mt-4"
            title="No runs yet"
            description="Start a run from a definition to inspect execution history here."
          />
        ) : (
          <ul className="mt-4 flex flex-col gap-2" aria-label="Workflow runs">
            {filteredRuns.map((run) => {
              const isSelected = selectedRunDetail?.id === run.id
              const definitionName =
                definitionNameById.get(run.workflow_definition_id) ?? run.workflow_definition_id
              return (
                <li key={run.id}>
                  <button
                    type="button"
                    className={[
                      'flex w-full flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-2 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
                      isSelected
                        ? 'border-brand-400 bg-brand-50'
                        : 'border-shell-800/15 bg-shell-50 hover:bg-shell-100',
                      run.status === 'waiting_approval' ? 'ring-2 ring-amber-400/60' : '',
                    ].join(' ')}
                    onClick={() => void selectRun(run.id)}
                    aria-pressed={isSelected}
                  >
                    <span className="text-sm font-medium text-shell-950">{definitionName}</span>
                    <span
                      className={[
                        'rounded-full border px-2 py-0.5 text-xs font-semibold capitalize',
                        runStatusClass(run.status),
                      ].join(' ')}
                    >
                      {formatRunStatus(run.status)}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </section>

      {selectedRunDetail ? (
        <section
          aria-labelledby="workflow-run-detail-heading"
          className="rounded-chat border border-shell-800/15 bg-white p-5 shadow-chat-card"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 id="workflow-run-detail-heading" className="text-base font-semibold text-shell-950">
              Run detail
            </h2>
            <span
              className={[
                'rounded-full border px-2 py-0.5 text-xs font-semibold capitalize',
                runStatusClass(selectedRunDetail.status),
              ].join(' ')}
            >
              {formatRunStatus(selectedRunDetail.status)}
            </span>
          </div>

          {selectedRunDetail.error ? (
            <p className="mt-2 text-sm text-danger-600" role="alert">
              {selectedRunDetail.error}
            </p>
          ) : null}

          <div className="mt-3 flex flex-wrap gap-2">
            {canCancelRun ? (
              <button
                type="button"
                className="rounded-lg border border-danger-500/40 px-3 py-1.5 text-xs font-semibold text-danger-700 transition hover:bg-danger-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger-500 disabled:opacity-60"
                onClick={() => void handleCancelRun()}
                disabled={isActingOnRun}
              >
                Cancel run
              </button>
            ) : null}
            {canResumeRun ? (
              <button
                type="button"
                className="rounded-lg border border-brand-500/40 px-3 py-1.5 text-xs font-semibold text-brand-700 transition hover:bg-brand-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:opacity-60"
                onClick={() => void handleResumeRun()}
                disabled={isActingOnRun}
              >
                Resume run
              </button>
            ) : null}
            <button
              type="button"
              className="rounded-lg border border-shell-800/20 px-3 py-1.5 text-xs font-semibold text-shell-800 transition hover:bg-shell-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:opacity-60"
              onClick={() => void refreshRunDetail(selectedRunDetail.id)}
              disabled={isRefreshingRun || isActingOnRun}
            >
              {isRefreshingRun ? 'Refreshing…' : 'Refresh'}
            </button>
          </div>

          {pendingApprovalExecution && selectedRunDetail ? (
            <WorkflowPendingApprovalPanel
              key={pendingApprovalExecution.id}
              execution={pendingApprovalExecution}
              runId={selectedRunDetail.id}
              isActingOnRun={isActingOnRun}
              onApprove={handleApprove}
              onRejectClick={(reason) => {
                setPendingRejectReason(reason)
                setRejectConfirmOpen(true)
              }}
              onValidationError={(message) => setError(message)}
            />
          ) : null}

          <h3 className="mt-5 text-sm font-semibold text-shell-950">Node executions</h3>
          {isRefreshingRun ? (
            <LoadingIndicator variant="inline" label="Loading run detail…" />
          ) : selectedRunDetail.node_executions.length === 0 ? (
            <p className="mt-2 text-sm text-shell-600">No node executions recorded yet.</p>
          ) : (
            <ol className="mt-3 flex flex-col gap-3" aria-label="Node execution history">
              {selectedRunDetail.node_executions.map((execution) => (
                <li
                  key={execution.id}
                  className="rounded-lg border border-shell-800/15 bg-shell-50 p-3"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-sm font-medium text-shell-950">
                      {execution.node_id}{' '}
                      <span className="font-normal text-shell-600">({execution.node_type})</span>
                    </span>
                    <span className="text-xs capitalize text-shell-700">{execution.status}</span>
                  </div>
                  <details className="mt-2">
                    <summary className="cursor-pointer text-xs font-semibold text-brand-700">
                      Input / output
                    </summary>
                    <div className="mt-2 grid gap-2 sm:grid-cols-2">
                      <div>
                        <p className="text-xs font-medium text-shell-700">Input</p>
                        <pre className="mt-1 overflow-x-auto rounded bg-white p-2 text-xs text-shell-800">
                          {formatWorkflowJson(execution.input)}
                        </pre>
                      </div>
                      {execution.output !== null ? (
                        <div>
                          <p className="text-xs font-medium text-shell-700">Output</p>
                          <pre className="mt-1 overflow-x-auto rounded bg-white p-2 text-xs text-shell-800">
                            {formatWorkflowJson(execution.output)}
                          </pre>
                        </div>
                      ) : null}
                    </div>
                  </details>
                  {execution.error ? (
                    <p className="mt-2 text-xs text-danger-600">{execution.error}</p>
                  ) : null}
                </li>
              ))}
            </ol>
          )}
        </section>
      ) : null}

      <ConfirmDialog
        open={rejectConfirmOpen}
        title="Reject approval?"
        message="Rejecting will fail this approval node and stop the workflow run. This cannot be undone."
        confirmLabel="Reject"
        cancelLabel="Cancel"
        isDestructive
        onConfirm={() => void handleRejectConfirmed()}
        onCancel={() => setRejectConfirmOpen(false)}
      />
    </div>
  )
}

export function WorkflowsPage() {
  const { workflowEngineEnabled, healthLoading } = useChatStreamingEnabled()

  return (
    <div className="min-h-dvh bg-linear-to-b from-shell-50 via-shell-100 to-[#ebeff6]">
      <header className="sticky top-0 z-20 border-b border-shell-800/10 bg-white/90 backdrop-blur-sm">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-2 px-3 py-3 sm:px-4">
          <div className="flex min-w-0 items-center gap-2">
            <AppNav current="workflows" />
            <h1 className="truncate text-lg font-semibold text-shell-950">Workflows</h1>
          </div>
          <AuthControls />
        </div>
      </header>

      {healthLoading ? (
        <div className="mx-auto flex w-full max-w-3xl justify-center px-3 py-12 sm:px-4">
          <LoadingIndicator variant="inline" label="Loading workflows…" />
        </div>
      ) : workflowEngineEnabled ? (
        <WorkflowsContent />
      ) : (
        <WorkflowUnavailableNotice />
      )}
    </div>
  )
}
