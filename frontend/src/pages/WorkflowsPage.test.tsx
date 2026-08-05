/* @vitest-environment jsdom */

import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ProtectedRoute } from '../components/ProtectedRoute'
import { storeSession } from '../auth/tokenStorage'
import { renderWithProviders } from '../test/renderWithProviders'
import { jsonHealthResponse } from '../test/chatFetchStubs'
import type { AuthenticatedUser } from '../types/auth'
import { ChatPage } from './ChatPage'
import { WorkflowsPage } from './WorkflowsPage'

const user: AuthenticatedUser = {
  id: 'user-1',
  email: 'person@example.com',
  display_name: 'Person',
  picture_url: null,
}

function makeJwt(expSecondsFromNow: number): string {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))
  const exp = Math.floor(Date.now() / 1000) + expSecondsFromNow
  const payload = btoa(JSON.stringify({ exp }))
  return `${header}.${payload}.signature`
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
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
  status: 'waiting_approval',
  context: { trigger_input: {}, variables: {}, metadata: {} },
  current_node_ids: ['approve'],
  error: null,
  created_at: '2026-08-01T10:00:00.000Z',
  updated_at: '2026-08-01T10:01:00.000Z',
  started_at: '2026-08-01T10:00:00.000Z',
  completed_at: null,
}

const sampleRunDetail = {
  ...sampleRun,
  node_executions: [
    {
      id: 'exec-1',
      run_id: 'run-1',
      node_id: 'approve',
      node_type: 'approval',
      status: 'waiting_approval',
      input: { message: 'Please review this step.' },
      output: null,
      error: null,
      decided_by: null,
      decided_at: null,
      decision: null,
      started_at: '2026-08-01T10:01:00.000Z',
      completed_at: null,
    },
  ],
}

function createWorkflowFetchMock(options?: {
  workflowEnabled?: boolean
  definitions?: (typeof sampleDefinition)[]
  runs?: (typeof sampleRun)[]
  runDetail?: typeof sampleRunDetail
}) {
  const workflowEnabled = options?.workflowEnabled ?? true
  const definitions = options?.definitions ?? [sampleDefinition]
  const runs = options?.runs ?? [sampleRun]
  const runDetail = options?.runDetail ?? sampleRunDetail

  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    const method = init?.method ?? 'GET'

    if (url.endsWith('/api/health') && method === 'GET') {
      return jsonHealthResponse(true, false, false, false, false, workflowEnabled)
    }

    if (url.endsWith('/api/chat/sessions') && method === 'GET') {
      return jsonResponse([])
    }

    if (url.endsWith('/api/workflows') && method === 'GET') {
      return jsonResponse({ definitions, limit: 50, offset: 0, total: definitions.length })
    }

    if (url.endsWith('/api/workflow-runs') && method === 'GET') {
      return jsonResponse({ runs, limit: 50, offset: 0, total: runs.length })
    }

    if (url.includes('/api/workflow-runs/run-1') && method === 'GET') {
      return jsonResponse(runDetail)
    }

    if (url.includes('/api/workflow-runs/run-1/nodes/exec-1/approve') && method === 'POST') {
      return jsonResponse({ ...sampleRun, status: 'running' })
    }

    if (url.includes('/api/workflow-runs/run-1/nodes/exec-1/reject') && method === 'POST') {
      return jsonResponse({ ...sampleRun, status: 'failed' })
    }

    if (url.endsWith('/api/workflows') && method === 'POST') {
      return jsonResponse(sampleDefinition)
    }

    if (url.includes('/api/workflows/def-1/runs') && method === 'POST') {
      return jsonResponse(sampleRun)
    }

    return jsonResponse([])
  })
}

function renderWorkflowRoute(initialRoute = '/workflows') {
  return renderWithProviders(
    <Routes>
      <Route path="/" element={<ChatPage />} />
      <Route
        path="/workflows"
        element={
          <ProtectedRoute>
            <WorkflowsPage />
          </ProtectedRoute>
        }
      />
    </Routes>,
    { initialRoute, withChatProvider: true },
  )
}

