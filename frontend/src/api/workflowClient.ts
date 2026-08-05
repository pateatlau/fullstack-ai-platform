import type {
  StartWorkflowRunRequest,
  WorkflowDefinition,
  WorkflowDefinitionCreateRequest,
  WorkflowDefinitionListResponse,
  WorkflowRun,
  WorkflowRunDetail,
  WorkflowRunListResponse,
} from '../types/workflow'
import { API_BASE_URL, buildAuthHeaders, captureRequestId, parseErrorEnvelope } from './request'

export class WorkflowApiError extends Error {
  status: number
  code?: string

  constructor(message: string, status: number, code?: string) {
    super(message)
    this.name = 'WorkflowApiError'
    this.status = status
    this.code = code
  }
}

async function toWorkflowApiError(
  response: Response,
  fallbackMessage: string,
): Promise<WorkflowApiError> {
  const parsed = await parseErrorEnvelope(response, fallbackMessage)
  return new WorkflowApiError(parsed.message, parsed.status, parsed.code)
}

export async function listWorkflowDefinitions(options?: {
  limit?: number
  offset?: number
}): Promise<WorkflowDefinitionListResponse> {
  const params = new URLSearchParams()
  if (options?.limit !== undefined) {
    params.set('limit', String(options.limit))
  }
  if (options?.offset !== undefined) {
    params.set('offset', String(options.offset))
  }

  const query = params.toString()
  const url = query ? `${API_BASE_URL}/api/workflows?${query}` : `${API_BASE_URL}/api/workflows`

  const response = await fetch(url, {
    method: 'GET',
    headers: buildAuthHeaders(),
  })

  captureRequestId(response)

  if (!response.ok) {
    throw await toWorkflowApiError(response, `Failed to list workflows: ${response.status}`)
  }

  return (await response.json()) as WorkflowDefinitionListResponse
}

export async function createWorkflowDefinition(
  body: WorkflowDefinitionCreateRequest,
): Promise<WorkflowDefinition> {
  const response = await fetch(`${API_BASE_URL}/api/workflows`, {
    method: 'POST',
    headers: buildAuthHeaders({ json: true }),
    body: JSON.stringify(body),
  })

  captureRequestId(response)

  if (!response.ok) {
    throw await toWorkflowApiError(response, `Failed to create workflow: ${response.status}`)
  }

  return (await response.json()) as WorkflowDefinition
}

export async function listWorkflowRuns(options?: {
  limit?: number
  offset?: number
}): Promise<WorkflowRunListResponse> {
  const params = new URLSearchParams()
  if (options?.limit !== undefined) {
    params.set('limit', String(options.limit))
  }
  if (options?.offset !== undefined) {
    params.set('offset', String(options.offset))
  }

  const query = params.toString()
  const url = query
    ? `${API_BASE_URL}/api/workflow-runs?${query}`
    : `${API_BASE_URL}/api/workflow-runs`

  const response = await fetch(url, {
    method: 'GET',
    headers: buildAuthHeaders(),
  })

  captureRequestId(response)

  if (!response.ok) {
    throw await toWorkflowApiError(response, `Failed to list workflow runs: ${response.status}`)
  }

  return (await response.json()) as WorkflowRunListResponse
}

export async function listWorkflowRunsForDefinition(
  definitionId: string,
  options?: { limit?: number; offset?: number },
): Promise<WorkflowRunListResponse> {
  const params = new URLSearchParams()
  if (options?.limit !== undefined) {
    params.set('limit', String(options.limit))
  }
  if (options?.offset !== undefined) {
    params.set('offset', String(options.offset))
  }

  const query = params.toString()
  const url = query
    ? `${API_BASE_URL}/api/workflows/${definitionId}/runs?${query}`
    : `${API_BASE_URL}/api/workflows/${definitionId}/runs`

  const response = await fetch(url, {
    method: 'GET',
    headers: buildAuthHeaders(),
  })

  captureRequestId(response)

  if (!response.ok) {
    throw await toWorkflowApiError(response, `Failed to list runs for workflow: ${response.status}`)
  }

  return (await response.json()) as WorkflowRunListResponse
}

export async function getWorkflowRun(runId: string): Promise<WorkflowRunDetail> {
  const response = await fetch(`${API_BASE_URL}/api/workflow-runs/${runId}`, {
    method: 'GET',
    headers: buildAuthHeaders(),
  })

  captureRequestId(response)

  if (!response.ok) {
    throw await toWorkflowApiError(response, `Failed to load workflow run: ${response.status}`)
  }

  return (await response.json()) as WorkflowRunDetail
}

export async function startWorkflowRun(
  definitionId: string,
  body: StartWorkflowRunRequest,
): Promise<WorkflowRun> {
  const response = await fetch(`${API_BASE_URL}/api/workflows/${definitionId}/runs`, {
    method: 'POST',
    headers: buildAuthHeaders({ json: true }),
    body: JSON.stringify(body),
  })

  captureRequestId(response)

  if (!response.ok) {
    throw await toWorkflowApiError(response, `Failed to start workflow run: ${response.status}`)
  }

  return (await response.json()) as WorkflowRun
}

export async function cancelWorkflowRun(runId: string): Promise<WorkflowRun> {
  const response = await fetch(`${API_BASE_URL}/api/workflow-runs/${runId}/cancel`, {
    method: 'POST',
    headers: buildAuthHeaders(),
  })

  captureRequestId(response)

  if (!response.ok) {
    throw await toWorkflowApiError(response, `Failed to cancel workflow run: ${response.status}`)
  }

  return (await response.json()) as WorkflowRun
}

export async function resumeWorkflowRun(runId: string): Promise<WorkflowRun> {
  const response = await fetch(`${API_BASE_URL}/api/workflow-runs/${runId}/resume`, {
    method: 'POST',
    headers: buildAuthHeaders(),
  })

  captureRequestId(response)

  if (!response.ok) {
    throw await toWorkflowApiError(response, `Failed to resume workflow run: ${response.status}`)
  }

  return (await response.json()) as WorkflowRun
}

export async function approveWorkflowNode(
  runId: string,
  nodeExecutionId: string,
): Promise<WorkflowRun> {
  const response = await fetch(
    `${API_BASE_URL}/api/workflow-runs/${runId}/nodes/${nodeExecutionId}/approve`,
    {
      method: 'POST',
      headers: buildAuthHeaders(),
    },
  )

  captureRequestId(response)

  if (!response.ok) {
    throw await toWorkflowApiError(response, `Failed to approve workflow node: ${response.status}`)
  }

  return (await response.json()) as WorkflowRun
}

export async function rejectWorkflowNode(
  runId: string,
  nodeExecutionId: string,
): Promise<WorkflowRun> {
  const response = await fetch(
    `${API_BASE_URL}/api/workflow-runs/${runId}/nodes/${nodeExecutionId}/reject`,
    {
      method: 'POST',
      headers: buildAuthHeaders(),
    },
  )

  captureRequestId(response)

  if (!response.ok) {
    throw await toWorkflowApiError(response, `Failed to reject workflow node: ${response.status}`)
  }

  return (await response.json()) as WorkflowRun
}
