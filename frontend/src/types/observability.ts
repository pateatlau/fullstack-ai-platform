export type UsageGroupBy = 'day' | 'provider' | 'model'

export interface UsageSummaryRow {
  day: string | null
  provider: string | null
  model: string | null
  request_count: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cost_usd: number | null
}

export interface UsageSummaryResponse {
  since: string
  until: string
  group_by: UsageGroupBy
  rows: UsageSummaryRow[]
}

export interface UsageSummaryParams {
  since?: string
  until?: string
  group_by?: UsageGroupBy
}

/** ISO date string (YYYY-MM-DD) for trailing ``days`` ending today (UTC). */
export function defaultTrailingDateRange(days = 30): { since: string; until: string } {
  const until = new Date()
  const since = new Date(until)
  since.setUTCDate(since.getUTCDate() - days)
  return {
    since: since.toISOString().slice(0, 10),
    until: until.toISOString().slice(0, 10),
  }
}

export function formatCostUsd(value: number | null): string {
  if (value === null) {
    return '—'
  }
  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  }).format(value)
}

export function formatInteger(value: number): string {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(value)
}
