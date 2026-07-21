import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ChatApiError,
  createChatSession,
  getChatSession,
  getLastRequestId,
  listChatSessions,
  setRetryRequestId,
} from '../api/chatClient'
import { type ProviderName } from '../constants/providerModels'
import { AppNav } from '../components/AppNav'
import { AuthControls } from '../components/AuthControls'
import { MenuIcon, PanelCollapseIcon, PanelExpandIcon } from '../components/icons/ShellIcons'
import { useAuthContext } from '../context/AuthContext'
import { ChatProvider, useChatContext } from '../context/ChatContext'
import { useChatStream } from '../hooks/useChatStream'
import { useChatCompletion } from '../hooks/useChatCompletion'
import { useChatStreamingEnabled } from '../hooks/useChatStreamingEnabled'
import { MessageList } from '../components/MessageList'
import { PageBanner } from '../components/PageBanner'
import { Composer } from '../components/Composer'
import type {
  ChatChunk,
  ChatRequest,
  ChatSessionSummary,
  Message,
  PersistedChatMessage,
} from '../types/chat'

const INVALID_ACCESS_TOKEN_CODE = 'invalid_access_token'
const QUOTA_EXCEEDED_CODE = 'quota_exceeded'
const SIDEBAR_COLLAPSED_STORAGE_KEY = 'chat-sidebar-collapsed'

function readSidebarCollapsedPreference(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === 'true'
  } catch {
    return false
  }
}

function persistSidebarCollapsedPreference(collapsed: boolean): void {
  try {
    window.localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, collapsed ? 'true' : 'false')
  } catch {
    // Ignore storage failures (private mode, quota, etc.).
  }
}

function isChunkError(
  error: Extract<ChatChunk, { type: 'error' }> | Error,
): error is Extract<ChatChunk, { type: 'error' }> {
  return 'type' in error && error.type === 'error'
}

function toConnectionErrorMessage(error: Error): string {
  if (error.message.includes('Failed to fetch')) {
    return 'Could not reach the backend. Check the server connection and retry.'
  }
  return error.message
}

function toLocalMessage(message: PersistedChatMessage): Message {
  return {
    id: message.id,
    role: message.role,
    content: message.content,
    status: message.status,
    createdAt: message.created_at,
  }
}

