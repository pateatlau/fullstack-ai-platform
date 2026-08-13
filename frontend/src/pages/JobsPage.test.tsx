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
import { JobsPage } from './JobsPage'

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

const sampleJobs = {
  jobs: [
    {
      id: '11111111-1111-1111-1111-111111111111',
      job_type: 'hitl_approval_expiry_sweep',
      status: 'dead_letter',
      payload: { version: 1 },
      result: null,
      attempt_count: 3,
      max_attempts: 3,
      run_at: '2026-08-13T08:00:00.000Z',
      last_error: 'A'.repeat(150),
      schedule_id: null,
      created_at: '2026-08-13T07:00:00.000Z',
      updated_at: '2026-08-13T08:00:00.000Z',
      started_at: '2026-08-13T07:30:00.000Z',
      finished_at: '2026-08-13T08:00:00.000Z',
    },
    {
      id: '33333333-3333-3333-3333-333333333333',
      job_type: 'rag_document_indexing',
      status: 'succeeded',
      payload: { version: 1, document_id: 'doc-1', user_id: 'user-1' },
      result: { summary: 'indexed 1 document' },
      attempt_count: 1,
      max_attempts: 3,
      run_at: '2026-08-13T06:00:00.000Z',
      last_error: null,
      schedule_id: null,
      created_at: '2026-08-13T05:00:00.000Z',
      updated_at: '2026-08-13T06:00:00.000Z',
      started_at: '2026-08-13T05:30:00.000Z',
      finished_at: '2026-08-13T06:00:00.000Z',
    },
  ],
}

const sampleSchedules = {
  schedules: [
    {
      id: '22222222-2222-2222-2222-222222222222',
      name: 'hitl_approval_expiry',
      job_type: 'hitl_approval_expiry_sweep',
      payload: { version: 1 },
      interval_seconds: 300,
      next_run_at: '2026-08-13T09:00:00.000Z',
      status: 'enabled',
      created_at: '2026-08-13T00:00:00.000Z',
      updated_at: '2026-08-13T00:00:00.000Z',
    },
  ],
}

function createJobsFetchMock(options?: {
  backgroundJobsEnabled?: boolean
  jobs?: typeof sampleJobs
  schedules?: typeof sampleSchedules
  jobsError?: { status: number; code: string; message: string }
  retryError?: { status: number; code: string; message: string }
}) {
  const backgroundJobsEnabled = options?.backgroundJobsEnabled ?? true
  const jobs = options?.jobs ?? sampleJobs
  const schedules = options?.schedules ?? sampleSchedules

  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    const method = init?.method ?? 'GET'

    if (url.endsWith('/api/health') && method === 'GET') {
      return jsonHealthResponse(
        true,
        false,
        false,
        false,
        false,
        false,
        false,
        false,
        false,
        0,
        backgroundJobsEnabled,
      )
    }

    if (url.includes('/api/jobs/schedules') && method === 'GET') {
      return jsonResponse(schedules)
    }

    if (url.match(/\/api\/jobs\/[^/]+\/retry$/) && method === 'POST') {
      if (options?.retryError) {
        const { status, code, message } = options.retryError
        return jsonResponse({ error: { code, message } }, status)
      }
      const jobId = url.split('/')[3]
      const retried = jobs.jobs.find((job) => job.id === jobId)
      return jsonResponse({
        job: retried ? { ...retried, status: 'queued', attempt_count: 0 } : jobs.jobs[0],
      })
    }

    if (url.includes('/api/jobs/') && method === 'GET') {
      const jobId = url.split('/').pop()
      const job = jobs.jobs.find((entry) => entry.id === jobId)
      return job
        ? jsonResponse(job)
        : jsonResponse({ error: { code: 'job_not_found', message: 'missing' } }, 404)
    }

    if (url.endsWith('/api/jobs') && method === 'GET') {
      if (options?.jobsError) {
        const { status, code, message } = options.jobsError
        return jsonResponse({ error: { code, message } }, status)
      }
      return jsonResponse(jobs)
    }

    if (url.endsWith('/api/chat/sessions') && method === 'GET') {
      return jsonResponse([])
    }

    return jsonResponse([])
  })
}

function renderJobsRoute(initialRoute = '/jobs') {
  return renderWithProviders(
    <Routes>
      <Route path="/" element={<ChatPage />} />
      <Route
        path="/jobs"
        element={
          <ProtectedRoute>
            <JobsPage />
          </ProtectedRoute>
        }
      />
    </Routes>,
    { initialRoute },
  )
}

