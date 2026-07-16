import { useEffect, useMemo, useRef, useState } from 'react'
import { ChatApiError } from '../api/chatClient'
import { type ProviderName } from '../constants/providerModels'
import { ChatProvider, useChatContext } from '../context/ChatContext'
import { useChatStream } from '../hooks/useChatStream'
import { MessageList } from '../components/MessageList'
import { Composer } from '../components/Composer'
import type { ChatChunk, ChatRequest, ChatSessionSummary, Message } from '../types/chat'

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

function ChatPageContent() {
  const { state, dispatch } = useChatContext()
  const [isMobileSidebarOpen, setIsMobileSidebarOpen] = useState(false)
  const [isTabletSidebarCollapsed, setIsTabletSidebarCollapsed] = useState(false)
  const [selectedSessionId, setSelectedSessionId] = useState('current-session')
  const currentMessageIdRef = useRef<string | null>(null)
  const currentStreamIdRef = useRef<string | null>(null)
  const pendingRequestRef = useRef<ChatRequest | null>(null)
  const retryTargetMessageIdRef = useRef<string | null>(null)
  const messageRequestMapRef = useRef(new Map<string, ChatRequest>())
  const streamMessageMapRef = useRef(new Map<string, string>())
  const stoppedStreamIdsRef = useRef(new Set<string>())

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

      const localMessageId = streamMessageMapRef.current.get(chunk.id) ?? chunk.id
      dispatch({ type: 'END_MESSAGE', id: localMessageId })
      streamMessageMapRef.current.delete(chunk.id)
      currentMessageIdRef.current = null
      currentStreamIdRef.current = null
      pendingRequestRef.current = null
      retryTargetMessageIdRef.current = null
    },
    onError: (error) => {
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
        streamMessageMapRef.current.delete(error.id)
      } else {
        if (error instanceof ChatApiError) {
          if (id) {
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

      currentMessageIdRef.current = null
      currentStreamIdRef.current = null
      pendingRequestRef.current = null
      retryTargetMessageIdRef.current = null
    },
  })

  const startRequest = (request: ChatRequest, retryMessageId?: string) => {
    pendingRequestRef.current = request
    retryTargetMessageIdRef.current = retryMessageId ?? null
    currentMessageIdRef.current = retryMessageId ?? null
    dispatch({ type: 'CLEAR_ERROR' })

    if (retryMessageId) {
      dispatch({ type: 'RETRY_MESSAGE', id: retryMessageId })
    }

    void start(request)
  }

  const handleSend = (content: string, provider: ProviderName, model: string) => {
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
    startRequest({ messages: history, provider, model })
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

    startRequest(request, messageId)
  }

  const handleStop = () => {
    stop()
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

  const currentSession = useMemo<ChatSessionSummary>(() => {
    return {
      id: 'current-session',
      title: state.messages.length > 0 ? 'Current session' : 'New conversation',
      preview:
        state.messages.length > 0
          ? 'Live conversation in progress. Select to continue this chat.'
          : 'Start a conversation to build your first session.',
      updatedLabel: state.messages.length > 0 ? 'Active now' : 'Ready to begin',
      messageCount: state.messages.length,
      isSelectable: true,
    }
  }, [state.messages])

  const sidebarSessions = useMemo<ChatSessionSummary[]>(() => [currentSession], [currentSession])
  const savedSessions: ChatSessionSummary[] = []
  const isSavedSessionsLoading = false

  const handleSelectSession = (sessionId: string) => {
    setSelectedSessionId(sessionId)
    setIsMobileSidebarOpen(false)
  }

  const handleNewChat = () => {
    setSelectedSessionId(currentSession.id)
    setIsMobileSidebarOpen(false)
  }

  useEffect(() => {
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsMobileSidebarOpen(false)
      }
    }

    window.addEventListener('keydown', handleEscape)
    return () => window.removeEventListener('keydown', handleEscape)
  }, [])

  useEffect(() => {
    if (!isMobileSidebarOpen) return
    const originalOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = originalOverflow
    }
  }, [isMobileSidebarOpen])

  return (
    <div className="relative mx-auto flex h-dvh w-full max-w-375 overflow-hidden bg-linear-to-b from-shell-50 via-shell-100 to-[#ebeff6]">
      {isMobileSidebarOpen ? (
        <button
          type="button"
          className="fixed inset-0 z-30 bg-zinc-500/20 md:hidden"
          aria-label="Close sidebar overlay"
          onClick={() => setIsMobileSidebarOpen(false)}
        />
      ) : null}

      <nav
        aria-label="Chat sessions"
        className={[
          'fixed inset-y-0 left-0 z-40 w-[18rem] border-r border-zinc-300 bg-zinc-200 text-zinc-950 shadow-chat-shell transition-transform duration-300 md:sticky md:top-0 md:z-auto md:h-dvh md:flex-none md:translate-x-0 md:overflow-hidden',
          isMobileSidebarOpen ? 'translate-x-0' : '-translate-x-full',
          isTabletSidebarCollapsed ? 'md:hidden lg:block lg:w-[18rem]' : 'md:w-[18rem]',
        ].join(' ')}
      >
        <div className="flex h-full flex-col gap-4 p-3 sm:p-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold tracking-wide text-zinc-900">Sessions</h2>
            <button
              type="button"
              className="rounded-full border border-zinc-400/60 px-3 py-1.5 text-xs font-semibold text-zinc-900 transition hover:bg-zinc-300/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
              onClick={() => setIsMobileSidebarOpen(false)}
            >
              Close
            </button>
          </div>

          <button
            type="button"
            className="rounded-chat bg-brand-600 px-4 py-3 text-left text-sm font-semibold text-white shadow-chat-card transition hover:bg-brand-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-200 focus-visible:ring-brand-500"
            onClick={handleNewChat}
          >
            + New chat
          </button>

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
                  const isActive = session.id === selectedSessionId

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
                <span className="text-[11px] text-zinc-600">Future multi-chat</span>
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
                    This section is ready for persisted multi-chat sessions once backend support is
                    added.
                  </p>
                </div>
              )}
            </section>
          </div>

          <div className="rounded-chat border border-brand-500/20 bg-brand-500/10 p-3">
            <p className="text-xs text-zinc-800">Sidebar is ready for multi-chat session wiring.</p>
          </div>
        </div>
      </nav>

      <section className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
        <header className="sticky top-0 z-20 border-b border-shell-800/15 bg-shell-50/90 px-3 py-2 backdrop-blur sm:px-4">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <button
                type="button"
                className="rounded-lg border border-shell-800/20 px-3 py-2 text-sm font-medium text-shell-900 transition hover:bg-shell-900/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 md:hidden"
                aria-label="Open sidebar"
                onClick={() => setIsMobileSidebarOpen(true)}
              >
                Menu
              </button>
              <button
                type="button"
                className="hidden rounded-lg border border-shell-800/20 px-3 py-2 text-sm font-medium text-shell-900 transition hover:bg-shell-900/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 md:inline-flex lg:hidden"
                aria-label={isTabletSidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
                onClick={() => setIsTabletSidebarCollapsed((value) => !value)}
              >
                {isTabletSidebarCollapsed ? 'Expand' : 'Collapse'}
              </button>
              <h1 className="text-sm font-semibold tracking-wide text-shell-900 sm:text-base">
                AI Chat Assistant
              </h1>
            </div>
          </div>
        </header>

        {state.error ? (
          <div
            className="mx-3 mt-3 rounded-chat border border-danger-600/25 bg-danger-100 px-4 py-3 text-sm text-danger-600 sm:mx-4"
            role="alert"
          >
            {state.error}
          </div>
        ) : null}

        <main
          aria-label="Conversation"
          className="flex min-h-0 flex-1 flex-col px-2 pb-2 pt-2 sm:px-4 sm:pb-4"
        >
          <MessageList messages={state.messages} onRetryMessage={handleRetry} />
          <Composer onSend={handleSend} onStop={handleStop} isStreaming={isStreaming} />
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
