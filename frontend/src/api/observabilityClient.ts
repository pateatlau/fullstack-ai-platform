import type { UsageSummaryParams, UsageSummaryResponse } from '../types/observability'
import { API_BASE_URL, buildAuthHeaders, captureRequestId, parseErrorEnvelope } from './request'

export class ObservabilityApiError extends Error {
  status: number
  code?: string

  constructor(message: string, status: number, code?: string) {
    super(message)
    this.name = 'ObservabilityApiError'
    this.status = status
    this.code = code
  }
}

async function toObservabilityApiError(
  response: Response,
  fallbackMessage: string,
): Promise<ObservabilityApiError> {
  const parsed = await parseErrorEnvelope(response, fallbackMessage)
  return new ObservabilityApiError(parsed.message, parsed.status, parsed.code)
}

/** Fetches caller-scoped usage/cost summary from ``GET /api/observability/usage``. */
export async function fetchUsageSummary(
  params: UsageSummaryParams = {},
): Promise<UsageSummaryResponse> {
  const search = new URLSearchParams()
  if (params.since) {
    search.set('since', params.since)
  }
  if (params.until) {
    search.set('until', params.until)
  }
  if (params.group_by) {
    search.set('group_by', params.group_by)
  }

  const query = search.toString()
  const url = `${API_BASE_URL}/api/observability/usage${query ? `?${query}` : ''}`

  const response = await fetch(url, {
    method: 'GET',
    headers: buildAuthHeaders({ json: false }),
  })

  captureRequestId(response)

  if (!response.ok) {
    throw await toObservabilityApiError(
      response,
      `Failed to load usage summary: ${response.status}`,
    )
  }

  return (await response.json()) as UsageSummaryResponse
}
