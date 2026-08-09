import { useCallback, useEffect, useMemo, useState } from 'react'
import { fetchUsageSummary, ObservabilityApiError } from '../api/observabilityClient'
import { AppNav } from '../components/AppNav'
import { AuthControls } from '../components/AuthControls'
import { EmptyState } from '../components/EmptyState'
import { LoadingIndicator } from '../components/LoadingIndicator'
import { useAuthContext } from '../context/AuthContext'
import { useChatStreamingEnabled } from '../hooks/useChatStreamingEnabled'
import type { UsageGroupBy, UsageSummaryResponse, UsageSummaryRow } from '../types/observability'
import { defaultTrailingDateRange, formatCostUsd, formatInteger } from '../types/observability'

function isInvalidAccessTokenError(error: unknown): boolean {
  return (
    error instanceof ObservabilityApiError &&
    (error.code === 'invalid_access_token' || error.status === 401)
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
      aria-live="polite"
    >
      {message}
    </div>
  )
}

function ObservabilityUnavailableNotice() {
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-3 py-8 sm:px-4">
      <section className="rounded-chat border border-shell-800/15 bg-white p-5 shadow-chat-card">
        <h2 className="text-base font-semibold text-shell-950">Observability is not available</h2>
        <p className="mt-2 text-sm text-shell-700">
          Usage and cost reporting is disabled on this server. Your chat experience is unchanged.
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

function aggregateTotals(rows: UsageSummaryRow[]) {
  return rows.reduce(
    (acc, row) => ({
      request_count: acc.request_count + row.request_count,
      prompt_tokens: acc.prompt_tokens + row.prompt_tokens,
      completion_tokens: acc.completion_tokens + row.completion_tokens,
      total_tokens: acc.total_tokens + row.total_tokens,
      cost_usd:
        row.cost_usd === null
          ? acc.cost_usd
          : acc.cost_usd === null
            ? row.cost_usd
            : acc.cost_usd + row.cost_usd,
    }),
    {
      request_count: 0,
      prompt_tokens: 0,
      completion_tokens: 0,
      total_tokens: 0,
      cost_usd: null as number | null,
    },
  )
}

function groupByLabel(groupBy: UsageGroupBy): string {
  switch (groupBy) {
    case 'day':
      return 'Day'
    case 'provider':
      return 'Provider'
    case 'model':
      return 'Model'
  }
}

function ObservabilityContent() {
  const { handleInvalidAccessToken } = useAuthContext()
  const defaultRange = useMemo(() => defaultTrailingDateRange(), [])
  const [since, setSince] = useState(defaultRange.since)
  const [until, setUntil] = useState(defaultRange.until)
  const [groupBy, setGroupBy] = useState<UsageGroupBy>('day')
  const [summary, setSummary] = useState<UsageSummaryResponse | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const handleApiError = useCallback(
    (apiError: unknown): boolean => {
      if (isInvalidAccessTokenError(apiError)) {
        handleInvalidAccessToken()
        return true
      }
      if (apiError instanceof ObservabilityApiError) {
        if (apiError.code === 'feature_disabled') {
          setError('Observability is not enabled on this server.')
          return true
        }
        setError(apiError.message)
        return true
      }
      setError('Something went wrong. Please try again.')
      return true
    },
    [handleInvalidAccessToken],
  )

  const reloadSummary = useCallback(
    async (params: { since: string; until: string; groupBy: UsageGroupBy }) => {
      setError(null)
      setIsLoading(true)
      try {
        const response = await fetchUsageSummary({
          since: params.since,
          until: params.until,
          group_by: params.groupBy,
        })
        setSummary(response)
        setSince(response.since)
        setUntil(response.until)
        setGroupBy(response.group_by)
      } catch (apiError) {
        handleApiError(apiError)
        setSummary(null)
      } finally {
        setIsLoading(false)
      }
    },
    [handleApiError],
  )

  useEffect(() => {
    let cancelled = false

    void (async () => {
      setIsLoading(true)
      setError(null)
      try {
        const response = await fetchUsageSummary({
          since: defaultRange.since,
          until: defaultRange.until,
          group_by: 'day',
        })
        if (!cancelled) {
          setSummary(response)
          setSince(response.since)
          setUntil(response.until)
          setGroupBy(response.group_by)
        }
      } catch (apiError) {
        if (!cancelled) {
          handleApiError(apiError)
          setSummary(null)
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
  }, [defaultRange.since, defaultRange.until, handleApiError])

  const totals = useMemo(() => aggregateTotals(summary?.rows ?? []), [summary?.rows])

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-3 py-4 sm:px-4 sm:py-6">
      {error ? <StatusBanner tone="error" message={error} /> : null}

      <section
        aria-labelledby="observability-status-heading"
        className="rounded-chat border border-shell-800/15 bg-white p-4 shadow-chat-card sm:p-5"
      >
        <h2 id="observability-status-heading" className="text-base font-semibold text-shell-950">
          Observability status
        </h2>
        <p className="mt-2 text-sm text-shell-700">
          Usage and estimated cost are active. Summaries reflect your own LLM requests only.
        </p>
      </section>

      <section
        aria-labelledby="usage-filters-heading"
        className="rounded-chat border border-shell-800/15 bg-white p-4 shadow-chat-card sm:p-5"
      >
        <h2 id="usage-filters-heading" className="text-base font-semibold text-shell-950">
          Usage filters
        </h2>
        <form
          className="mt-4 grid gap-4 sm:grid-cols-2"
          onSubmit={(event) => {
            event.preventDefault()
            void reloadSummary({ since, until, groupBy })
          }}
        >
          <div>
            <label htmlFor="usage-since" className="block text-sm font-medium text-shell-900">
              From
            </label>
            <input
              id="usage-since"
              type="date"
              value={since}
              onChange={(event) => setSince(event.target.value)}
              className="mt-1 w-full rounded-lg border border-shell-800/20 px-3 py-2 text-sm text-shell-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
            />
          </div>
          <div>
            <label htmlFor="usage-until" className="block text-sm font-medium text-shell-900">
              To
            </label>
            <input
              id="usage-until"
              type="date"
              value={until}
              onChange={(event) => setUntil(event.target.value)}
              className="mt-1 w-full rounded-lg border border-shell-800/20 px-3 py-2 text-sm text-shell-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
            />
          </div>
          <div className="sm:col-span-2">
            <label htmlFor="usage-group-by" className="block text-sm font-medium text-shell-900">
              Group by
            </label>
            <select
              id="usage-group-by"
              value={groupBy}
              onChange={(event) => {
                const nextGroupBy = event.target.value as UsageGroupBy
                setGroupBy(nextGroupBy)
                void reloadSummary({ since, until, groupBy: nextGroupBy })
              }}
              className="mt-1 w-full rounded-lg border border-shell-800/20 px-3 py-2 text-sm text-shell-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
            >
              <option value="day">Day</option>
              <option value="provider">Provider</option>
              <option value="model">Model</option>
            </select>
          </div>
          <div className="sm:col-span-2">
            <button
              type="submit"
              disabled={isLoading}
              className="inline-flex min-h-11 w-full items-center justify-center rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-brand-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:opacity-60 sm:w-auto"
            >
              {isLoading ? 'Loading…' : 'Apply filters'}
            </button>
          </div>
        </form>
      </section>

      <section
        aria-labelledby="usage-summary-heading"
        className="rounded-chat border border-shell-800/15 bg-white p-4 shadow-chat-card sm:p-5"
      >
        <h2 id="usage-summary-heading" className="text-base font-semibold text-shell-950">
          Usage summary
        </h2>
        <p className="mt-1 text-sm text-shell-700">
          {summary
            ? `${summary.since} through ${summary.until}, grouped by ${groupByLabel(summary.group_by).toLowerCase()}.`
            : 'Estimated token usage and cost for the selected range.'}
        </p>

        {isLoading ? (
          <LoadingIndicator variant="inline" label="Loading usage summary…" className="mt-4" />
        ) : summary && summary.rows.length === 0 ? (
          <EmptyState
            className="mt-4 border-shell-800/20 bg-shell-50/80 [&_h3]:text-shell-950 [&_p]:text-shell-700"
            title="No usage recorded"
            description="No LLM requests were recorded for you in this date range."
          />
        ) : summary ? (
          <div className="mt-4 overflow-x-auto">
            <table className="min-w-full text-left text-sm text-shell-900">
              <caption className="sr-only">
                Usage and cost summary grouped by {groupByLabel(summary.group_by)}
              </caption>
              <thead className="border-b border-shell-800/15 text-xs uppercase tracking-wide text-shell-700">
                <tr>
                  {summary.group_by === 'day' ? (
                    <th scope="col" className="px-2 py-2">
                      Day
                    </th>
                  ) : null}
                  {summary.group_by === 'provider' || summary.group_by === 'model' ? (
                    <th scope="col" className="px-2 py-2">
                      Provider
                    </th>
                  ) : null}
                  {summary.group_by === 'model' ? (
                    <th scope="col" className="px-2 py-2">
                      Model
                    </th>
                  ) : null}
                  <th scope="col" className="px-2 py-2">
                    Requests
                  </th>
                  <th scope="col" className="px-2 py-2">
                    Prompt tokens
                  </th>
                  <th scope="col" className="px-2 py-2">
                    Completion tokens
                  </th>
                  <th scope="col" className="px-2 py-2">
                    Total tokens
                  </th>
                  <th scope="col" className="px-2 py-2">
                    Est. cost
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-shell-800/10">
                {summary.rows.map((row, index) => (
                  <tr key={`${row.day ?? ''}-${row.provider ?? ''}-${row.model ?? ''}-${index}`}>
                    {summary.group_by === 'day' ? (
                      <td className="px-2 py-2 font-medium">{row.day ?? '—'}</td>
                    ) : null}
                    {summary.group_by === 'provider' || summary.group_by === 'model' ? (
                      <td className="px-2 py-2">{row.provider ?? '—'}</td>
                    ) : null}
                    {summary.group_by === 'model' ? (
                      <td className="px-2 py-2">{row.model ?? '—'}</td>
                    ) : null}
                    <td className="px-2 py-2">{formatInteger(row.request_count)}</td>
                    <td className="px-2 py-2">{formatInteger(row.prompt_tokens)}</td>
                    <td className="px-2 py-2">{formatInteger(row.completion_tokens)}</td>
                    <td className="px-2 py-2">{formatInteger(row.total_tokens)}</td>
                    <td className="px-2 py-2">{formatCostUsd(row.cost_usd)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot className="border-t border-shell-800/15 font-semibold">
                <tr>
                  <td
                    className="px-2 py-2"
                    colSpan={
                      summary.group_by === 'day' ? 1 : summary.group_by === 'provider' ? 1 : 2
                    }
                  >
                    Total
                  </td>
                  <td className="px-2 py-2">{formatInteger(totals.request_count)}</td>
                  <td className="px-2 py-2">{formatInteger(totals.prompt_tokens)}</td>
                  <td className="px-2 py-2">{formatInteger(totals.completion_tokens)}</td>
                  <td className="px-2 py-2">{formatInteger(totals.total_tokens)}</td>
                  <td className="px-2 py-2">{formatCostUsd(totals.cost_usd)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        ) : null}
      </section>
    </div>
  )
}

export function ObservabilityPage() {
  const { observabilityEnabled, healthLoading } = useChatStreamingEnabled()

  return (
    <div className="min-h-dvh bg-linear-to-b from-shell-50 via-shell-100 to-[#ebeff6]">
      <header className="sticky top-0 z-20 border-b border-shell-800/15 bg-shell-50/90 px-3 py-2 backdrop-blur sm:px-4">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <AppNav current="observability" />
            <h1 className="min-w-0 truncate text-sm font-semibold tracking-wide text-shell-900 sm:text-base">
              Observability
            </h1>
          </div>
          <div className="shrink-0">
            <AuthControls />
          </div>
        </div>
      </header>

      {healthLoading ? (
        <div className="mx-auto flex w-full max-w-3xl justify-center px-3 py-12 sm:px-4">
          <LoadingIndicator variant="inline" label="Loading observability…" />
        </div>
      ) : observabilityEnabled ? (
        <ObservabilityContent />
      ) : (
        <ObservabilityUnavailableNotice />
      )}
    </div>
  )
}
