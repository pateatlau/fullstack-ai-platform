import type { PluginInventoryListResponse } from '../types/plugins'
import { API_BASE_URL, buildAuthHeaders, captureRequestId, parseErrorEnvelope } from './request'

export class PluginsApiError extends Error {
  status: number
  code?: string

  constructor(message: string, status: number, code?: string) {
    super(message)
    this.name = 'PluginsApiError'
    this.status = status
    this.code = code
  }
}

async function toPluginsApiError(
  response: Response,
  fallbackMessage: string,
): Promise<PluginsApiError> {
  const parsed = await parseErrorEnvelope(response, fallbackMessage)
  return new PluginsApiError(parsed.message, parsed.status, parsed.code)
}

/** Fetches loaded plugin inventory from ``GET /api/plugins``. */
export async function fetchPluginInventory(): Promise<PluginInventoryListResponse> {
  const response = await fetch(`${API_BASE_URL}/api/plugins`, {
    method: 'GET',
    headers: buildAuthHeaders({ json: false }),
  })

  captureRequestId(response)

  if (!response.ok) {
    throw await toPluginsApiError(response, `Failed to load plugin inventory: ${response.status}`)
  }

  return (await response.json()) as PluginInventoryListResponse
}
