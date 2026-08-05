/** Workflow domain types matching the public REST API (Epic 06 Phase 11). */

export type NodeType =
  'task' | 'llm' | 'agent' | 'router' | 'fork' | 'join' | 'approval' | 'terminal'

export type DefinitionStatus = 'draft' | 'active' | 'archived'

export type RunStatus =
  'pending' | 'running' | 'waiting_approval' | 'completed' | 'failed' | 'cancelled'

export type NodeStatus =
  'pending' | 'running' | 'waiting_approval' | 'succeeded' | 'failed' | 'skipped' | 'cancelled'

export type ApprovalDecision = 'approved' | 'rejected'

export interface WorkflowNode {
  id: string
  type: NodeType
  config: Record<string, unknown>
  retry_policy?: { max_retries: number; base_delay_seconds: number } | null
  timeout_seconds?: number | null
}

export interface WorkflowEdge {
  id: string
  from_node_id: string
  to_node_id: string
  condition?: Record<string, unknown> | null
}

export interface WorkflowDefinition {
  id: string
  name: string
  description: string | null
  version: number
  status: DefinitionStatus
  entry_node_id: string
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  metadata: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface WorkflowDefinitionListResponse {
  definitions: WorkflowDefinition[]
  limit: number
  offset: number
  total: number
}

export interface WorkflowDefinitionCreateRequest {
  name: string
  description?: string | null
  status?: DefinitionStatus
  entry_node_id: string
  nodes: WorkflowNode[]
  edges?: WorkflowEdge[]
  metadata?: Record<string, unknown>
}

export interface WorkflowContext {
  trigger_input: Record<string, unknown>
  variables: Record<string, unknown>
  metadata: Record<string, unknown>
}

export interface WorkflowRun {
  id: string
  workflow_definition_id: string
  idempotency_key: string
  session_id: string | null
  status: RunStatus
  context: WorkflowContext
  current_node_ids: string[]
  error: string | null
  created_at: string
  updated_at: string
  started_at: string | null
  completed_at: string | null
}

export interface WorkflowNodeExecution {
  id: string
  run_id: string
  node_id: string
  node_type: NodeType
  status: NodeStatus
  input: Record<string, unknown>
  output: Record<string, unknown> | null
  error: string | null
  decided_by: string | null
  decided_at: string | null
  decision: ApprovalDecision | null
  started_at: string | null
  completed_at: string | null
}

export interface WorkflowRunDetail extends WorkflowRun {
  node_executions: WorkflowNodeExecution[]
}

export interface WorkflowRunListResponse {
  runs: WorkflowRun[]
  limit: number
  offset: number
  total: number
}

export interface StartWorkflowRunRequest {
  idempotency_key: string
  trigger_input?: Record<string, unknown>
}

/** Pretty-print JSON for display; falls back to string on invalid input. */
export function formatWorkflowJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}
