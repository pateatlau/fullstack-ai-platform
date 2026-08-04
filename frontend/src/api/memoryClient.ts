import type {
  MemoryRecord,
  MemoryRecordListResponse,
  MemoryType,
  UserPreferenceItem,
  UserPreferenceListResponse,
  UserPreferenceUpsert,
} from '../types/memory'
import { API_BASE_URL, buildAuthHeaders, captureRequestId, parseErrorEnvelope } from './request'

export class MemoryApiError extends Error {
  status: number
  code?: string

  constructor(message: string, status: number, code?: string) {
    super(message)
    this.name = 'MemoryApiError'
    this.status = status
    this.code = code
  }
}

async function toMemoryApiError(
  response: Response,
  fallbackMessage: string,
): Promise<MemoryApiError> {
  const parsed = await parseErrorEnvelope(response, fallbackMessage)
  return new MemoryApiError(parsed.message, parsed.status, parsed.code)
}

export async function listMemoryRecords(options: {
  memoryType: MemoryType
  sessionId?: string
}): Promise<MemoryRecord[]> {
  const params = new URLSearchParams({ memory_type: options.memoryType })
  if (options.sessionId) {
    params.set('session_id', options.sessionId)
  }

  const response = await fetch(`${API_BASE_URL}/api/memory/records?${params.toString()}`, {
    method: 'GET',
    headers: buildAuthHeaders(),
  })

  captureRequestId(response)

  if (!response.ok) {
    throw await toMemoryApiError(response, `Failed to list memories: ${response.status}`)
  }

  const body = (await response.json()) as MemoryRecordListResponse
  return body.records
}

export async function getMemoryRecord(recordId: string): Promise<MemoryRecord> {
  const response = await fetch(`${API_BASE_URL}/api/memory/records/${recordId}`, {
    method: 'GET',
    headers: buildAuthHeaders(),
  })

  captureRequestId(response)

  if (!response.ok) {
    throw await toMemoryApiError(response, `Failed to load memory: ${response.status}`)
  }

  return (await response.json()) as MemoryRecord
}

export async function deleteMemoryRecord(recordId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/memory/records/${recordId}`, {
    method: 'DELETE',
    headers: buildAuthHeaders(),
  })

  captureRequestId(response)

  if (response.status === 404) {
    return
  }

  if (!response.ok) {
    throw await toMemoryApiError(response, `Failed to delete memory: ${response.status}`)
  }
}

export async function listPreferences(): Promise<UserPreferenceItem[]> {
  const response = await fetch(`${API_BASE_URL}/api/memory/preferences`, {
    method: 'GET',
    headers: buildAuthHeaders(),
  })

  captureRequestId(response)

  if (!response.ok) {
    throw await toMemoryApiError(response, `Failed to list preferences: ${response.status}`)
  }

  const body = (await response.json()) as UserPreferenceListResponse
  return body.preferences
}

export async function upsertPreference(
  key: string,
  body: UserPreferenceUpsert,
): Promise<UserPreferenceItem> {
  const response = await fetch(
    `${API_BASE_URL}/api/memory/preferences/${encodeURIComponent(key)}`,
    {
      method: 'PUT',
      headers: buildAuthHeaders(),
      body: JSON.stringify(body),
    },
  )

  captureRequestId(response)

  if (!response.ok) {
    throw await toMemoryApiError(response, `Failed to save preference: ${response.status}`)
  }

  return (await response.json()) as UserPreferenceItem
}

export async function deletePreference(key: string): Promise<void> {
  const response = await fetch(
    `${API_BASE_URL}/api/memory/preferences/${encodeURIComponent(key)}`,
    {
      method: 'DELETE',
      headers: buildAuthHeaders(),
    },
  )

  captureRequestId(response)

  if (response.status === 404) {
    return
  }

  if (!response.ok) {
    throw await toMemoryApiError(response, `Failed to delete preference: ${response.status}`)
  }
}

export async function clearSessionSummary(sessionId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/memory/sessions/${sessionId}/summary`, {
    method: 'DELETE',
    headers: buildAuthHeaders(),
  })

  captureRequestId(response)

  if (!response.ok) {
    throw await toMemoryApiError(
      response,
      `Failed to clear conversation summary: ${response.status}`,
    )
  }
}
