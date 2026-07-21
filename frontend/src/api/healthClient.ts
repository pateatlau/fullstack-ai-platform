const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? 'http://localhost:8000'

export interface HealthResponse {
  status: string
  provider: string
  version: string
  chat_streaming_enabled: boolean
}

/** Fetches server health and feature flags exposed by ``GET /api/health``. */
export async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE_URL}/api/health`)
  if (!response.ok) {
    throw new Error(`Health check failed: ${response.status}`)
  }
  return (await response.json()) as HealthResponse
}
