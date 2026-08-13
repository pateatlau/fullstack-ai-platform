import { useCallback, useEffect, useMemo, useState } from 'react'
import { fetchJobSchedules, fetchJobs, JobsApiError, retryJob } from '../api/jobsClient'
import { AppNav } from '../components/AppNav'
import { AuthControls } from '../components/AuthControls'
import { EmptyState } from '../components/EmptyState'
import { LoadingIndicator } from '../components/LoadingIndicator'
import { useAuthContext } from '../context/AuthContext'
import { useChatStreamingEnabled } from '../hooks/useChatStreamingEnabled'
import type { JobRecord, JobScheduleRecord, JobStatus } from '../types/jobs'
import {
  formatIntervalSeconds,
  formatJobStatus,
  formatJobType,
  formatScheduleStatus,
  formatTimestamp,
  JOB_STATUSES,
  jobStatusBadgeClass,
  scheduleStatusBadgeClass,
  truncateError,
} from '../types/jobs'

type JobsPageTab = 'jobs' | 'schedules'

function isInvalidAccessTokenError(error: unknown): boolean {
  return (
    error instanceof JobsApiError && (error.code === 'invalid_access_token' || error.status === 401)
  )
}

interface StatusBannerProps {
  tone: 'success' | 'error'
  message: string
}

function StatusBanner({ tone, message }: StatusBannerProps) {
  const toneClass =
    tone === 'success'
      ? 'border-brand-500/30 bg-brand-500/10 text-brand-700'
      : 'border-danger-600/30 bg-danger-100 text-danger-600'

  return (
    <div
      className={`rounded-lg border px-3 py-2 text-sm ${toneClass}`}
      role={tone === 'error' ? 'alert' : 'status'}
      aria-live={tone === 'error' ? 'assertive' : 'polite'}
    >
      {message}
    </div>
  )
}

function JobsUnavailableNotice() {
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-3 py-8 sm:px-4">
      <section className="rounded-chat border border-shell-800/15 bg-white p-5 shadow-chat-card">
        <h2 className="text-base font-semibold text-shell-950">
          Background jobs are not available
        </h2>
        <p className="mt-2 text-sm text-shell-700">
          The Background Jobs platform is disabled on this server. Your chat experience is
          unchanged.
        </p>
        <a
          href="/"
          className="mt-4 inline-flex text-sm font-semibold text-brand-600 underline-offset-2 hover:underline"
        >
          Return to chat
        </a>
      </section>
    </div>
  )
}

interface TabButtonProps {
  active: boolean
  label: string
  onClick: () => void
}

function TabButton({ active, label, onClick }: TabButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`rounded-lg border px-3 py-1.5 text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 ${
        active
          ? 'border-brand-500 bg-brand-500/10 text-brand-800'
          : 'border-shell-800/20 bg-white text-shell-800 hover:bg-shell-900/5'
      }`}
    >
      {label}
    </button>
  )
}

interface JobsTabProps {
  onApiError: (error: unknown) => boolean
}

