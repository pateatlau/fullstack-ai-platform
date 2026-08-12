export type ApprovalKind = 'agent_tool' | 'workflow_node'

export type ApprovalAuditStatus = 'pending' | 'approved' | 'rejected' | 'expired' | 'cancelled'

export interface ProposedToolCall {
  name: string
  arguments: Record<string, unknown>
  call_id: string
}

export interface ApprovalAuditEntry {
  id: string
  kind: ApprovalKind
  approval_correlation_id: string
  status: ApprovalAuditStatus
  tool_calls: ProposedToolCall[] | null
  workflow_run_id: string | null
  workflow_node_id: string | null
  session_id: string | null
  requested_at: string
  decided_at: string | null
  decided_by: string | null
  decision: string | null
  reason: string | null
  edited: boolean
  revision_count: number
  decide_url: string
}

export interface ApprovalAuditListResponse {
  approvals: ApprovalAuditEntry[]
  limit: number
  offset: number
  total: number
}

export interface ApprovalRevision {
  id: string
  approval_id: string
  approval_kind: ApprovalKind
  revision_number: number
  edited_by: string
  edited_at: string
  edited_payload: Record<string, unknown> | ProposedToolCall[]
  note: string | null
}

export interface ApprovalResult {
  approval_id: string
  approval_kind: ApprovalKind
  status: string
  edited: boolean
  final_payload: Record<string, unknown> | ProposedToolCall[] | null
  reason: string | null
  approver: string | null
  decided_at: string
  approval_correlation_id: string
}

export interface ApprovalDecideRequest {
  decision: 'approved' | 'rejected'
  edited_calls?: ProposedToolCall[]
  reason?: string | null
}

export interface ApprovalReviseRequest {
  edited_calls: ProposedToolCall[]
  note?: string | null
}

export function formatApprovalKind(kind: ApprovalKind): string {
  return kind === 'agent_tool' ? 'Agent tool call' : 'Workflow node'
}

export function formatApprovalStatus(status: ApprovalAuditStatus): string {
  return status.replace(/_/g, ' ')
}
