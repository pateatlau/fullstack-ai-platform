import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from 'react'
import {
  ChatApiError,
  createChatSession,
  deleteChatSession,
  getChatSession,
  getLastRequestId,
  listChatSessions,
  setRetryRequestId,
} from '../api/chatClient'
import { type ProviderName } from '../constants/providerModels'
import { AppNav } from '../components/AppNav'
import { AuthControls } from '../components/AuthControls'
import {
  MenuIcon,
  PanelCollapseIcon,
  PanelExpandIcon,
  TrashIcon,
} from '../components/icons/ShellIcons'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { LoadingIndicator } from '../components/LoadingIndicator'
import { useAuthContext } from '../context/AuthContext'
import { ChatProvider, useChatContext } from '../context/ChatContext'
import { useChatStream } from '../hooks/useChatStream'
import { useChatCompletion } from '../hooks/useChatCompletion'
import { useChatStreamingEnabled } from '../hooks/useChatStreamingEnabled'
import { EmptyState } from '../components/EmptyState'
import { MessageList } from '../components/MessageList'
import { PageBanner } from '../components/PageBanner'
import { Composer } from '../components/Composer'
import type { ChatChunk, ChatRequest, ChatSessionSummary, Message } from '../types/chat'
import { toApiMessages, toLocalMessage } from '../utils/chatMessages'
import { friendlyErrorMessage } from '../utils/friendlyErrors'

const INVALID_ACCESS_TOKEN_CODE = 'invalid_access_token'
const QUOTA_EXCEEDED_CODE = 'quota_exceeded'
const SIDEBAR_COLLAPSED_STORAGE_KEY = 'chat-sidebar-collapsed'

const SESSION_ITEM_BASE =
  'cursor-pointer text-left transition-[background-color,border-color,box-shadow] duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500'
const SESSION_ITEM_HOVER = 'hover:border-brand-500/70 hover:bg-white hover:shadow-md'
const SESSION_ITEM_ACTIVE_HOVER = 'hover:border-brand-500 hover:bg-brand-50 hover:shadow-md'

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

