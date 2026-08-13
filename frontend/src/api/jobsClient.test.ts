/* @vitest-environment jsdom */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchJobDetail, fetchJobs, fetchJobSchedules, JobsApiError, retryJob } from './jobsClient'
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

const sampleJob = {
  id: '11111111-1111-1111-1111-111111111111',
  job_type: 'hitl_approval_expiry_sweep',
  status: 'dead_letter',
  payload: { version: 1 },
  result: null,
  attempt_count: 3,
  max_attempts: 3,
  run_at: '2026-08-13T08:00:00.000Z',
  last_error: 'handler timed out',
  schedule_id: null,
  created_at: '2026-08-13T07:00:00.000Z',
  updated_at: '2026-08-13T08:00:00.000Z',
  started_at: '2026-08-13T07:30:00.000Z',
  finished_at: '2026-08-13T08:00:00.000Z',
}

const sampleSchedule = {
  id: '22222222-2222-2222-2222-222222222222',
  name: 'hitl_approval_expiry',
  job_type: 'hitl_approval_expiry_sweep',
  payload: { version: 1 },
  interval_seconds: 300,
  next_run_at: '2026-08-13T09:00:00.000Z',
  status: 'enabled',
  created_at: '2026-08-13T00:00:00.000Z',
  updated_at: '2026-08-13T00:00:00.000Z',
}

describe('jobsClient Authorization header', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('fetchJobs sends Bearer token and query params', async () => {
    storeSession('jobs-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ jobs: [sampleJob] }))
    vi.stubGlobal('fetch', fetchMock)

    const result = await fetchJobs({
      status: 'dead_letter',
      job_type: 'hitl_approval_expiry_sweep',
    })

    expect(result.jobs).toHaveLength(1)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/jobs?status=dead_letter&job_type=hitl_approval_expiry_sweep',
      expect.objectContaining({
        method: 'GET',
        headers: expect.objectContaining({ Authorization: 'Bearer jobs-jwt' }),
      }),
    )
  })

  it('fetchJobDetail returns one job', async () => {
    storeSession('jobs-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(sampleJob))
    vi.stubGlobal('fetch', fetchMock)

    const result = await fetchJobDetail(sampleJob.id)

    expect(result.id).toBe(sampleJob.id)
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/jobs/${sampleJob.id}`,
      expect.objectContaining({ method: 'GET' }),
    )
  })

  it('fetchJobSchedules returns schedules', async () => {
    storeSession('jobs-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ schedules: [sampleSchedule] }))
    vi.stubGlobal('fetch', fetchMock)

    const result = await fetchJobSchedules()

    expect(result.schedules).toHaveLength(1)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/jobs/schedules',
      expect.objectContaining({ method: 'GET' }),
    )
  })

  it('retryJob posts to retry endpoint', async () => {
    storeSession('jobs-jwt', user)
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ job: { ...sampleJob, status: 'queued', attempt_count: 0 } }),
      )
    vi.stubGlobal('fetch', fetchMock)

    const result = await retryJob(sampleJob.id)

    expect(result.job.status).toBe('queued')
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/jobs/${sampleJob.id}/retry`,
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('fetchJobs throws JobsApiError on feature_disabled', async () => {
    storeSession('jobs-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(
        {
          error: {
            code: 'feature_disabled',
            message: 'Background Jobs are not enabled on this server.',
          },
        },
        503,
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchJobs()).rejects.toMatchObject({
      name: 'JobsApiError',
      code: 'feature_disabled',
      status: 503,
    } satisfies Partial<JobsApiError>)
  })
})
