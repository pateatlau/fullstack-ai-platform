/* @vitest-environment jsdom */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  approveWorkflowNode,
  createWorkflowDefinition,
  listWorkflowDefinitions,
  listWorkflowRuns,
  rejectWorkflowNode,
  startWorkflowRun,
  WorkflowApiError,
} from './workflowClient'
import { storeSession } from '../auth/tokenStorage'
import type { AuthenticatedUser } from '../types/auth'

const user: AuthenticatedUser = {
  id: 'user-1',
  email: 'person@example.com',
  display_name: 'Person',
  picture_url: null,
}

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

const sampleDefinition = {
  id: 'def-1',
  name: 'Sample Workflow',
  description: null,
  version: 1,
  status: 'active',
  entry_node_id: 'start',
  nodes: [
    { id: 'start', type: 'task', config: {} },
    { id: 'end', type: 'terminal', config: {} },
  ],
  edges: [{ id: 'e1', from_node_id: 'start', to_node_id: 'end' }],
  metadata: {},
  created_at: '2026-08-01T10:00:00.000Z',
  updated_at: '2026-08-01T10:00:00.000Z',
}

const sampleRun = {
  id: 'run-1',
  workflow_definition_id: 'def-1',
  idempotency_key: 'key-1',
  session_id: null,
  status: 'running',
  context: { trigger_input: {}, variables: {}, metadata: {} },
  current_node_ids: ['start'],
  error: null,
  created_at: '2026-08-01T10:00:00.000Z',
  updated_at: '2026-08-01T10:01:00.000Z',
  started_at: '2026-08-01T10:00:00.000Z',
  completed_at: null,
}

describe('workflowClient Authorization header', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('listWorkflowDefinitions sends Bearer token', async () => {
    storeSession('workflow-jwt', user)
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ definitions: [], limit: 50, offset: 0, total: 0 }))
    vi.stubGlobal('fetch', fetchMock)

    await listWorkflowDefinitions()

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toContain('/api/workflows')
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer workflow-jwt')
  })

  it('createWorkflowDefinition sends JSON body with Bearer token', async () => {
    storeSession('workflow-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(sampleDefinition))
    vi.stubGlobal('fetch', fetchMock)

    await createWorkflowDefinition({
      name: 'Sample Workflow',
      entry_node_id: 'start',
      nodes: [{ id: 'start', type: 'task', config: {} }],
    })

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toContain('/api/workflows')
    expect(init.method).toBe('POST')
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer workflow-jwt')
  })

  it('startWorkflowRun posts to definition runs endpoint', async () => {
    storeSession('workflow-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(sampleRun))
    vi.stubGlobal('fetch', fetchMock)

    await startWorkflowRun('def-1', { idempotency_key: 'key-1' })

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toContain('/api/workflows/def-1/runs')
    expect(init.method).toBe('POST')
    expect(init.body).toBe(JSON.stringify({ idempotency_key: 'key-1' }))
  })

  it('listWorkflowRuns hits /api/workflow-runs', async () => {
    storeSession('workflow-jwt', user)
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ runs: [], limit: 50, offset: 0, total: 0 }))
    vi.stubGlobal('fetch', fetchMock)

    await listWorkflowRuns()

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toContain('/api/workflow-runs')
  })

  it('approveWorkflowNode posts to approve endpoint', async () => {
    storeSession('workflow-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ...sampleRun, status: 'running' }))
    vi.stubGlobal('fetch', fetchMock)

    await approveWorkflowNode('run-1', 'node-exec-1')

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toContain('/api/workflow-runs/run-1/nodes/node-exec-1/approve')
    expect(init.method).toBe('POST')
  })

  it('rejectWorkflowNode posts to reject endpoint', async () => {
    storeSession('workflow-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ...sampleRun, status: 'failed' }))
    vi.stubGlobal('fetch', fetchMock)

    await rejectWorkflowNode('run-1', 'node-exec-1')

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toContain('/api/workflow-runs/run-1/nodes/node-exec-1/reject')
    expect(init.method).toBe('POST')
  })
})

describe('workflowClient error handling', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('maps feature_disabled 503 to WorkflowApiError', async () => {
    storeSession('workflow-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(
        {
          error: {
            code: 'feature_disabled',
            message: 'Workflow Engine is not enabled on this server.',
          },
        },
        503,
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(listWorkflowDefinitions()).rejects.toMatchObject({
      name: 'WorkflowApiError',
      status: 503,
      code: 'feature_disabled',
    } satisfies Partial<WorkflowApiError>)
  })

  it('maps workflow_validation_error 422 to WorkflowApiError', async () => {
    storeSession('workflow-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(
        {
          error: {
            code: 'workflow_validation_error',
            message: 'Graph validation failed.',
          },
        },
        422,
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      createWorkflowDefinition({
        name: 'Bad',
        entry_node_id: 'missing',
        nodes: [],
      }),
    ).rejects.toMatchObject({
      name: 'WorkflowApiError',
      status: 422,
      code: 'workflow_validation_error',
    } satisfies Partial<WorkflowApiError>)
  })
})
