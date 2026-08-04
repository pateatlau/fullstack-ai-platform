/** Memory domain types matching the public REST API (Epic 05 Phase 7). */

export type MemoryType = 'user' | 'project'

export interface MemoryRecord {
  id: string
  title: string | null
  content: string
  memory_type: MemoryType
  session_id: string | null
  created_at: string
  updated_at: string
}

export interface MemoryRecordListResponse {
  records: MemoryRecord[]
}

export interface UserPreferenceItem {
  key: string
  value: Record<string, unknown>
}

export interface UserPreferenceListResponse {
  preferences: UserPreferenceItem[]
}

export interface UserPreferenceUpsert {
  value: Record<string, unknown>
}

/** Client-side validation aligned with backend ``validate_preference_key``. */
export const PREFERENCE_KEY_PATTERN = /^[a-z][a-z0-9_]{0,127}$/

export function validatePreferenceKey(key: string): string | null {
  const normalized = key.trim()
  if (!normalized) {
    return 'Preference key is required.'
  }
  if (!PREFERENCE_KEY_PATTERN.test(normalized)) {
    return 'Use lowercase letters, digits, and underscores; start with a letter.'
  }
  return null
}

/** Parses a JSON object string for preference values. */
export function parsePreferenceValueJson(raw: string): {
  value: Record<string, unknown> | null
  error: string | null
} {
  const trimmed = raw.trim()
  if (!trimmed) {
    return { value: {}, error: null }
  }
  try {
    const parsed: unknown = JSON.parse(trimmed)
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return { value: null, error: 'Preference value must be a JSON object.' }
    }
    return { value: parsed as Record<string, unknown>, error: null }
  } catch {
    return { value: null, error: 'Invalid JSON. Enter a valid object, e.g. {"tone":"concise"}.' }
  }
}