function JobsTab({ onApiError }: JobsTabProps) {
  const [jobs, setJobs] = useState<JobRecord[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<JobStatus | ''>('')
  const [jobTypeFilter, setJobTypeFilter] = useState('')
  const [retryingJobId, setRetryingJobId] = useState<string | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)

  const jobTypeOptions = useMemo(() => {
    const types = new Set(jobs.map((job) => job.job_type))
    return Array.from(types).sort()
  }, [jobs])

  useEffect(() => {
    let cancelled = false

    void (async () => {
      setIsLoading(true)
      setError(null)
      try {
        const response = await fetchJobs({
          status: statusFilter || undefined,
          job_type: jobTypeFilter || undefined,
        })
        if (!cancelled) {
          setJobs(response.jobs)
        }
      } catch (apiError) {
        if (!cancelled) {
          if (!onApiError(apiError)) {
            if (apiError instanceof JobsApiError) {
              setError(apiError.message)
            } else {
              setError('Something went wrong. Please try again.')
            }
          }
          setJobs([])
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false)
        }
      }
    })()

    return () => {
      cancelled = true
    }
  }, [jobTypeFilter, onApiError, refreshKey, statusFilter])

  const handleRetry = async (jobId: string) => {
    setRetryingJobId(jobId)
    setError(null)
    setSuccessMessage(null)
    try {
      const response = await retryJob(jobId)
      setSuccessMessage(`Job ${response.job.id.slice(0, 8)}… re-queued.`)
      setRefreshKey((current) => current + 1)
    } catch (apiError) {
      if (apiError instanceof JobsApiError) {
        setError(apiError.message)
      } else {
        setError('Failed to retry job. Please try again.')
      }
    } finally {
      setRetryingJobId(null)
    }
  }

  return (
    <section
      aria-labelledby="jobs-list-heading"
      className="rounded-chat border border-shell-800/15 bg-white p-4 shadow-chat-card sm:p-5"
    >
      <h2 id="jobs-list-heading" className="text-base font-semibold text-shell-950">
        Background jobs
      </h2>
      <p className="mt-2 text-sm text-shell-700">
        Read-only queue snapshot. Retry is available only for dead-letter jobs.
      </p>

      <div className="mt-4 flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs font-medium text-shell-700">
          Status
          <select
            value={statusFilter}
            onChange={(event) => {
              setSuccessMessage(null)
              setStatusFilter(event.target.value as JobStatus | '')
            }}
            className="rounded-lg border border-shell-800/20 bg-white px-2 py-1.5 text-sm text-shell-900"
          >
            <option value="">All statuses</option>
            {JOB_STATUSES.map((status) => (
              <option key={status} value={status}>
                {formatJobStatus(status)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium text-shell-700">
          Job type
          <select
            value={jobTypeFilter}
            onChange={(event) => {
              setSuccessMessage(null)
              setJobTypeFilter(event.target.value)
            }}
            className="rounded-lg border border-shell-800/20 bg-white px-2 py-1.5 text-sm text-shell-900"
          >
            <option value="">All types</option>
            {jobTypeOptions.map((jobType) => (
              <option key={jobType} value={jobType}>
                {formatJobType(jobType)}
              </option>
            ))}
          </select>
        </label>
      </div>

      {successMessage ? (
        <div className="mt-4">
          <StatusBanner tone="success" message={successMessage} />
        </div>
      ) : null}
      {error ? (
        <div className="mt-4">
          <StatusBanner tone="error" message={error} />
        </div>
      ) : null}

      {isLoading ? (
        <LoadingIndicator variant="inline" label="Loading jobs…" className="mt-4" />
      ) : jobs.length === 0 ? (
        <EmptyState
          className="mt-4 border-shell-800/20 bg-shell-50/80 [&_h3]:text-shell-950 [&_p]:text-shell-700"
          title="No jobs found"
          description="No background jobs match the current filters."
        />
      ) : (
        <div className="mt-4 overflow-x-auto">
          <table className="min-w-full text-left text-sm text-shell-900">
            <caption className="sr-only">Background jobs queue snapshot</caption>
            <thead className="border-b border-shell-800/15 text-xs uppercase tracking-wide text-shell-700">
              <tr>
                <th scope="col" className="px-2 py-2">
                  Type
                </th>
                <th scope="col" className="px-2 py-2">
                  Status
                </th>
                <th scope="col" className="px-2 py-2">
                  Attempts
                </th>
                <th scope="col" className="px-2 py-2">
                  Created
                </th>
                <th scope="col" className="px-2 py-2">
                  Updated
                </th>
                <th scope="col" className="px-2 py-2">
                  Last error
                </th>
                <th scope="col" className="px-2 py-2">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-shell-800/10">
              {jobs.map((job) => (
                <tr key={job.id}>
                  <td className="px-2 py-2 align-top">
                    <div className="font-medium">{formatJobType(job.job_type)}</div>
                    <div className="mt-0.5 font-mono text-xs text-shell-600">
                      {job.id.slice(0, 8)}…
                    </div>
                  </td>
                  <td className="px-2 py-2 align-top">
                    <span
                      className={`inline-flex rounded-full border px-2 py-0.5 text-xs font-medium ${jobStatusBadgeClass(job.status)}`}
                    >
                      {formatJobStatus(job.status)}
                    </span>
                  </td>
                  <td className="px-2 py-2 align-top">
                    {job.attempt_count}/{job.max_attempts}
                  </td>
                  <td className="px-2 py-2 align-top whitespace-nowrap">
                    {formatTimestamp(job.created_at)}
                  </td>
                  <td className="px-2 py-2 align-top whitespace-nowrap">
                    {formatTimestamp(job.updated_at)}
                  </td>
                  <td className="max-w-xs px-2 py-2 align-top text-xs text-shell-700">
                    {truncateError(job.last_error)}
                  </td>
                  <td className="px-2 py-2 align-top">
                    {job.status === 'dead_letter' ? (
                      <button
                        type="button"
                        onClick={() => void handleRetry(job.id)}
                        disabled={retryingJobId === job.id}
                        className="rounded-lg border border-brand-500/40 bg-brand-500/10 px-2 py-1 text-xs font-semibold text-brand-800 transition hover:bg-brand-500/20 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {retryingJobId === job.id ? 'Retrying…' : 'Retry'}
                      </button>
                    ) : (
                      <span className="text-xs text-shell-600">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

interface SchedulesTabProps {
  onApiError: (error: unknown) => boolean
}

function SchedulesTab({ onApiError }: SchedulesTabProps) {
  const [schedules, setSchedules] = useState<JobScheduleRecord[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    void (async () => {
      setIsLoading(true)
      setError(null)
      try {
        const response = await fetchJobSchedules()
        if (!cancelled) {
          setSchedules(response.schedules)
        }
      } catch (apiError) {
        if (!cancelled) {
          if (!onApiError(apiError)) {
            if (apiError instanceof JobsApiError) {
              setError(apiError.message)
            } else {
              setError('Something went wrong. Please try again.')
            }
          }
          setSchedules([])
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false)
        }
      }
    })()

    return () => {
      cancelled = true
    }
  }, [onApiError])

  return (
    <section
      aria-labelledby="schedules-list-heading"
      className="rounded-chat border border-shell-800/15 bg-white p-4 shadow-chat-card sm:p-5"
    >
      <h2 id="schedules-list-heading" className="text-base font-semibold text-shell-950">
        Recurring schedules
      </h2>
      <p className="mt-2 text-sm text-shell-700">
        Read-only view of interval-based schedules seeded on the server.
      </p>

      {error ? <StatusBanner tone="error" message={error} /> : null}

      {isLoading ? (
        <LoadingIndicator variant="inline" label="Loading schedules…" className="mt-4" />
      ) : schedules.length === 0 ? (
        <EmptyState
          className="mt-4 border-shell-800/20 bg-shell-50/80 [&_h3]:text-shell-950 [&_p]:text-shell-700"
          title="No schedules found"
          description="No recurring job schedules are configured on this server."
        />
      ) : (
        <div className="mt-4 overflow-x-auto">
          <table className="min-w-full text-left text-sm text-shell-900">
            <caption className="sr-only">Recurring background job schedules</caption>
            <thead className="border-b border-shell-800/15 text-xs uppercase tracking-wide text-shell-700">
              <tr>
                <th scope="col" className="px-2 py-2">
                  Name
                </th>
                <th scope="col" className="px-2 py-2">
                  Job type
                </th>
                <th scope="col" className="px-2 py-2">
                  Interval
                </th>
                <th scope="col" className="px-2 py-2">
                  Next run
                </th>
                <th scope="col" className="px-2 py-2">
                  Status
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-shell-800/10">
              {schedules.map((schedule) => (
                <tr key={schedule.id}>
                  <td className="px-2 py-2 align-top font-medium">{schedule.name}</td>
                  <td className="px-2 py-2 align-top">{formatJobType(schedule.job_type)}</td>
                  <td className="px-2 py-2 align-top">
                    {formatIntervalSeconds(schedule.interval_seconds)}
                  </td>
                  <td className="px-2 py-2 align-top whitespace-nowrap">
                    {formatTimestamp(schedule.next_run_at)}
                  </td>
                  <td className="px-2 py-2 align-top">
                    <span
                      className={`inline-flex rounded-full border px-2 py-0.5 text-xs font-medium ${scheduleStatusBadgeClass(schedule.status)}`}
                    >
                      {formatScheduleStatus(schedule.status)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function JobsContent() {
  const { handleInvalidAccessToken } = useAuthContext()
  const [activeTab, setActiveTab] = useState<JobsPageTab>('jobs')
  const [featureDisabled, setFeatureDisabled] = useState(false)

  const handleApiError = useCallback(
    (apiError: unknown): boolean => {
      if (isInvalidAccessTokenError(apiError)) {
        handleInvalidAccessToken()
        return true
      }
      if (apiError instanceof JobsApiError) {
        if (apiError.code === 'feature_disabled') {
          setFeatureDisabled(true)
          return true
        }
      }
      return false
    },
    [handleInvalidAccessToken],
  )

  if (featureDisabled) {
    return <JobsUnavailableNotice />
  }

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-6 px-3 py-4 sm:px-4 sm:py-6">
      <section
        aria-labelledby="jobs-overview-heading"
        className="rounded-chat border border-shell-800/15 bg-white p-4 shadow-chat-card sm:p-5"
      >
        <h2 id="jobs-overview-heading" className="text-base font-semibold text-shell-950">
          Jobs dashboard
        </h2>
        <p className="mt-2 text-sm text-shell-700">
          Monitor background job queue state and recurring schedules. Payload and result contents
          are not shown.
        </p>
        <div className="mt-4 flex flex-wrap gap-2" role="tablist" aria-label="Jobs dashboard views">
          <TabButton
            active={activeTab === 'jobs'}
            label="Jobs"
            onClick={() => setActiveTab('jobs')}
          />
          <TabButton
            active={activeTab === 'schedules'}
            label="Schedules"
            onClick={() => setActiveTab('schedules')}
          />
        </div>
      </section>

      {activeTab === 'jobs' ? (
        <JobsTab onApiError={handleApiError} />
      ) : (
        <SchedulesTab onApiError={handleApiError} />
      )}
    </div>
  )
}

export function JobsPage() {
  const { backgroundJobsEnabled, healthLoading } = useChatStreamingEnabled()

  return (
    <div className="min-h-dvh bg-linear-to-b from-shell-50 via-shell-100 to-[#ebeff6]">
      <header className="sticky top-0 z-20 border-b border-shell-800/15 bg-shell-50/90 px-3 py-2 backdrop-blur sm:px-4">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <AppNav current="jobs" />
            <h1 className="min-w-0 truncate text-sm font-semibold tracking-wide text-shell-900 sm:text-base">
              Jobs
            </h1>
          </div>
          <div className="shrink-0">
            <AuthControls />
          </div>
        </div>
      </header>

      {healthLoading ? (
        <div className="mx-auto flex w-full max-w-5xl justify-center px-3 py-12 sm:px-4">
          <LoadingIndicator variant="inline" label="Loading jobs…" />
        </div>
      ) : backgroundJobsEnabled ? (
        <JobsContent />
      ) : (
        <JobsUnavailableNotice />
      )}
    </div>
  )
}