function toChatDisplayError(code: string | undefined, message: string): string {
  return friendlyErrorMessage(code, message)
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
  const [sessionPendingDelete, setSessionPendingDelete] = useState<string | null>(null)
  const [isDeletingSession, setIsDeletingSession] = useState(false)
  const currentMessageIdRef = useRef<string | null>(null)
  const currentStreamIdRef = useRef<string | null>(null)
  const pendingRequestRef = useRef<ChatRequest | null>(null)
  const retryTargetMessageIdRef = useRef<string | null>(null)
  const messageRequestMapRef = useRef(new Map<string, ChatRequest>())
  const streamMessageMapRef = useRef(new Map<string, string>())
  const stoppedStreamIdsRef = useRef(new Set<string>())
  type ActiveChatTransport = 'streaming' | 'completion'
  const activeTransportRef = useRef<ActiveChatTransport | null>(null)
  const [streamingToolActive, setStreamingToolActive] = useState(false)
  const [streamingRetrievalActive, setStreamingRetrievalActive] = useState(false)
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
            message: toChatDisplayError(error.code, error.message),
            code: error.code,
          })
        } else {
          dispatch({
            type: 'SET_ERROR',
            message: toChatDisplayError(error.code, error.message),
          })
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
    onRetrievalComplete: () => {
      setStreamingRetrievalActive(false)
    },
    onToolStart: () => {
      setStreamingToolActive(true)
    },
    onToolEnd: () => {
      setStreamingToolActive(false)
    },
    onStart: (chunk) => {
      setStreamingRetrievalActive(false)
      const localMessageId =
        retryTargetMessageIdRef.current ?? currentMessageIdRef.current ?? chunk.id
      const prestartedAssistantMessage =
        currentMessageIdRef.current === localMessageId && !retryTargetMessageIdRef.current
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

      if (prestartedAssistantMessage) {
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
      setStreamingToolActive(false)
      setStreamingRetrievalActive(false)
    },
    onError: (error) => {
      setStreamingToolActive(false)
      setStreamingRetrievalActive(false)
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
          const displayMessage = toChatDisplayError(error.code, error.message)
          if (localMessageId) {
            dispatch({
              type: 'STREAM_ERROR',
              id: localMessageId,
              message: displayMessage,
              code: error.code,
            })
          } else {
            dispatch({ type: 'SET_ERROR', message: displayMessage })
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
              message: toChatDisplayError(error.code, error.message),
              code: error.code,
            })
          } else {
            dispatch({
              type: 'SET_ERROR',
              message: toChatDisplayError(error.code, error.message),
            })
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

    const useStreamingTransport = chatStreamingEnabled

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
      const documentRetrievalPending =
        Boolean(request.use_documents && ragEnabled) && !retryMessageId
      const webSearchPending = Boolean(request.use_web_search && toolsEnabled) && !retryMessageId
      const assistantBubblePending = documentRetrievalPending || webSearchPending
      setStreamingRetrievalActive(documentRetrievalPending)
      setStreamingToolActive(webSearchPending && !documentRetrievalPending)
      if (assistantBubblePending) {
        const messageId = crypto.randomUUID()
        currentMessageIdRef.current = messageId
        dispatch({
          type: 'START_MESSAGE',
          id: messageId,
          createdAt: new Date().toISOString(),
        })
      }
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
    streamingToolActive || activityPhase === 'web_search'
      ? ('searching_web' as const)
      : streamingRetrievalActive || activityPhase === 'document_retrieval'
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

    const history = toApiMessages([...state.messages, userMessage])
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
    setStreamingToolActive(false)
  }

  const activeSessionListItem = useMemo(
    () => state.sessions.find((session) => session.id === state.activeSessionId) ?? null,
    [state.sessions, state.activeSessionId],
  )

  const currentSession = useMemo<ChatSessionSummary>(() => {
    const title =
      isAuthenticated && activeSessionListItem
        ? (activeSessionListItem.title ?? 'New chat')
        : state.messages.length > 0
          ? 'Current session'
          : 'New chat'

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
  }, [isAuthenticated, activeSessionListItem, state.messages, state.activeSessionId])

  const sidebarSessions = useMemo<ChatSessionSummary[]>(() => [currentSession], [currentSession])

  const savedSessions = useMemo<ChatSessionSummary[]>(() => {
    if (!isAuthenticated) return []
    return state.sessions
      .filter((session) => session.id !== state.activeSessionId)
      .map((session) => ({
        id: session.id,
        title: session.title ?? 'New chat',
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

  const handleDeleteSession = (sessionId: string, event: MouseEvent) => {
    event.stopPropagation()
    if (!isAuthenticated || areSessionControlsDisabled || isDeletingSession) {
      return
    }
    setSessionPendingDelete(sessionId)
  }

  const handleCancelDelete = () => {
    setSessionPendingDelete(null)
  }

  const handleConfirmDelete = () => {
    const sessionId = sessionPendingDelete
    if (!sessionId || !isAuthenticated) {
      return
    }
    setSessionPendingDelete(null)
    if (isGenerating) {
      handleStop()
    }
    const activeSessionIdAtDelete = state.activeSessionId
    const wasActiveSession =
      activeSessionIdAtDelete !== null && sessionId === activeSessionIdAtDelete
    void (async () => {
      setIsDeletingSession(true)
      try {
        await deleteChatSession(sessionId)
        const sessions = await refreshSessions()
        if (!wasActiveSession) {
          // Deleting a saved (non-active) session: sidebar refresh only — keep
          // the current transcript untouched (Phase 2 post-delete UX).
          return
        }

        // Active session deleted: invalidate in-flight fetches, then select the
        // most recently active remaining session or start a new empty chat.
        sessionLoadSeqRef.current += 1
        const remaining = (sessions ?? []).filter((session) => session.id !== sessionId)
        if (remaining.length > 0) {
          await loadSession(remaining[0].id)
        } else {
          const created = await createChatSession()
          dispatch({ type: 'LOAD_SESSION', sessionId: created.id, messages: [] })
          await refreshSessions()
        }
      } catch {
        dispatch({
          type: 'SET_ERROR',
          message: 'Could not delete that conversation. Try again.',
        })
      } finally {
        setIsDeletingSession(false)
      }
    })()
  }

  const renderDeleteButton = (sessionId: string, sessionTitle: string) => (
    <button
      type="button"
      className="inline-flex min-h-11 min-w-11 shrink-0 cursor-pointer items-center justify-center rounded-full text-zinc-700 transition-colors hover:bg-danger-600/15 hover:text-danger-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:opacity-50"
      aria-label={`Delete ${sessionTitle}`}
      disabled={areSessionControlsDisabled || isDeletingSession}
      onClick={(event) => handleDeleteSession(sessionId, event)}
    >
      <TrashIcon className="h-4 w-4" />
    </button>
  )

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
      <ConfirmDialog
        open={sessionPendingDelete !== null}
        title="Delete conversation"
        message="Delete this conversation? This cannot be undone."
        confirmLabel="Delete"
        cancelLabel="Cancel"
        isDestructive
        onConfirm={handleConfirmDelete}
        onCancel={handleCancelDelete}
      />
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
              className="inline-flex min-h-11 cursor-pointer items-center justify-center rounded-full border border-zinc-400/60 px-4 text-xs font-semibold text-zinc-900 transition hover:bg-zinc-300/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 md:hidden"
              onClick={handleCloseMobileSidebar}
            >
              Close
            </button>
            <button
              type="button"
              className="hidden cursor-pointer rounded-full border border-zinc-400/60 px-3 py-1.5 text-xs font-semibold text-zinc-900 transition hover:bg-zinc-300/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 md:inline-flex"
              onClick={handleCloseSidebar}
            >
              Close
            </button>
          </div>

          {isAuthenticated ? (
            <button
              type="button"
              className="cursor-pointer rounded-chat bg-brand-600 px-4 py-3 text-left text-sm font-semibold text-white shadow-chat-card transition hover:bg-brand-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-200 focus-visible:ring-brand-500"
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
                      <div className="flex items-start gap-1 sm:gap-2">
                        <button
                          type="button"
                          aria-label={session.title}
                          className={[
                            'min-w-0 flex-1 rounded-chat border p-3',
                            SESSION_ITEM_BASE,
                            isActive
                              ? [
                                  'border-brand-500/40 bg-white shadow-sm',
                                  SESSION_ITEM_ACTIVE_HOVER,
                                ].join(' ')
                              : ['border-zinc-300 bg-zinc-100', SESSION_ITEM_HOVER].join(' '),
                          ].join(' ')}
                          onClick={() => handleSelectSession(session.id)}
                          aria-current={isActive ? 'page' : undefined}
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0 flex-1">
                              <p
                                className="truncate text-sm font-semibold text-zinc-950"
                                title={session.title}
                              >
                                {session.title}
                              </p>
                              <p className="mt-1 line-clamp-2 text-xs text-zinc-700">
                                {session.preview}
                              </p>
                            </div>
                            <span className="shrink-0 rounded-chip bg-zinc-200 px-2 py-1 text-[11px] font-medium text-zinc-700">
                              {session.messageCount}
                            </span>
                          </div>
                          <p className="mt-2 text-[11px] text-zinc-600">{session.updatedLabel}</p>
                        </button>
                        {isAuthenticated &&
                        state.activeSessionId &&
                        session.id === state.activeSessionId
                          ? renderDeleteButton(session.id, session.title)
                          : null}
                      </div>
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
                <LoadingIndicator variant="skeleton" label="Loading saved conversations…" />
              ) : savedSessions.length > 0 ? (
                <ul className="space-y-2" aria-label="Saved chat sessions">
                  {savedSessions.map((session) => (
                    <li key={session.id}>
                      <div className="flex items-start gap-1 sm:gap-2">
                        <button
                          type="button"
                          aria-label={session.title}
                          className={[
                            'min-w-0 flex-1 rounded-chat border border-zinc-300 bg-zinc-100 p-3',
                            SESSION_ITEM_BASE,
                            SESSION_ITEM_HOVER,
                            'disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:border-zinc-300 disabled:hover:bg-zinc-100 disabled:hover:shadow-none',
                          ].join(' ')}
                          onClick={() => handleSelectSession(session.id)}
                          disabled={!session.isSelectable}
                        >
                          <p
                            className="truncate text-sm font-semibold text-zinc-950"
                            title={session.title}
                          >
                            {session.title}
                          </p>
                          <p className="mt-1 line-clamp-2 text-xs text-zinc-700">
                            {session.preview}
                          </p>
                        </button>
                        {renderDeleteButton(session.id, session.title)}
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyState
                  title="No saved conversations yet"
                  description={
                    isAuthenticated
                      ? 'Start a new chat to build up your conversation history.'
                      : 'Sign in to keep multiple conversations and pick up where you left off.'
                  }
                  action={
                    isAuthenticated
                      ? {
                          label: 'New chat',
                          onClick: handleNewChat,
                          disabled: isCreatingSession || areSessionControlsDisabled,
                        }
                      : undefined
                  }
                />
              )}
            </section>
          </div>

          {isTranscriptLoading ? (
            <div className="rounded-chat border border-brand-500/20 bg-brand-500/10 p-3">
              <LoadingIndicator
                variant="inline"
                label="Loading conversation…"
                className="text-xs"
              />
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
                className="inline-flex min-h-11 min-w-11 shrink-0 cursor-pointer items-center justify-center rounded-lg border border-shell-800/20 text-shell-900 transition hover:bg-shell-900/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 md:hidden"
                aria-label="Open sidebar"
                onClick={() => setIsMobileSidebarOpen(true)}
              >
                <MenuIcon />
              </button>
              {isSidebarCollapsed ? (
                <button
                  type="button"
                  className="hidden cursor-pointer items-center justify-center gap-2 rounded-lg border border-shell-800/20 px-2 py-2 text-sm font-medium text-shell-900 transition hover:bg-shell-900/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 md:inline-flex lg:px-3"
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
            <LoadingIndicator
              variant="inline"
              label="Loading conversation…"
              className="px-2 py-3"
            />
          ) : null}
          <MessageList
            messages={state.messages}
            onRetryMessage={handleRetry}
            isStreaming={isGenerating}
            showStreamingStatus={chatStreamingEnabled}
            waitingVariant={assistantWaitingVariant}
            isAuthenticated={isAuthenticated}
            toolsEnabled={toolsEnabled}
            ragEnabled={ragEnabled}
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
