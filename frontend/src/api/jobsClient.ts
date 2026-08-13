import type {
  JobDetailResponse,
  JobListResponse,
  JobRetryResponse,
  JobScheduleListResponse,
  JobStatus,
} from '../types/jobs'
import { API_BASE_URL, buildAuthHeaders, captureRequestId, parseErrorEnvelope } from './request'

export class JobsApiError extends Error {
  status: number
  code?: string

  constructor(message: string, status: number, code?: string) {
    super(message)
    this.name = 'JobsApiError'
    this.status = status
    this.code = code
  }
}

async function toJobsApiError(response: Response, fallbackMessage: string): Promise<JobsApiError> {
  const parsed = await parseErrorEnvelope(response, fallbackMessage)
  return new JobsApiError(parsed.message, parsed.status, parsed.code)
}

export interface FetchJobsParams {
  status?: JobStatus
  job_type?: string
  limit?: number
  offset?: number
}

function buildJobsQuery(params?: FetchJobsParams): string {
  if (!params) {
    return ''
  }
  const search = new URLSearchParams()
  if (params.status) {
    search.set('status', params.status)
  }
  if (params.job_type) {
    search.set('job_type', params.job_type)
  }
  if (params.limit !== undefined) {
    search.set('limit', String(params.limit))
  }
  if (params.offset !== undefined) {
    search.set('offset', String(params.offset))
  }
  const query = search.toString()
  return query ? `?${query}` : ''
}

/** Fetches background jobs from ``GET /api/jobs``. */
export async function fetchJobs(params?: FetchJobsParams): Promise<JobListResponse> {
  const response = await fetch(`${API_BASE_URL}/api/jobs${buildJobsQuery(params)}`, {
    method: 'GET',
    headers: buildAuthHeaders({ json: false }),
  })

  captureRequestId(response)

  if (!response.ok) {
    throw await toJobsApiError(response, `Failed to load jobs: ${response.status}`)
  }

  return (await response.json()) as JobListResponse
}

/** Fetches one job from ``GET /api/jobs/{id}``. */
export async function fetchJobDetail(jobId: string): Promise<JobDetailResponse> {
  const response = await fetch(`${API_BASE_URL}/api/jobs/${encodeURIComponent(jobId)}`, {
    method: 'GET',
    headers: buildAuthHeaders({ json: false }),
  })

  captureRequestId(response)

  if (!response.ok) {
    throw await toJobsApiError(response, `Failed to load job: ${response.status}`)
  }

  return (await response.json()) as JobDetailResponse
}

/** Fetches recurring schedules from ``GET /api/jobs/schedules``. */
export async function fetchJobSchedules(): Promise<JobScheduleListResponse> {
  const response = await fetch(`${API_BASE_URL}/api/jobs/schedules`, {
    method: 'GET',
    headers: buildAuthHeaders({ json: false }),
  })

  captureRequestId(response)

  if (!response.ok) {
    throw await toJobsApiError(response, `Failed to load job schedules: ${response.status}`)
  }

  return (await response.json()) as JobScheduleListResponse
}

/** Re-queues a dead-letter job via ``POST /api/jobs/{id}/retry``. */
export async function retryJob(jobId: string): Promise<JobRetryResponse> {
  const response = await fetch(`${API_BASE_URL}/api/jobs/${encodeURIComponent(jobId)}/retry`, {
    method: 'POST',
    headers: buildAuthHeaders(),
  })

  captureRequestId(response)

  if (!response.ok) {
    throw await toJobsApiError(response, `Failed to retry job: ${response.status}`)
  }

  return (await response.json()) as JobRetryResponse
}
