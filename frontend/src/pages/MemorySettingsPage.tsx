import { useCallback, useEffect, useState } from 'react'
import { listChatSessions } from '../api/chatClient'
import {
  deleteMemoryRecord,
  deletePreference,
  listMemoryRecords,
  listPreferences,
  MemoryApiError,
  upsertPreference,
  clearSessionSummary,
} from '../api/memoryClient'
import { AppNav } from '../components/AppNav'
import { AuthControls } from '../components/AuthControls'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { EmptyState } from '../components/EmptyState'
import { LoadingIndicator } from '../components/LoadingIndicator'
import { useAuthContext } from '../context/AuthContext'
import { useChatStreamingEnabled } from '../hooks/useChatStreamingEnabled'
import type { ChatSessionListItem } from '../types/chat'
import type { MemoryRecord, UserPreferenceItem } from '../types/memory'
import { parsePreferenceValueJson, validatePreferenceKey } from '../types/memory'

function isInvalidAccessTokenError(error: unknown): boolean {
  return (
    error instanceof MemoryApiError &&
    (error.code === 'invalid_access_token' || error.status === 401)
  )
}

function formatMemoryType(type: MemoryRecord['memory_type']): string {
  return type === 'project' ? 'Project' : 'Long-term'
}

function sessionLabel(session: ChatSessionListItem): string {
  return session.title?.trim() || 'Untitled chat'
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

function MemoryUnavailableNotice() {
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-3 py-8 sm:px-4">
      <section className="rounded-chat border border-shell-800/15 bg-white p-5 shadow-chat-card">
        <h2 className="text-base font-semibold text-shell-950">Memory is not available</h2>
        <p className="mt-2 text-sm text-shell-700">
          Memory management is disabled on this server. Your chat experience is unchanged.
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

function MemorySettingsContent() {
  const { handleInvalidAccessToken } = useAuthContext()
  const [userMemories, setUserMemories] = useState<MemoryRecord[]>([])
  const [projectMemories, setProjectMemories] = useState<MemoryRecord[]>([])
  const [preferences, setPreferences] = useState<UserPreferenceItem[]>([])
  const [sessions, setSessions] = useState<ChatSessionListItem[]>([])
  const [selectedSessionId, setSelectedSessionId] = useState<string>('')
  const [isLoading, setIsLoading] = useState(true)
  const [isRefreshingProject, setIsRefreshingProject] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  const [prefKey, setPrefKey] = useState('')
  const [prefValueJson, setPrefValueJson] = useState('{}')
  const [prefValidationError, setPrefValidationError] = useState<string | null>(null)
  const [isSavingPreference, setIsSavingPreference] = useState(false)
  const [deletingPrefKey, setDeletingPrefKey] = useState<string | null>(null)

  const [deletingMemoryId, setDeletingMemoryId] = useState<string | null>(null)
  const [isBulkDeleting, setIsBulkDeleting] = useState(false)

  const [clearSummarySessionId, setClearSummarySessionId] = useState<string | null>(null)
  const [clearingSummarySessionId, setClearingSummarySessionId] = useState<string | null>(null)
  const [clearedSummarySessionIds, setClearedSummarySessionIds] = useState<Set<string>>(
    () => new Set(),
  )

  const handleApiError = useCallback(
    (apiError: unknown): boolean => {
      if (isInvalidAccessTokenError(apiError)) {
        handleInvalidAccessToken()
        return true
      }
      if (apiError instanceof MemoryApiError) {
        if (apiError.code === 'feature_disabled') {
          setError('Memory is not enabled on this server.')
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

  const loadCoreData = useCallback(async () => {
    const [records, prefs, sessionList] = await Promise.all([
      listMemoryRecords({ memoryType: 'user' }),
      listPreferences(),
      listChatSessions(),
    ])
    setUserMemories(records)
    setPreferences(prefs)
    setSessions(sessionList)

    const nextSessionId = sessionList[0]?.id ?? ''
    setSelectedSessionId(nextSessionId)
    if (nextSessionId) {
      setIsRefreshingProject(true)
      try {
        const projectRecords = await listMemoryRecords({
          memoryType: 'project',
          sessionId: nextSessionId,
        })
        setProjectMemories(projectRecords)
      } finally {
        setIsRefreshingProject(false)
      }
    } else {
      setProjectMemories([])
    }
  }, [])

  const loadProjectMemories = useCallback(async (sessionId: string) => {
    if (!sessionId) {
      setProjectMemories([])
      return
    }
    setIsRefreshingProject(true)
    try {
      const records = await listMemoryRecords({ memoryType: 'project', sessionId })
      setProjectMemories(records)
    } finally {
      setIsRefreshingProject(false)
    }
  }, [])

  const handleSessionChange = useCallback(
    (sessionId: string) => {
      setSelectedSessionId(sessionId)
      void loadProjectMemories(sessionId).catch(handleApiError)
    },
    [handleApiError, loadProjectMemories],
  )

  useEffect(() => {
    let cancelled = false

    void (async () => {
      setIsLoading(true)
      setError(null)
      try {
        await loadCoreData()
      } catch (apiError) {
        if (!cancelled) {
          handleApiError(apiError)
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
  }, [handleApiError, loadCoreData])

  const refreshAll = useCallback(async () => {
    setError(null)
    try {
      await loadCoreData()
      if (selectedSessionId) {
        await loadProjectMemories(selectedSessionId)
      }
    } catch (apiError) {
      handleApiError(apiError)
    }
  }, [handleApiError, loadCoreData, loadProjectMemories, selectedSessionId])

  const handleSavePreference = async () => {
    setPrefValidationError(null)
    setError(null)
    setSuccess(null)

    const keyError = validatePreferenceKey(prefKey)
    if (keyError) {
      setPrefValidationError(keyError)
      return
    }

    const { value, error: valueError } = parsePreferenceValueJson(prefValueJson)
    if (valueError || value === null) {
      setPrefValidationError(valueError)
      return
    }

    setIsSavingPreference(true)
    try {
      await upsertPreference(prefKey.trim(), { value })
      setSuccess('Preference saved.')
      setPrefKey('')
      setPrefValueJson('{}')
      const updated = await listPreferences()
      setPreferences(updated)
    } catch (apiError) {
      handleApiError(apiError)
    } finally {
      setIsSavingPreference(false)
    }
  }

  const handleDeletePreference = async (key: string) => {
    setDeletingPrefKey(key)
    setError(null)
    setSuccess(null)
    try {
      await deletePreference(key)
      setSuccess('Preference removed.')
      setPreferences((current) => current.filter((item) => item.key !== key))
    } catch (apiError) {
      handleApiError(apiError)
    } finally {
      setDeletingPrefKey(null)
    }
  }

  const handleDeleteMemory = async (record: MemoryRecord) => {
    setDeletingMemoryId(record.id)
    setError(null)
    setSuccess(null)
    try {
      await deleteMemoryRecord(record.id)
      setSuccess('Memory deleted.')
      if (record.memory_type === 'project') {
        setProjectMemories((current) => current.filter((item) => item.id !== record.id))
      } else {
        setUserMemories((current) => current.filter((item) => item.id !== record.id))
      }
    } catch (apiError) {
      handleApiError(apiError)
    } finally {
      setDeletingMemoryId(null)
    }
  }

  const handleBulkDeleteUserMemories = async () => {
    if (userMemories.length === 0) {
      return
    }
    setIsBulkDeleting(true)
    setError(null)
    setSuccess(null)
    try {
      await Promise.all(userMemories.map((record) => deleteMemoryRecord(record.id)))
      setUserMemories([])
      setSuccess('All long-term memories deleted.')
    } catch (apiError) {
      handleApiError(apiError)
      await refreshAll()
    } finally {
      setIsBulkDeleting(false)
    }
  }

  const handleConfirmClearSummary = async () => {
    if (!clearSummarySessionId) {
      return
    }
    const sessionId = clearSummarySessionId
    setClearingSummarySessionId(sessionId)
    setError(null)
    setSuccess(null)
    try {
      await clearSessionSummary(sessionId)
      setClearedSummarySessionIds((current) => new Set(current).add(sessionId))
      setSuccess('Conversation summary cleared.')
    } catch (apiError) {
      handleApiError(apiError)
    } finally {
      setClearingSummarySessionId(null)
      setClearSummarySessionId(null)
    }
  }

  if (isLoading) {
    return (
      <div className="mx-auto flex w-full max-w-3xl justify-center px-3 py-12 sm:px-4">
        <LoadingIndicator variant="inline" label="Loading memory settings…" />
      </div>
    )
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-3 py-4 sm:px-4 sm:py-6">
      {error ? <StatusBanner tone="error" message={error} /> : null}
      {success ? <StatusBanner tone="success" message={success} /> : null}

      <section
        aria-labelledby="memory-status-heading"
        className="rounded-chat border border-shell-800/15 bg-white p-4 shadow-chat-card sm:p-5"
      >
        <h2 id="memory-status-heading" className="text-base font-semibold text-shell-950">
          Memory status
        </h2>
        <p className="mt-2 text-sm text-shell-700">
          Memory is active. The assistant uses stored preferences, long-term memories, and
          conversation summaries to personalize responses.
        </p>
      </section>

      <section
        aria-labelledby="preferences-heading"
        className="rounded-chat border border-shell-800/15 bg-white p-4 shadow-chat-card sm:p-5"
      >
        <h2 id="preferences-heading" className="text-base font-semibold text-shell-950">
          User preferences
        </h2>
        <p className="mt-1 text-sm text-shell-700">
          Structured settings the assistant applies across conversations.
        </p>

        <form
          className="mt-4 flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault()
            void handleSavePreference()
          }}
        >
          <div>
            <label htmlFor="pref-key" className="block text-sm font-medium text-shell-900">
              Key
            </label>
            <input
              id="pref-key"
              type="text"
              value={prefKey}
              onChange={(event) => setPrefKey(event.target.value)}
              placeholder="response_style"
              className="mt-1 w-full rounded-lg border border-shell-800/20 px-3 py-2 text-sm text-shell-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
              autoComplete="off"
            />
          </div>
          <div>
            <label htmlFor="pref-value" className="block text-sm font-medium text-shell-900">
              Value (JSON object)
            </label>
            <textarea
              id="pref-value"
              value={prefValueJson}
              onChange={(event) => setPrefValueJson(event.target.value)}
              rows={3}
              className="mt-1 w-full rounded-lg border border-shell-800/20 px-3 py-2 font-mono text-sm text-shell-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
            />
          </div>
          {prefValidationError ? (
            <p className="text-sm text-danger-600" role="alert">
              {prefValidationError}
            </p>
          ) : null}
          <button
            type="submit"
            disabled={isSavingPreference}
            className="inline-flex min-h-11 w-full items-center justify-center rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-brand-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:opacity-60 sm:w-auto"
          >
            {isSavingPreference ? 'Saving…' : 'Save preference'}
          </button>
        </form>

        {preferences.length === 0 ? (
          <EmptyState
            className="mt-4 border-shell-800/20 bg-shell-50/80 [&_h3]:text-shell-950 [&_p]:text-shell-700"
            title="No preferences yet"
            description="Add a preference above to guide how the assistant responds."
          />
        ) : (
          <ul className="mt-4 divide-y divide-shell-800/10" aria-label="Saved preferences">
            {preferences.map((preference) => (
              <li
                key={preference.key}
                className="flex flex-col gap-2 py-3 sm:flex-row sm:items-start sm:justify-between"
              >
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-shell-950">{preference.key}</p>
                  <pre className="mt-1 overflow-x-auto rounded bg-shell-50 px-2 py-1 text-xs text-shell-800">
                    {JSON.stringify(preference.value, null, 2)}
                  </pre>
                </div>
                <button
                  type="button"
                  aria-label={`Remove preference ${preference.key}`}
                  disabled={deletingPrefKey === preference.key}
                  className="inline-flex min-h-11 w-full shrink-0 items-center justify-center rounded-lg border border-shell-800/20 px-3 text-sm font-medium text-shell-900 transition hover:bg-shell-900/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:opacity-60 sm:w-auto"
                  onClick={() => void handleDeletePreference(preference.key)}
                >
                  {deletingPrefKey === preference.key ? 'Removing…' : 'Remove'}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <MemoryRecordsSection
        headingId="user-memories-heading"
        title="Long-term memories"
        description="Facts and context the assistant remembers about you across conversations."
        records={userMemories}
        deletingId={deletingMemoryId}
        onDelete={(record) => void handleDeleteMemory(record)}
        bulkAction={
          userMemories.length > 1
            ? {
                label: isBulkDeleting ? 'Deleting all…' : 'Delete all',
                disabled: isBulkDeleting,
                onClick: () => void handleBulkDeleteUserMemories(),
              }
            : undefined
        }
      />

      <section
        aria-labelledby="project-memories-heading"
        className="rounded-chat border border-shell-800/15 bg-white p-4 shadow-chat-card sm:p-5"
      >
        <h2 id="project-memories-heading" className="text-base font-semibold text-shell-950">
          Project memories
        </h2>
        <p className="mt-1 text-sm text-shell-700">
          Session-scoped memories tied to a specific chat conversation.
        </p>

        {sessions.length === 0 ? (
          <EmptyState
            className="mt-4 border-shell-800/20 bg-shell-50/80 [&_h3]:text-shell-950 [&_p]:text-shell-700"
            title="No chat sessions"
            description="Start a saved chat to manage project memories for that conversation."
          />
        ) : (
          <>
            <label
              htmlFor="project-session"
              className="mt-4 block text-sm font-medium text-shell-900"
            >
              Chat session
            </label>
            <select
              id="project-session"
              value={selectedSessionId}
              onChange={(event) => handleSessionChange(event.target.value)}
              className="mt-1 w-full rounded-lg border border-shell-800/20 px-3 py-2 text-sm text-shell-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
            >
              {sessions.map((session) => (
                <option key={session.id} value={session.id}>
                  {sessionLabel(session)}
                </option>
              ))}
            </select>

            {isRefreshingProject ? (
              <LoadingIndicator
                variant="inline"
                label="Loading project memories…"
                className="mt-4"
              />
            ) : (
              <MemoryRecordsSection
                className="mt-4 border-0 bg-transparent p-0 shadow-none"
                headingId="project-records-list"
                title=""
                description=""
                records={projectMemories}
                deletingId={deletingMemoryId}
                onDelete={(record) => void handleDeleteMemory(record)}
                hideHeading
              />
            )}
          </>
        )}
      </section>

      <section
        aria-labelledby="summaries-heading"
        className="rounded-chat border border-shell-800/15 bg-white p-4 shadow-chat-card sm:p-5"
      >
        <h2 id="summaries-heading" className="text-base font-semibold text-shell-950">
          Conversation summaries
        </h2>
        <p className="mt-1 text-sm text-shell-700">
          Rolling summaries compress long chats. Clear a summary to remove condensed context for
          that conversation.
        </p>

        {sessions.length === 0 ? (
          <EmptyState
            className="mt-4 border-shell-800/20 bg-shell-50/80 [&_h3]:text-shell-950 [&_p]:text-shell-700"
            title="No saved conversations"
            description="Summaries are created automatically for saved chats with enough messages."
          />
        ) : (
          <ul className="mt-4 divide-y divide-shell-800/10" aria-label="Chat sessions">
            {sessions.map((session) => {
              const wasCleared = clearedSummarySessionIds.has(session.id)
              const isClearing = clearingSummarySessionId === session.id
              return (
                <li
                  key={session.id}
                  className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between"
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-shell-950">
                      {sessionLabel(session)}
                    </p>
                    <p className="mt-1 text-xs text-shell-700">
                      {wasCleared
                        ? 'Summary cleared'
                        : 'Summary may be present for long conversations'}
                    </p>
                  </div>
                  <button
                    type="button"
                    aria-label={`Clear summary for ${sessionLabel(session)}`}
                    disabled={isClearing || wasCleared}
                    className="inline-flex min-h-11 w-full shrink-0 items-center justify-center rounded-lg border border-shell-800/20 px-3 text-sm font-medium text-shell-900 transition hover:bg-shell-900/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:opacity-60 sm:w-auto"
                    onClick={() => setClearSummarySessionId(session.id)}
                  >
                    {isClearing ? 'Clearing…' : wasCleared ? 'Cleared' : 'Clear summary'}
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </section>

      <ConfirmDialog
        open={clearSummarySessionId !== null}
        title="Clear conversation summary?"
        message="This removes the rolling summary for the selected chat. The full message history is not deleted."
        confirmLabel="Clear summary"
        isDestructive
        onConfirm={() => void handleConfirmClearSummary()}
        onCancel={() => setClearSummarySessionId(null)}
      />
    </div>
  )
}

interface MemoryRecordsSectionProps {
  headingId: string
  title: string
  description: string
  records: MemoryRecord[]
  deletingId: string | null
  onDelete: (record: MemoryRecord) => void
  bulkAction?: {
    label: string
    disabled: boolean
    onClick: () => void
  }
  className?: string
  hideHeading?: boolean
}

function MemoryRecordsSection({
  headingId,
  title,
  description,
  records,
  deletingId,
  onDelete,
  bulkAction,
  className,
  hideHeading = false,
}: MemoryRecordsSectionProps) {
  const sectionClass =
    className ?? 'rounded-chat border border-shell-800/15 bg-white p-4 shadow-chat-card sm:p-5'

  return (
    <section aria-labelledby={headingId} className={sectionClass}>
      {!hideHeading ? (
        <>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <h2 id={headingId} className="text-base font-semibold text-shell-950">
                {title}
              </h2>
              <p className="mt-1 text-sm text-shell-700">{description}</p>
            </div>
            {bulkAction ? (
              <button
                type="button"
                disabled={bulkAction.disabled}
                className="inline-flex min-h-11 shrink-0 items-center justify-center rounded-lg border border-danger-600/30 px-3 text-sm font-medium text-danger-600 transition hover:bg-danger-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger-600 disabled:cursor-not-allowed disabled:opacity-60"
                onClick={bulkAction.onClick}
              >
                {bulkAction.label}
              </button>
            ) : null}
          </div>
        </>
      ) : null}

      {records.length === 0 ? (
        <EmptyState
          className="mt-4 border-shell-800/20 bg-shell-50/80 [&_h3]:text-shell-950 [&_p]:text-shell-700"
          title="No memories stored"
          description="Memories are created automatically from your conversations when relevant."
        />
      ) : (
        <ul className="mt-4 divide-y divide-shell-800/10" aria-label={title || 'Stored memories'}>
          {records.map((record) => (
            <li
              key={record.id}
              className="flex flex-col gap-2 py-3 sm:flex-row sm:items-start sm:justify-between"
            >
              <div className="min-w-0 flex-1">
                {record.title ? (
                  <p className="text-sm font-medium text-shell-950">{record.title}</p>
                ) : null}
                <p className="mt-1 text-sm text-shell-800">{record.content}</p>
                <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-shell-700">
                  <span className="rounded-chip bg-shell-100 px-2 py-0.5 font-medium text-shell-800">
                    {formatMemoryType(record.memory_type)}
                  </span>
                  <time dateTime={record.updated_at}>
                    {new Date(record.updated_at).toLocaleString()}
                  </time>
                </div>
              </div>
              <button
                type="button"
                aria-label={`Delete memory ${record.title ?? record.content.slice(0, 40)}`}
                disabled={deletingId === record.id}
                className="inline-flex min-h-11 w-full shrink-0 items-center justify-center rounded-lg border border-shell-800/20 px-3 text-sm font-medium text-shell-900 transition hover:bg-shell-900/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:opacity-60 sm:w-auto"
                onClick={() => onDelete(record)}
              >
                {deletingId === record.id ? 'Deleting…' : 'Delete'}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

export function MemorySettingsPage() {
  const { memoryEnabled } = useChatStreamingEnabled()

  return (
    <div className="min-h-dvh bg-linear-to-b from-shell-50 via-shell-100 to-[#ebeff6]">
      <header className="sticky top-0 z-20 border-b border-shell-800/15 bg-shell-50/90 px-3 py-2 backdrop-blur sm:px-4">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <AppNav current="memory" />
            <h1 className="min-w-0 truncate text-sm font-semibold tracking-wide text-shell-900 sm:text-base">
              Memory
            </h1>
          </div>
          <div className="shrink-0">
            <AuthControls />
          </div>
        </div>
      </header>

      {memoryEnabled ? <MemorySettingsContent /> : <MemoryUnavailableNotice />}
    </div>
  )
}
