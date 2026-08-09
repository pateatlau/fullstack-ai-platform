import { API_BASE_URL } from './request'

export interface HealthResponse {
  status: string
  provider: string
  version: string
  chat_streaming_enabled: boolean
  tools_enabled: boolean
  rag_enabled: boolean
  voice_enabled: boolean
  memory_enabled: boolean
  workflow_engine_enabled: boolean
  observability_enabled: boolean
  capabilities?: {
    by_provider: Record<
      string,
      {
        supports_streaming: boolean
        supports_tool_calling: boolean
      }
    >
  }
}

/** Fetches server health and feature flags exposed by ``GET /api/health``. */
export async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE_URL}/api/health`)
  if (!response.ok) {
    throw new Error(`Health check failed: ${response.status}`)
  }
  return (await response.json()) as HealthResponse
}
