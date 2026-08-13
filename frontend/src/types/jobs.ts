export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'dead_letter' | 'cancelled'

export type ScheduleStatus = 'enabled' | 'disabled'

export interface JobRecord {
  id: string
  job_type: string
  status: JobStatus
  payload: Record<string, unknown>
  result: Record<string, unknown> | null
  attempt_count: number
  max_attempts: number
  run_at: string
  last_error: string | null
  schedule_id: string | null
  created_at: string
  updated_at: string
  started_at: string | null
  finished_at: string | null
}

export interface JobListResponse {
  jobs: JobRecord[]
}

export type JobDetailResponse = JobRecord

export interface JobRetryResponse {
  job: JobRecord
}

export interface JobScheduleRecord {
  id: string
  name: string
  job_type: string
  payload: Record<string, unknown>
  interval_seconds: number
  next_run_at: string
  status: ScheduleStatus
  created_at: string
  updated_at: string
}

export interface JobScheduleListResponse {
  schedules: JobScheduleRecord[]
}

export const JOB_STATUSES: JobStatus[] = [
  'queued',
  'running',
  'succeeded',
  'failed',
  'dead_letter',
  'cancelled',
]

export function formatJobStatus(status: JobStatus): string {
  switch (status) {
    case 'queued':
      return 'Queued'
    case 'running':
      return 'Running'
    case 'succeeded':
      return 'Succeeded'
    case 'failed':
      return 'Failed'
    case 'dead_letter':
      return 'Dead letter'
    case 'cancelled':
      return 'Cancelled'
  }
}

export function formatScheduleStatus(status: ScheduleStatus): string {
  return status === 'enabled' ? 'Enabled' : 'Disabled'
}

export function formatJobType(jobType: string): string {
  return jobType.replace(/_/g, ' ')
}

export function formatIntervalSeconds(seconds: number): string {
  if (seconds < 60) {
    return `${seconds}s`
  }
  if (seconds < 3600) {
    const minutes = Math.round(seconds / 60)
    return `${minutes}m`
  }
  if (seconds < 86400) {
    const hours = Math.round(seconds / 3600)
    return `${hours}h`
  }
  const days = Math.round(seconds / 86400)
  return `${days}d`
}

export function formatTimestamp(value: string | null): string {
  if (!value) {
    return '—'
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  try {
    return date.toLocaleString()
  } catch {
    return value
  }
}

export function truncateError(error: string | null, maxLength = 120): string {
  if (!error) {
    return '—'
  }
  if (error.length <= maxLength) {
    return error
  }
  return `${error.slice(0, maxLength - 1)}…`
}

export function jobStatusBadgeClass(status: JobStatus): string {
  switch (status) {
    case 'succeeded':
      return 'border-brand-300 bg-brand-100 text-brand-800'
    case 'running':
      return 'border-sky-300 bg-sky-100 text-sky-800'
    case 'queued':
      return 'border-shell-300 bg-shell-100 text-shell-800'
    case 'dead_letter':
      return 'border-danger-300 bg-danger-100 text-danger-700'
    case 'failed':
      return 'border-amber-300 bg-amber-100 text-amber-900'
    case 'cancelled':
      return 'border-shell-300 bg-shell-50 text-shell-600'
    default:
      return 'border-shell-300 bg-shell-100 text-shell-800'
  }
}

export function scheduleStatusBadgeClass(status: ScheduleStatus): string {
  return status === 'enabled'
    ? 'border-brand-300 bg-brand-100 text-brand-800'
    : 'border-shell-300 bg-shell-100 text-shell-700'
}
