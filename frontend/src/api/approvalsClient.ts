import type { ChatChunk } from '../types/chat'
import type {
  ApprovalAuditEntry,
  ApprovalAuditListResponse,
  ApprovalDecideRequest,
  ApprovalResult,
  ApprovalRevision,
  ApprovalReviseRequest,
} from '../types/approvals'
import { API_BASE_URL, buildAuthHeaders, captureRequestId, parseErrorEnvelope } from './request'
import { SseParser } from './sseParser'

export class ApprovalsApiError extends Error {
  status: number
  code?: string

  constructor(message: string, status: number, code?: string) {
    super(message)
    this.name = 'ApprovalsApiError'
    this.status = status
    this.code = code
  }
}

async function toApprovalsApiError(
  response: Response,
  fallbackMessage: string,
): Promise<ApprovalsApiError> {
  const parsed = await parseErrorEnvelope(response, fallbackMessage)
  return new ApprovalsApiError(parsed.message, parsed.status, parsed.code)
}

export interface FetchApprovalsOptions {
  status?: 'pending' | 'approved' | 'rejected' | 'expired' | 'cancelled'
  kind?: 'agent_tool' | 'workflow_node'
  limit?: number
  offset?: number
}

/** Lists caller-scoped approvals from ``GET /api/approvals``. */
export async function fetchApprovals(
  options?: FetchApprovalsOptions,
): Promise<ApprovalAuditListResponse> {
  const params = new URLSearchParams()
  if (options?.status) {
    params.set('status', options.status)
  }
  if (options?.kind) {
    params.set('kind', options.kind)
  }
  if (options?.limit !== undefined) {
    params.set('limit', String(options.limit))
  }
  if (options?.offset !== undefined) {
    params.set('offset', String(options.offset))
  }

  const query = params.toString()
  const url = query ? `${API_BASE_URL}/api/approvals?${query}` : `${API_BASE_URL}/api/approvals`

  const response = await fetch(url, {
    method: 'GET',
    headers: buildAuthHeaders({ json: false }),
  })

  captureRequestId(response)

  if (!response.ok) {
    throw await toApprovalsApiError(response, `Failed to list approvals: ${response.status}`)
  }

  return (await response.json()) as ApprovalAuditListResponse
}

/** Fetches one approval audit entry from ``GET /api/approvals/{id}``. */
export async function fetchApproval(approvalId: string): Promise<ApprovalAuditEntry> {
  const response = await fetch(`${API_BASE_URL}/api/approvals/${approvalId}`, {
    method: 'GET',
    headers: buildAuthHeaders({ json: false }),
  })

  captureRequestId(response)

  if (!response.ok) {
    throw await toApprovalsApiError(response, `Failed to load approval: ${response.status}`)
  }

  return (await response.json()) as ApprovalAuditEntry
}

/** Lists revision history from ``GET /api/approvals/{id}/revisions``. */
export async function fetchApprovalRevisions(approvalId: string): Promise<ApprovalRevision[]> {
  const response = await fetch(`${API_BASE_URL}/api/approvals/${approvalId}/revisions`, {
    method: 'GET',
    headers: buildAuthHeaders({ json: false }),
  })

  captureRequestId(response)

  if (!response.ok) {
    throw await toApprovalsApiError(
      response,
      `Failed to load approval revisions: ${response.status}`,
    )
  }

  return (await response.json()) as ApprovalRevision[]
}

/** Appends a pre-decision edit for agent tool approvals via ``POST …/revise``. */
export async function reviseApproval(
  approvalId: string,
  body: ApprovalReviseRequest,
): Promise<ApprovalRevision> {
  const response = await fetch(`${API_BASE_URL}/api/approvals/${approvalId}/revise`, {
    method: 'POST',
    headers: buildAuthHeaders({ json: true }),
    body: JSON.stringify(body),
  })

  captureRequestId(response)

  if (!response.ok) {
    throw await toApprovalsApiError(response, `Failed to revise approval: ${response.status}`)
  }

  return (await response.json()) as ApprovalRevision
}

/** Records a reject decision (JSON response) via ``POST …/decide``. */
export async function rejectApproval(
  approvalId: string,
  reason?: string | null,
): Promise<ApprovalResult> {
  const body: ApprovalDecideRequest = { decision: 'rejected', reason: reason ?? null }
  const response = await fetch(`${API_BASE_URL}/api/approvals/${approvalId}/decide`, {
    method: 'POST',
    headers: buildAuthHeaders({ json: true }),
    body: JSON.stringify(body),
  })

  captureRequestId(response)

  if (!response.ok) {
    throw await toApprovalsApiError(response, `Failed to reject approval: ${response.status}`)
  }

  return (await response.json()) as ApprovalResult
}

export interface ApprovalDecideStreamHandlers {
  onStart?: (chunk: Extract<ChatChunk, { type: 'start' }>) => void
  onDelta?: (chunk: Extract<ChatChunk, { type: 'delta' }>) => void
  onEnd?: (chunk: Extract<ChatChunk, { type: 'end' }>) => void
  onToolStart?: (chunk: Extract<ChatChunk, { type: 'tool_start' }>) => void
  onToolEnd?: (chunk: Extract<ChatChunk, { type: 'tool_end' }>) => void
  onApprovalRequired?: (chunk: Extract<ChatChunk, { type: 'approval_required' }>) => void
  onError?: (error: Extract<ChatChunk, { type: 'error' }> | Error) => void
}

/**
 * Submits an approve decision and consumes the resumed agent SSE stream
 * from ``POST /api/approvals/{id}/decide``.
 */
export async function streamApproveApproval(
  approvalId: string,
  body: Omit<ApprovalDecideRequest, 'decision'>,
  handlers: ApprovalDecideStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/approvals/${approvalId}/decide`, {
    method: 'POST',
    headers: buildAuthHeaders({ json: true }),
    body: JSON.stringify({ ...body, decision: 'approved' } satisfies ApprovalDecideRequest),
    signal,
  })

  captureRequestId(response)

  if (!response.ok) {
    handlers.onError?.(await toApprovalsApiError(response, `Failed to approve: ${response.status}`))
    return
  }

  if (!response.body) {
    handlers.onError?.(new Error('Approve response did not include a stream body.'))
    return
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  const parser = new SseParser()

  try {
    readLoop: while (true) {
      const { done, value } = await reader.read()
      if (done) {
        break
      }

      const text = decoder.decode(value, { stream: true })
      for (const frame of parser.feed(text)) {
        const chunk = frame.data
        if (chunk.type === 'start') {
          handlers.onStart?.(chunk)
        } else if (chunk.type === 'delta') {
          handlers.onDelta?.(chunk)
        } else if (chunk.type === 'end') {
          handlers.onEnd?.(chunk)
        } else if (chunk.type === 'tool_start') {
          handlers.onToolStart?.(chunk)
        } else if (chunk.type === 'tool_end') {
          handlers.onToolEnd?.(chunk)
        } else if (chunk.type === 'approval_required') {
          handlers.onApprovalRequired?.(chunk)
        } else if (chunk.type === 'error') {
          handlers.onError?.(chunk)
          break readLoop
        }
      }
    }
  } catch (error) {
    if ((error as Error).name !== 'AbortError') {
      handlers.onError?.(error as Error)
    }
  } finally {
    try {
      await reader.cancel()
    } catch {
      // Stream may already be closed after natural end or cancel.
    }
  }
}