describe('WorkflowsPage guest redirect', () => {
  beforeEach(() => {
    Object.defineProperty(globalThis.HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    })
  })

  afterEach(() => {
    cleanup()
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('redirects guests to chat instead of showing workflow controls', () => {
    renderWorkflowRoute('/workflows')

    expect(screen.getByPlaceholderText('Ask something…')).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Workflows' })).toBeNull()
  })
})

describe('WorkflowsPage authenticated', () => {
  beforeEach(() => {
    Object.defineProperty(globalThis.HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    })
  })

  afterEach(() => {
    cleanup()
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('renders definitions and runs when workflow engine is enabled', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal('fetch', createWorkflowFetchMock({ workflowEnabled: true }))

    renderWorkflowRoute('/workflows')

    expect(await screen.findByRole('heading', { name: 'Workflows' })).toBeTruthy()
    expect(await screen.findByLabelText('Workflow definitions')).toBeTruthy()
    expect(await screen.findByLabelText('Workflow runs')).toBeTruthy()
    expect(await screen.findByRole('button', { name: /waiting approval/i })).toBeTruthy()
  })

  it('shows unavailable notice when workflow_engine_enabled is false', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal('fetch', createWorkflowFetchMock({ workflowEnabled: false }))

    renderWorkflowRoute('/workflows')

    expect(await screen.findByText('Workflows are not available')).toBeTruthy()
    expect(screen.queryByLabelText('Workflow definitions')).toBeNull()
  })

  it('shows validation error for invalid definition JSON', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal('fetch', createWorkflowFetchMock({ workflowEnabled: true }))

    renderWorkflowRoute('/workflows')
    await screen.findByLabelText('Graph JSON')

    const textarea = screen.getByLabelText('Graph JSON')
    await userEvent.clear(textarea)
    await userEvent.click(textarea)
    await userEvent.paste('{ invalid json')
    await userEvent.click(screen.getByRole('button', { name: 'Create definition' }))

    expect(await screen.findByText('Invalid JSON. Check syntax and try again.')).toBeTruthy()
  })

  it('displays pending approval context and approves successfully', async () => {
    storeSession(makeJwt(3600), user)
    const fetchMock = createWorkflowFetchMock({ workflowEnabled: true })
    vi.stubGlobal('fetch', fetchMock)

    renderWorkflowRoute('/workflows')
    await screen.findByLabelText('Workflow runs')

    await userEvent.click(screen.getByRole('button', { name: /waiting approval/i }))

    const approvalRegion = await screen.findByRole('region', { name: /Pending approval/i })
    expect(within(approvalRegion).getByText(/Please review this step/)).toBeTruthy()

    await userEvent.click(screen.getByRole('button', { name: 'Approve' }))

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url, init]) => {
          const resolvedUrl = typeof url === 'string' ? url : url.toString()
          return (
            resolvedUrl.includes('/api/workflow-runs/run-1/nodes/exec-1/approve') &&
            init?.method === 'POST'
          )
        }),
      ).toBe(true)
    })

    expect(await screen.findByText('Approval submitted.')).toBeTruthy()
  })

  it('requires confirmation before rejecting approval', async () => {
    storeSession(makeJwt(3600), user)
    const fetchMock = createWorkflowFetchMock({ workflowEnabled: true })
    vi.stubGlobal('fetch', fetchMock)

    renderWorkflowRoute('/workflows')
    await screen.findByLabelText('Workflow runs')

    await userEvent.click(screen.getByRole('button', { name: /waiting approval/i }))
    await screen.findByText('Pending approval — approve')

    await userEvent.click(screen.getByRole('button', { name: 'Reject' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('Reject approval?')).toBeTruthy()

    await userEvent.click(within(dialog).getByRole('button', { name: 'Reject' }))

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url, init]) => {
          const resolvedUrl = typeof url === 'string' ? url : url.toString()
          return (
            resolvedUrl.includes('/api/workflow-runs/run-1/nodes/exec-1/reject') &&
            init?.method === 'POST'
          )
        }),
      ).toBe(true)
    })
  })
})

describe('AppNav workflows link visibility', () => {
  beforeEach(() => {
    Object.defineProperty(globalThis.HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    })
  })

  afterEach(() => {
    cleanup()
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('hides Workflows nav link when workflow_engine_enabled is false', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal('fetch', createWorkflowFetchMock({ workflowEnabled: false }))

    renderWithProviders(<ChatPage />, { withChatProvider: true })

    await waitFor(() => {
      expect(screen.queryByRole('link', { name: 'Workflows' })).toBeNull()
    })
  })

  it('shows Workflows nav link when workflow_engine_enabled is true', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal('fetch', createWorkflowFetchMock({ workflowEnabled: true }))

    renderWithProviders(<ChatPage />, { withChatProvider: true })

    const workflowsLink = await screen.findByRole('link', { name: 'Workflows' })
    expect(workflowsLink.getAttribute('href')).toBe('/workflows')
  })
})

describe('WorkflowsPage accessibility', () => {
  beforeEach(() => {
    Object.defineProperty(globalThis.HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    })
  })

  afterEach(() => {
    cleanup()
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('exposes labelled sections and dialog semantics for approval actions', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal('fetch', createWorkflowFetchMock({ workflowEnabled: true }))

    renderWorkflowRoute('/workflows')
    await screen.findByRole('heading', { name: 'Definitions' })
    expect(screen.getByRole('heading', { name: 'Runs' })).toBeTruthy()

    await userEvent.click(screen.getByRole('button', { name: /waiting approval/i }))
    await screen.findByRole('region', { name: /Pending approval/i })

    await userEvent.click(screen.getByRole('button', { name: 'Reject' }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog.getAttribute('aria-modal')).toBe('true')
  })
})
