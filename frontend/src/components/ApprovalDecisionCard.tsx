import { useCallback, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ApprovalsApiError,
  rejectApproval,
  reviseApproval,
  streamApproveApproval,
} from '../api/approvalsClient'
import type { ProposedToolCall } from '../types/approvals'
import type { ChatChunk, PendingApprovalContext } from '../types/chat'

interface ApprovalDecisionCardProps {
  messageId: string
  pendingApproval: PendingApprovalContext
  onApproveStream?: {
    onDelta: (content: string) => void
    onComplete: () => void
    onError: (message: string) => void
    onApprovalRequired?: (chunk: Extract<ChatChunk, { type: 'approval_required' }>) => void
  }
  onRejected: () => void
  onRevised?: (calls: ProposedToolCall[]) => void
}

function callsToJson(calls: ProposedToolCall[]): string {
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
    const name = record.name
    const callId = record.call_id
    if (typeof name !== 'string' || !name.trim()) {
      throw new Error(`Entry ${index} requires a non-empty name.`)
    }
    if (typeof callId !== 'string' || !callId.trim()) {
      throw new Error(`Entry ${index} requires a non-empty call_id.`)
    }
    const args = record.arguments
    return {
      name,
      call_id: callId,
      arguments:
        typeof args === 'object' && args !== null && !Array.isArray(args)
          ? (args as Record<string, unknown>)
          : {},
    }
  })
}

export function ApprovalDecisionCard(props: ApprovalDecisionCardProps) {
  return <ApprovalDecisionCardContent key={props.pendingApproval.approvalId} {...props} />
}

function ApprovalDecisionCardContent({
  messageId,
  pendingApproval,
  onApproveStream,
  onRejected,
  onRevised,
}: ApprovalDecisionCardProps) {
  const initialCalls: ProposedToolCall[] = pendingApproval.proposedCalls.map((call) => ({
    name: call.name,
    call_id: call.call_id,
    arguments: call.arguments,
  }))

  const [editedJson, setEditedJson] = useState(() => callsToJson(initialCalls))
  const [reason, setReason] = useState('')
  const [actionError, setActionError] = useState<string | null>(null)
  const [isSaving, setIsSaving] = useState(false)
  const [isDeciding, setIsDeciding] = useState(false)
  const [saveSuccess, setSaveSuccess] = useState<string | null>(null)

  const handleSaveEdits = useCallback(async () => {
    setActionError(null)
    setSaveSuccess(null)
    let editedCalls: ProposedToolCall[]
    try {
      editedCalls = parseEditedCalls(editedJson)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : 'Invalid JSON.')
      return
    }

    setIsSaving(true)
    try {
      await reviseApproval(pendingApproval.approvalId, {
        edited_calls: editedCalls,
        note: reason.trim() || null,
      })
      setSaveSuccess('Edits saved.')
      onRevised?.(editedCalls)
    } catch (error) {
      setActionError(error instanceof ApprovalsApiError ? error.message : 'Could not save edits.')
    } finally {
      setIsSaving(false)
    }
  }, [editedJson, onRevised, pendingApproval.approvalId, reason])

  const resolveEditedCalls = (): ProposedToolCall[] | null => {
    try {
      return parseEditedCalls(editedJson)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : 'Invalid JSON.')
      return null
    }
  }

  const handleReject = async () => {
    setActionError(null)
    setIsDeciding(true)
    try {
      await rejectApproval(pendingApproval.approvalId, reason.trim() || null)
      onRejected()
    } catch (error) {
      setActionError(error instanceof ApprovalsApiError ? error.message : 'Reject failed.')
    } finally {
      setIsDeciding(false)
    }
  }

  const handleApprove = async () => {
    setActionError(null)
    const editedCalls = resolveEditedCalls()
    if (!editedCalls) {
      return
    }

    setIsDeciding(true)
    try {
      await streamApproveApproval(
        pendingApproval.approvalId,
        {
          edited_calls: editedCalls,
          reason: reason.trim() || null,
        },
        {
          onDelta: (chunk) => {
            onApproveStream?.onDelta(chunk.content)
          },
          onEnd: () => {
            onApproveStream?.onComplete()
          },
          onApprovalRequired: (chunk) => {
            onApproveStream?.onApprovalRequired?.(chunk)
          },
          onError: (error) => {
            const message =
              error instanceof ApprovalsApiError
                ? error.message
                : 'type' in error && error.type === 'error'
                  ? error.message
                  : error instanceof Error
                    ? error.message
                    : 'Approval stream failed.'
            onApproveStream?.onError(message)
            setActionError(message)
          },
        },
      )
    } catch (error) {
      setActionError(error instanceof ApprovalsApiError ? error.message : 'Approve failed.')
    } finally {
      setIsDeciding(false)
    }
  }

  return (
    <div
      className="mt-3 rounded-xl border border-amber-300 bg-amber-50/90 p-3"
      role="region"
      aria-label="Tool approval required"
      data-message-id={messageId}
    >
      <p className="text-xs font-semibold uppercase tracking-wide text-amber-900">
        Approval required
      </p>
      <p className="mt-1 text-xs text-amber-800">
        Review proposed tool arguments before execution.{' '}
        <Link
          to="/approvals"
          className="font-semibold text-brand-700 underline-offset-2 hover:underline"
        >
          Open inbox
        </Link>
      </p>

      <label
        className="mt-3 block text-xs font-medium text-shell-800"
        htmlFor={`approval-json-${messageId}`}
      >
        Proposed tool calls (JSON)
      </label>
      <textarea
        id={`approval-json-${messageId}`}
        className="mt-1 w-full min-h-28 rounded-lg border border-shell-800/20 bg-white px-2 py-1.5 font-mono text-xs text-shell-900"
        value={editedJson}
        onChange={(event) => setEditedJson(event.target.value)}
        disabled={isDeciding}
      />

      <label
        className="mt-3 block text-xs font-medium text-shell-800"
        htmlFor={`approval-reason-${messageId}`}
      >
        Reason (optional)
      </label>
      <textarea
        id={`approval-reason-${messageId}`}
        className="mt-1 w-full min-h-16 rounded-lg border border-shell-800/20 bg-white px-2 py-1.5 text-xs text-shell-900"
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        disabled={isDeciding}
        maxLength={2000}
      />

      {actionError ? (
        <p className="mt-2 text-xs font-medium text-danger-600" role="alert">
          {actionError}
        </p>
      ) : null}
      {saveSuccess ? (
        <p className="mt-2 text-xs font-medium text-brand-700" role="status">
          {saveSuccess}
        </p>
      ) : null}

      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          className="rounded-chip border border-shell-800/20 bg-white px-3 py-1.5 text-xs font-semibold text-shell-900 transition hover:bg-shell-50 disabled:opacity-60"
          onClick={() => void handleSaveEdits()}
          disabled={isSaving || isDeciding}
        >
          {isSaving ? 'Saving…' : 'Save edits'}
        </button>
        <button
          type="button"
          className="rounded-chip bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-brand-500 disabled:opacity-60"
          onClick={() => void handleApprove()}
          disabled={isDeciding || isSaving}
        >
          {isDeciding ? 'Approving…' : 'Approve'}
        </button>
        <button
          type="button"
          className="rounded-chip bg-danger-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-danger-500 disabled:opacity-60"
          onClick={() => void handleReject()}
          disabled={isDeciding || isSaving}
        >
          Reject
        </button>
      </div>
    </div>
  )
}