function ChatPageContent() {
  const { state, dispatch } = useChatContext()
  const { status, sessionExpired, dismissSessionExpired, handleInvalidAccessToken } =
    useAuthContext()
  const [isMobileSidebarOpen, setIsMobileSidebarOpen] = useState(false)
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(readSidebarCollapsedPreference)
  const [isSessionsLoading, setIsSessionsLoading] = useState(false)
  const [isTranscriptLoading, setIsTranscriptLoading] = useState(false)
  const [isCreatingSession, setIsCreatingSession] = useState(false)
  const currentMessageIdRef = useRef<string | null>(null)
  const currentStreamIdRef = useRef<string | null>(null)
  const pendingRequestRef = useRef<ChatRequest | null>(null)
  const retryTargetMessageIdRef = useRef<string | null>(null)
  const messageRequestMapRef = useRef(new Map<string, ChatRequest>())
  const streamMessageMapRef = useRef(new Map<string, string>())
  const stoppedStreamIdsRef = useRef(new Set<string>())
  type ActiveChatTransport = 'streaming' | 'completion'
  const activeTransportRef = useRef<ActiveChatTransport | null>(null)
  // Monotonic counter guarding loadSession against out-of-order responses: a
  // superseded fetch (an older selection resolving after a newer one) must
  // never overwrite the transcript of the session the user is now viewing.
  const sessionLoadSeqRef = useRef(0)
  const isAuthenticated = status === 'authenticated'
  const { chatStreamingEnabled, toolsEnabled, ragEnabled, capabilitiesByProvider } =
    useChatStreamingEnabled()
  const activeSessionIdRef = useRef(state.activeSessionId)
  useEffect(() => {
    activeSessionIdRef.current = state.activeSessionId
  }, [state.activeSessionId])

  /** Best-effort sidebar refresh (plan Section 2.2); failures don't interrupt chat. */
  const refreshSessions = useCallback(async () => {
    try {
      const sessions = await listChatSessions()
      dispatch({ type: 'SET_SESSIONS', sessions })
      return sessions
    } catch {
      return null
    }
  }, [dispatch])

  /** Fetches a session's transcript and loads it into the reducer (plan Sections 5.3, 6.4). */
  const loadSession = useCallback(
    async (sessionId: string) => {
      const requestSeq = ++sessionLoadSeqRef.current
      setIsTranscriptLoading(true)
      try {
        const detail = await getChatSession(sessionId)
        if (sessionLoadSeqRef.current !== requestSeq) {
          // A newer selection started while this fetch was in flight; discard
          // this now-superseded response so it can't overwrite the transcript.
          return
        }
        dispatch({
          type: 'LOAD_SESSION',
          sessionId: detail.id,
          messages: detail.messages.map(toLocalMessage),
        })
      } catch (error) {
        if (sessionLoadSeqRef.current !== requestSeq) {
          return
        }
        if (error instanceof ChatApiError && error.status === 404) {
          // Foreign/unknown session: clear the active session AND the stale
          // transcript together (plan Section 6.6) — LOAD_SESSION resets both
          // in one dispatch so the previous session's messages never linger.
          dispatch({ type: 'LOAD_SESSION', sessionId: null, messages: [] })
          dispatch({ type: 'SET_ERROR', message: 'That chat session was not found.' })
          void refreshSessions()
        } else {
          dispatch({
            type: 'SET_ERROR',
            message: 'Could not load that conversation. Try again.',
          })
        }
      } finally {
        if (sessionLoadSeqRef.current === requestSeq) {
          setIsTranscriptLoading(false)
        }
      }
    },
    [dispatch, refreshSessions],
  )

  // Authenticated-only: load the session list on mount/login, and auto-resume
  // the most recently active session (surfaces a just-linked guest chat too).
  useEffect(() => {
    if (!isAuthenticated) return
    let cancelled = false

    void (async () => {
      setIsSessionsLoading(true)
      const sessions = await refreshSessions()
      if (cancelled) return
      setIsSessionsLoading(false)
      if (sessions && sessions.length > 0 && !activeSessionIdRef.current) {
        void loadSession(sessions[0].id)
      }
    })()

    return () => {
      cancelled = true
    }
  }, [isAuthenticated, refreshSessions, loadSession])

  const handleCompletionError = useCallback(
    (error: Error) => {
      if (activeTransportRef.current !== 'completion') {
        return
      }
      activeTransportRef.current = null

      const id = currentMessageIdRef.current

      if (error instanceof ChatApiError) {
        if (error.code === INVALID_ACCESS_TOKEN_CODE) {
          handleInvalidAccessToken()
        } else if (error.code === QUOTA_EXCEEDED_CODE) {
          dispatch({ type: 'SET_QUOTA_BLOCKED' })
        } else if (id) {
          dispatch({
            type: 'STREAM_ERROR',
            id,
            message: error.message,
            code: error.code,
          })
        } else {
          dispatch({ type: 'SET_ERROR', message: error.message })
        }
      } else if (id) {
        dispatch({
          type: 'INTERRUPT_MESSAGE',
          id,
          message: 'The connection dropped before the response finished. Retry to send again.',
        })
      } else {
        dispatch({ type: 'SET_ERROR', message: toConnectionErrorMessage(error) })
      }

      currentMessageIdRef.current = null
      pendingRequestRef.current = null
      retryTargetMessageIdRef.current = null
    },
    [dispatch, handleInvalidAccessToken],
  )

  const {
    start: completionStart,
    stop: completionStop,
    isPending,
    activityPhase,
  } = useChatCompletion({
    useProgress: false,
    onComplete: (response) => {
      if (activeTransportRef.current !== 'completion') {
        return
      }
      activeTransportRef.current = null

      const localMessageId = currentMessageIdRef.current ?? response.id

      if (pendingRequestRef.current) {
        messageRequestMapRef.current.set(localMessageId, pendingRequestRef.current)
      }

      if (
        isAuthenticated &&
        response.session_id &&
        response.session_id !== activeSessionIdRef.current
      ) {
        dispatch({ type: 'SET_ACTIVE_SESSION', sessionId: response.session_id })
      }

      dispatch({ type: 'APPEND_DELTA', id: localMessageId, content: response.content })
      dispatch({
        type: 'END_MESSAGE',
        id: localMessageId,
        toolsUsed: response.tools_used ?? undefined,
        retrievedChunkCount: response.retrieved_chunks?.length ?? undefined,
      })

      currentMessageIdRef.current = null
      pendingRequestRef.current = null
      retryTargetMessageIdRef.current = null

      if (isAuthenticated) {
        void refreshSessions()
      }
    },
    onError: handleCompletionError,
  })

  const { start, stop, isStreaming } = useChatStream({
    onStart: (chunk) => {
      const localMessageId = retryTargetMessageIdRef.current ?? chunk.id
      stoppedStreamIdsRef.current.delete(chunk.id)

      currentMessageIdRef.current = localMessageId
      currentStreamIdRef.current = chunk.id
      streamMessageMapRef.current.set(chunk.id, localMessageId)

      if (pendingRequestRef.current) {
        messageRequestMapRef.current.set(localMessageId, pendingRequestRef.current)
      }

      // Tag the conversation with its backend session id (plan Section 2.4) so
      // follow-up turns append to the same session instead of starting a new one.
      if (isAuthenticated && chunk.session_id && chunk.session_id !== state.activeSessionId) {
        dispatch({ type: 'SET_ACTIVE_SESSION', sessionId: chunk.session_id })
      }

      if (retryTargetMessageIdRef.current) {
        retryTargetMessageIdRef.current = null
        return
      }

      dispatch({
        type: 'START_MESSAGE',
        id: localMessageId,
        createdAt: chunk.timestamp,
      })
    },
    onDelta: (chunk) => {
      if (stoppedStreamIdsRef.current.has(chunk.id)) {
        return
      }
      const localMessageId = streamMessageMapRef.current.get(chunk.id) ?? chunk.id
      dispatch({ type: 'APPEND_DELTA', id: localMessageId, content: chunk.content })
    },
    onEnd: (chunk) => {
      if (stoppedStreamIdsRef.current.has(chunk.id)) {
        stoppedStreamIdsRef.current.delete(chunk.id)
        streamMessageMapRef.current.delete(chunk.id)
        currentMessageIdRef.current = null
        currentStreamIdRef.current = null
        pendingRequestRef.current = null
        retryTargetMessageIdRef.current = null
        return
      }

      if (activeTransportRef.current !== 'streaming') {
        return
      }

      const localMessageId = streamMessageMapRef.current.get(chunk.id) ?? chunk.id
      dispatch({ type: 'END_MESSAGE', id: localMessageId })
      streamMessageMapRef.current.delete(chunk.id)
      if (activeTransportRef.current === 'streaming') {
        activeTransportRef.current = null
      }
      currentMessageIdRef.current = null
      currentStreamIdRef.current = null
      pendingRequestRef.current = null
      retryTargetMessageIdRef.current = null

      // Best-effort: keeps sidebar ordering/title current after a turn lands.
      if (isAuthenticated) {
        void refreshSessions()
      }
    },
    onError: (error) => {
      if (activeTransportRef.current !== 'streaming') {
        return
      }

      const id = currentMessageIdRef.current
      const chunkError = isChunkError(error)

      if (chunkError) {
        if (stoppedStreamIdsRef.current.has(error.id)) {
          stoppedStreamIdsRef.current.delete(error.id)
          streamMessageMapRef.current.delete(error.id)
          currentMessageIdRef.current = null
          currentStreamIdRef.current = null
          pendingRequestRef.current = null
          retryTargetMessageIdRef.current = null
          return
        }

        if (error.code === INVALID_ACCESS_TOKEN_CODE) {
          // The session-expired banner already communicates this; avoid also
          // surfacing a stale/confusing chat error for the same event.
          handleInvalidAccessToken()
        } else {
          const localMessageId = streamMessageMapRef.current.get(error.id) ?? id
          if (localMessageId) {
            dispatch({
              type: 'STREAM_ERROR',
              id: localMessageId,
              message: error.message,
              code: error.code,
            })
          } else {
            dispatch({ type: 'SET_ERROR', message: error.message })
          }
        }
        streamMessageMapRef.current.delete(error.id)
      } else {
        if (error instanceof ChatApiError) {
          if (error.code === INVALID_ACCESS_TOKEN_CODE) {
            // The session-expired banner already communicates this; avoid also
            // surfacing a stale/confusing chat error for the same event.
            handleInvalidAccessToken()
          } else if (error.code === QUOTA_EXCEEDED_CODE) {
            // Blocks the composer + shows a login prompt instead of a generic
            // error banner or a dangling streaming message (plan Sections 3.1, 6.4).
            dispatch({ type: 'SET_QUOTA_BLOCKED' })
          } else if (id) {
            dispatch({
              type: 'STREAM_ERROR',
              id,
              message: error.message,
              code: error.code,
            })
          } else {
            dispatch({ type: 'SET_ERROR', message: error.message })
          }
        } else if (id) {
          dispatch({
            type: 'INTERRUPT_MESSAGE',
            id,
            message: 'The connection dropped before the response finished. Retry to send again.',
          })
        } else {
          dispatch({ type: 'SET_ERROR', message: toConnectionErrorMessage(error) })
        }

        if (currentStreamIdRef.current) {
          streamMessageMapRef.current.delete(currentStreamIdRef.current)
        }
      }

      activeTransportRef.current = null
      currentMessageIdRef.current = null
      currentStreamIdRef.current = null
      pendingRequestRef.current = null
      retryTargetMessageIdRef.current = null
    },
  })

  const startRequest = (request: ChatRequest, retryMessageId?: string) => {
    pendingRequestRef.current = request
    retryTargetMessageIdRef.current = retryMessageId ?? null
    dispatch({ type: 'CLEAR_ERROR' })

    const useUnifiedToggles = Boolean(request.use_web_search || request.use_documents)
    const useStreamingTransport = chatStreamingEnabled && !useUnifiedToggles

    if (retryMessageId) {
      currentMessageIdRef.current = retryMessageId
      dispatch({ type: 'RETRY_MESSAGE', id: retryMessageId })
    } else if (!useStreamingTransport) {
      const messageId = crypto.randomUUID()
      currentMessageIdRef.current = messageId
      dispatch({
        type: 'START_MESSAGE',
        id: messageId,
        createdAt: new Date().toISOString(),
      })
    } else {
      currentMessageIdRef.current = retryMessageId ?? null
    }

    if (useStreamingTransport) {
      activeTransportRef.current = 'streaming'
      void start(request)
    } else {
      activeTransportRef.current = 'completion'
      void completionStart(request, {
        useProgress: Boolean(
          (request.use_web_search && toolsEnabled) || (request.use_documents && ragEnabled),
        ),
      })
    }
  }

  const isGenerating = isStreaming || isPending
  const assistantWaitingVariant =
    activityPhase === 'web_search'
      ? ('searching_web' as const)
      : activityPhase === 'document_retrieval'
        ? ('searching_documents' as const)
        : ('typing' as const)

  const handleSend = (
    content: string,
    provider?: ProviderName,
    model?: string,
    options?: { useWebSearch?: boolean; useDocuments?: boolean },
  ) => {
    const userMessage: Message = {
      id: crypto.randomUUID(),
      role: 'user',
      content,
      status: 'complete',
      createdAt: new Date().toISOString(),
    }
    dispatch({ type: 'ADD_USER_MESSAGE', message: userMessage })

    const history = [...state.messages, userMessage].map(({ role, content: text }) => ({
      role,
      content: text,
    }))
    // Guests omit provider/model (server applies the system default, plan
    // Section 3.2) and session_id (the backend reuses their single default
    // chat automatically). Authenticated turns continue the active session.
    startRequest({
      messages: history,
      provider,
      model,
      session_id: isAuthenticated ? (state.activeSessionId ?? undefined) : undefined,
      client_message_id: userMessage.id,
      use_web_search: options?.useWebSearch,
      use_documents: options?.useDocuments,
    })
  }

  const handleRetry = (messageId: string) => {
    const request = messageRequestMapRef.current.get(messageId)
    if (!request) {
      dispatch({
        type: 'SET_ERROR',
        message: 'The original request is no longer available for retry.',
      })
      return
    }

    setRetryRequestId(getLastRequestId())
    startRequest(request, messageId)
  }

  const handleStop = () => {
    if (activeTransportRef.current === 'streaming') {
      stop()
    } else if (activeTransportRef.current === 'completion') {
      completionStop()
    }
    activeTransportRef.current = null

    const id = currentMessageIdRef.current
    const streamId = currentStreamIdRef.current

    pendingRequestRef.current = null
    retryTargetMessageIdRef.current = null
    currentStreamIdRef.current = null
    if (streamId) {
      stoppedStreamIdsRef.current.add(streamId)
      streamMessageMapRef.current.delete(streamId)
    }

    if (id) {
      dispatch({ type: 'STOP_MESSAGE', id })
      currentMessageIdRef.current = null
    }
  }

  const activeSessionListItem = useMemo(
    () => state.sessions.find((session) => session.id === state.activeSessionId) ?? null,
    [state.sessions, state.activeSessionId],
  )

  const currentSession = useMemo<ChatSessionSummary>(() => {
    const title =
      isAuthenticated && activeSessionListItem
        ? (activeSessionListItem.title ?? 'New conversation')
        : state.messages.length > 0
          ? 'Current session'
          : 'New conversation'

    return {
      id: isAuthenticated ? (state.activeSessionId ?? 'unsaved-session') : 'current-session',
      title,
      preview:
        state.messages.length > 0
          ? 'Live conversation in progress. Select to continue this chat.'
          : 'Start a conversation to build your first session.',
      updatedLabel: state.messages.length > 0 ? 'Active now' : 'Ready to begin',
      messageCount: state.messages.length,
      isSelectable: true,
    }
  }, [isAuthenticated, activeSessionListItem, state.messages])

  const sidebarSessions = useMemo<ChatSessionSummary[]>(() => [currentSession], [currentSession])

  const savedSessions = useMemo<ChatSessionSummary[]>(() => {
    if (!isAuthenticated) return []
    return state.sessions
      .filter((session) => session.id !== state.activeSessionId)
      .map((session) => ({
        id: session.id,
        title: session.title ?? 'New conversation',
        preview: session.last_message_at ? 'Continue this conversation.' : 'No messages yet.',
        updatedLabel: session.last_message_at
          ? new Date(session.last_message_at).toLocaleString()
          : 'Not started',
        messageCount: 0,
        isSelectable: true,
      }))
  }, [isAuthenticated, state.sessions, state.activeSessionId])

  const isSavedSessionsLoading = isAuthenticated && isSessionsLoading
  // Disables session-switching controls while any session transition or an
  // active stream is in flight, so overlapping clicks can't race each other
  // or leave a stream writing into a conversation the user has since left.
  const areSessionControlsDisabled = isTranscriptLoading || isCreatingSession || isGenerating

  const handleSelectSession = (sessionId: string) => {
    setIsMobileSidebarOpen(false)
    // Guard against the 'unsaved-session' sentinel (no backend session yet),
    // re-selecting the already-active session, and overlapping transitions —
    // none of these should fetch.
    if (!isAuthenticated || sessionId === currentSession.id || areSessionControlsDisabled) {
      return
    }
    if (isGenerating) {
      handleStop()
    }
    void loadSession(sessionId)
  }

  const handleNewChat = () => {
    setIsMobileSidebarOpen(false)
    if (!isAuthenticated || areSessionControlsDisabled) {
      // Guests are limited to a single default chat; the control is hidden
      // for them (plan Section 3.3) — this is a defensive no-op. Also blocks
      // re-entry while a session transition or stream is already in flight.
      return
    }
    if (isGenerating) {
      handleStop()
    }
    void (async () => {
      setIsCreatingSession(true)
      try {
        const created = await createChatSession()
        dispatch({ type: 'LOAD_SESSION', sessionId: created.id, messages: [] })
        await refreshSessions()
      } catch {
        dispatch({ type: 'SET_ERROR', message: 'Could not start a new chat. Try again.' })
      } finally {
        setIsCreatingSession(false)
      }
    })()
  }

  const handleCloseMobileSidebar = useCallback(() => {
    setIsMobileSidebarOpen(false)
  }, [])

  const handleCloseSidebar = useCallback(() => {
    setIsSidebarCollapsed(true)
    persistSidebarCollapsedPreference(true)
  }, [])

  const handleExpandSidebar = () => {
    setIsSidebarCollapsed(false)
    persistSidebarCollapsedPreference(false)
  }

  useEffect(() => {
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        handleCloseMobileSidebar()
      }
    }

    window.addEventListener('keydown', handleEscape)
    return () => window.removeEventListener('keydown', handleEscape)
  }, [handleCloseMobileSidebar])

  useEffect(() => {
    if (!isMobileSidebarOpen) return
    const originalOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = originalOverflow
    }
  }, [isMobileSidebarOpen])

  useEffect(() => {
    // Signing in raises the caller's limits (plan Section 3.3); unblock the
    // composer once the guest quota banner is no longer applicable.
    if (status === 'authenticated' && state.quotaBlocked) {
      dispatch({ type: 'CLEAR_QUOTA_BLOCKED' })
    }
  }, [status, state.quotaBlocked, dispatch])

  return (
    <div className="relative mx-auto flex h-dvh w-full max-w-375 overflow-hidden bg-linear-to-b from-shell-50 via-shell-100 to-[#ebeff6]">
      {isMobileSidebarOpen ? (
        <button
          type="button"
          className="fixed inset-0 z-30 bg-zinc-500/20 md:hidden"
          aria-label="Close sidebar overlay"
          onClick={handleCloseMobileSidebar}
        />
      ) : null}

      <nav
        aria-label="Chat sessions"
        className={[
          'fixed inset-y-0 left-0 z-40 w-[18rem] border-r border-zinc-300 bg-zinc-200 text-zinc-950 shadow-chat-shell transition-transform duration-300 md:sticky md:top-0 md:z-auto md:h-dvh md:flex-none md:translate-x-0 md:overflow-hidden',
          isMobileSidebarOpen ? 'translate-x-0' : '-translate-x-full',
          isSidebarCollapsed ? 'md:hidden' : 'md:w-[18rem]',
        ].join(' ')}
      >
        <div className="flex h-full flex-col gap-4 p-3 sm:p-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold tracking-wide text-zinc-900">Sessions</h2>
            <button
              type="button"
              className="rounded-full border border-zinc-400/60 px-3 py-1.5 text-xs font-semibold text-zinc-900 transition hover:bg-zinc-300/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 md:hidden"
              onClick={handleCloseMobileSidebar}
            >
              Close
            </button>
            <button
              type="button"
              className="hidden rounded-full border border-zinc-400/60 px-3 py-1.5 text-xs font-semibold text-zinc-900 transition hover:bg-zinc-300/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 md:inline-flex"
              onClick={handleCloseSidebar}
            >
              Close
            </button>
          </div>

          {isAuthenticated ? (
            <button
              type="button"
              className="rounded-chat bg-brand-600 px-4 py-3 text-left text-sm font-semibold text-white shadow-chat-card transition hover:bg-brand-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-200 focus-visible:ring-brand-500"
              onClick={handleNewChat}
            >
              + New chat
            </button>
          ) : state.messages.length === 0 ? (
            <p className="rounded-chat border border-dashed border-zinc-300 bg-zinc-100/80 p-3 text-xs text-zinc-700">
              Guests get a single chat. Sign in above to start additional chats.
            </p>
          ) : null}

          <div className="flex-1 space-y-4 overflow-y-auto">
            <section aria-labelledby="current-session-heading" className="space-y-2">
              <div className="flex items-center justify-between px-1">
                <h3
                  id="current-session-heading"
                  className="text-xs font-semibold uppercase tracking-[0.18em] text-zinc-700"
                >
                  Current
                </h3>
                <span className="text-[11px] text-zinc-600">{sidebarSessions.length} session</span>
              </div>

              <ul className="space-y-2" aria-label="Current chat history">
                {sidebarSessions.map((session) => {
                  const isActive = session.id === currentSession.id

                  return (
                    <li key={session.id}>
                      <button
                        type="button"
                        className={[
                          'w-full rounded-chat border p-3 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
                          isActive
                            ? 'border-brand-500/40 bg-white shadow-sm'
                            : 'border-zinc-300 bg-zinc-100 hover:bg-zinc-50',
                        ].join(' ')}
                        onClick={() => handleSelectSession(session.id)}
                        aria-current={isActive ? 'page' : undefined}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-sm font-semibold text-zinc-950">
                              {session.title}
                            </p>
                            <p className="mt-1 line-clamp-2 text-xs text-zinc-700">
                              {session.preview}
                            </p>
                          </div>
                          <span className="rounded-chip bg-zinc-200 px-2 py-1 text-[11px] font-medium text-zinc-700">
                            {session.messageCount}
                          </span>
                        </div>
                        <p className="mt-2 text-[11px] text-zinc-600">{session.updatedLabel}</p>
                      </button>
                    </li>
                  )
                })}
              </ul>
            </section>

            <section aria-labelledby="saved-session-heading" className="space-y-2">
              <div className="flex items-center justify-between px-1">
                <h3
                  id="saved-session-heading"
                  className="text-xs font-semibold uppercase tracking-[0.18em] text-zinc-700"
                >
                  Saved
                </h3>
                <span className="text-[11px] text-zinc-600">
                  {isAuthenticated ? `${savedSessions.length} previous` : 'Sign in to save chats'}
                </span>
              </div>

              {isSavedSessionsLoading ? (
                <div
                  className="rounded-chat border border-zinc-300 bg-zinc-100 p-3"
                  aria-live="polite"
                >
                  <div className="h-3 w-24 animate-pulse rounded bg-zinc-300" />
                  <div className="mt-2 h-3 w-full animate-pulse rounded bg-zinc-200" />
                </div>
              ) : savedSessions.length > 0 ? (
                <ul className="space-y-2" aria-label="Saved chat sessions">
                  {savedSessions.map((session) => (
                    <li key={session.id}>
                      <button
                        type="button"
                        className="w-full rounded-chat border border-zinc-300 bg-zinc-100 p-3 text-left transition hover:bg-zinc-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                        onClick={() => handleSelectSession(session.id)}
                        disabled={!session.isSelectable}
                      >
                        <p className="truncate text-sm font-semibold text-zinc-950">
                          {session.title}
                        </p>
                        <p className="mt-1 line-clamp-2 text-xs text-zinc-700">{session.preview}</p>
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="rounded-chat border border-dashed border-zinc-300 bg-zinc-100/80 p-3">
                  <p className="text-sm font-medium text-zinc-900">No saved conversations yet</p>
                  <p className="mt-1 text-xs text-zinc-600">
                    {isAuthenticated
                      ? 'Start a new chat to build up your conversation history.'
                      : 'Sign in to keep multiple conversations and pick up where you left off.'}
                  </p>
                </div>
              )}
            </section>
          </div>

          {isTranscriptLoading ? (
            <div className="rounded-chat border border-brand-500/20 bg-brand-500/10 p-3">
              <p className="text-xs text-zinc-800">Loading conversation…</p>
            </div>
          ) : null}
        </div>
      </nav>

      <section className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
        <header className="sticky top-0 z-20 border-b border-shell-800/15 bg-shell-50/90 px-3 py-2 backdrop-blur sm:px-4">
          <div
            className={[
              'flex gap-2',
              isAuthenticated
                ? 'items-center justify-between'
                : 'flex-col sm:flex-row sm:items-center sm:justify-between',
            ].join(' ')}
          >
            <div className="flex min-w-0 items-center gap-2">
              <button
                type="button"
                className="inline-flex items-center justify-center rounded-lg border border-shell-800/20 p-2 text-shell-900 transition hover:bg-shell-900/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 md:hidden"
                aria-label="Open sidebar"
                onClick={() => setIsMobileSidebarOpen(true)}
              >
                <MenuIcon />
              </button>
              {isSidebarCollapsed ? (
                <button
                  type="button"
                  className="hidden items-center justify-center gap-2 rounded-lg border border-shell-800/20 px-2 py-2 text-sm font-medium text-shell-900 transition hover:bg-shell-900/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 md:inline-flex lg:px-3"
                  aria-label="Expand sidebar"
                  onClick={handleExpandSidebar}
                >
                  <PanelExpandIcon className="h-5 w-5 lg:hidden" />
                  <span className="hidden lg:inline">Sessions</span>
                </button>
              ) : (
                <button
                  type="button"
                  className="hidden items-center justify-center gap-2 rounded-lg border border-shell-800/20 px-2 py-2 text-sm font-medium text-shell-900 transition hover:bg-shell-900/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 md:inline-flex lg:px-3 lg:hidden"
                  aria-label="Collapse sidebar"
                  onClick={handleCloseSidebar}
                >
                  <PanelCollapseIcon className="h-5 w-5" />
                  <span className="sr-only">Collapse</span>
                </button>
              )}
              <AppNav current="chat" />
              <h1 className="min-w-0 truncate text-sm font-semibold tracking-wide text-shell-900 sm:text-base">
                AI Chat Assistant
              </h1>
            </div>
            <div className={isAuthenticated ? 'shrink-0' : 'flex items-center gap-2 sm:shrink-0'}>
              <AuthControls />
            </div>
          </div>
        </header>

        <PageBanner
          sessionExpired={sessionExpired}
          onDismissSessionExpired={dismissSessionExpired}
          quotaBlocked={state.quotaBlocked}
          error={state.error}
        />

        <main
          aria-label="Conversation"
          className="flex min-h-0 flex-1 flex-col px-2 pb-2 pt-2 sm:px-4 sm:pb-4"
        >
          {isTranscriptLoading ? (
            <div className="px-2 py-3 text-sm text-shell-700" role="status" aria-live="polite">
              Loading conversation…
            </div>
          ) : null}
          <MessageList
            messages={state.messages}
            onRetryMessage={handleRetry}
            isStreaming={isGenerating}
            showStreamingStatus={chatStreamingEnabled}
            waitingVariant={assistantWaitingVariant}
          />
          <Composer
            onSend={handleSend}
            onStop={handleStop}
            isStreaming={isGenerating}
            showStreamingStatus={chatStreamingEnabled}
            canSwitchProvider={status === 'authenticated'}
            disabled={state.quotaBlocked || isTranscriptLoading}
            isAuthenticated={isAuthenticated}
            toolsEnabled={toolsEnabled}
            ragEnabled={ragEnabled}
            capabilitiesByProvider={capabilitiesByProvider}
            streamingOnlyMode={chatStreamingEnabled}
          />
        </main>
      </section>
    </div>
  )
}

export function ChatPage() {
  return (
    <ChatProvider>
      <ChatPageContent />
    </ChatProvider>
  )
}