describe('JobsPage', () => {
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

  it('shows unavailable notice when background_jobs_enabled is false', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal('fetch', createJobsFetchMock({ backgroundJobsEnabled: false }))

    renderJobsRoute()

    expect(
      await screen.findByRole('heading', { name: 'Background jobs are not available' }),
    ).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Background jobs' })).toBeNull()
  })

  it('shows unavailable notice when jobs API returns feature_disabled', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal(
      'fetch',
      createJobsFetchMock({
        backgroundJobsEnabled: true,
        jobsError: {
          status: 503,
          code: 'feature_disabled',
          message: 'Background Jobs are not enabled on this server.',
        },
      }),
    )

    renderJobsRoute()

    expect(
      await screen.findByRole('heading', { name: 'Background jobs are not available' }),
    ).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'No jobs found' })).toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('renders jobs table with retry on dead-letter rows only', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal('fetch', createJobsFetchMock({ backgroundJobsEnabled: true }))

    renderJobsRoute()

    const listHeading = await screen.findByRole('heading', { name: 'Background jobs' })
    const listRegion = listHeading.closest('section')
    expect(listRegion).toBeTruthy()

    await waitFor(() => {
      const table = within(listRegion as HTMLElement).getByRole('table')
      expect(within(table).getByText('hitl approval expiry sweep')).toBeTruthy()
      expect(within(table).getByText('rag document indexing')).toBeTruthy()
      expect(within(table).getAllByRole('button', { name: 'Retry' })).toHaveLength(1)
      expect(within(table).getByText(/^A{10,}…$/)).toBeTruthy()
    })
  })

  it('retries a dead-letter job', async () => {
    storeSession(makeJwt(3600), user)
    const fetchMock = createJobsFetchMock({ backgroundJobsEnabled: true })
    vi.stubGlobal('fetch', fetchMock)

    renderJobsRoute()
    const retryButton = await screen.findByRole('button', { name: 'Retry' })
    await userEvent.click(retryButton)

    await waitFor(() => {
      expect(screen.getByText(/re-queued/i)).toBeTruthy()
    })
    expect(
      fetchMock.mock.calls.some(([url, init]) => {
        const resolved = typeof url === 'string' ? url : url.toString()
        return resolved.includes('/retry') && (init?.method ?? 'GET') === 'POST'
      }),
    ).toBe(true)
  })

  it('shows unavailable notice when retry returns feature_disabled', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal(
      'fetch',
      createJobsFetchMock({
        backgroundJobsEnabled: true,
        retryError: {
          status: 503,
          code: 'feature_disabled',
          message: 'Background Jobs are not enabled on this server.',
        },
      }),
    )

    renderJobsRoute()
    const retryButton = await screen.findByRole('button', { name: 'Retry' })
    await userEvent.click(retryButton)

    expect(
      await screen.findByRole('heading', { name: 'Background jobs are not available' }),
    ).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Background jobs' })).toBeNull()
  })

  it('renders schedules tab', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal('fetch', createJobsFetchMock({ backgroundJobsEnabled: true }))

    renderJobsRoute()

    await screen.findByRole('heading', { name: 'Background jobs' })
    await userEvent.click(screen.getByRole('button', { name: 'Schedules' }))

    const schedulesHeading = await screen.findByRole('heading', { name: 'Recurring schedules' })
    const schedulesRegion = schedulesHeading.closest('section')
    expect(schedulesRegion).toBeTruthy()

    await waitFor(() => {
      const table = within(schedulesRegion as HTMLElement).getByRole('table')
      expect(within(table).getByText('hitl_approval_expiry')).toBeTruthy()
      expect(within(table).getByText('5m')).toBeTruthy()
    })
  })

  it('shows empty state when no jobs are returned', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal(
      'fetch',
      createJobsFetchMock({
        backgroundJobsEnabled: true,
        jobs: { jobs: [] },
      }),
    )

    renderJobsRoute()

    expect(await screen.findByRole('heading', { name: 'No jobs found' })).toBeTruthy()
  })

  it('shows API error banner on failure', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal(
      'fetch',
      createJobsFetchMock({
        backgroundJobsEnabled: true,
        jobsError: {
          status: 500,
          code: 'internal_error',
          message: 'Unable to load jobs.',
        },
      }),
    )

    renderJobsRoute()

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('Unable to load jobs.')
  })
})

describe('AppNav jobs link visibility', () => {
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

  it('hides Jobs nav link when background_jobs_enabled is false', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal('fetch', createJobsFetchMock({ backgroundJobsEnabled: false }))

    renderWithProviders(<ChatPage />, { withChatProvider: true })

    await waitFor(() => {
      expect(screen.queryByRole('link', { name: 'Jobs' })).toBeNull()
    })
  })

  it('shows Jobs nav link when background_jobs_enabled is true', async () => {
    storeSession(makeJwt(3600), user)
    vi.stubGlobal('fetch', createJobsFetchMock({ backgroundJobsEnabled: true }))

    renderWithProviders(<ChatPage />, { withChatProvider: true })

    const link = await screen.findByRole('link', { name: 'Jobs' })
    expect(link.getAttribute('href')).toBe('/jobs')
  })
})
