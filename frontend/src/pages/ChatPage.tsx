import { useRef } from 'react'
import { ChatApiError } from '../api/chatClient'
import { ChatProvider, useChatContext } from '../context/ChatContext'
import { useChatStream } from '../hooks/useChatStream'
import { MessageList } from '../components/MessageList'
import { Composer } from '../components/Composer'
import type { ChatChunk, ChatRequest, Message } from '../types/chat'

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
  const currentMessageIdRef = useRef<string | null>(null)
  const currentStreamIdRef = useRef<string | null>(null)
  const pendingRequestRef = useRef<ChatRequest | null>(null)
  const retryTargetMessageIdRef = useRef<string | null>(null)
  const messageRequestMapRef = useRef(new Map<string, ChatRequest>())
  const streamMessageMapRef = useRef(new Map<string, string>())

  const { start, stop, isStreaming } = useChatStream({
    onStart: (chunk) => {
      const localMessageId = retryTargetMessageIdRef.current ?? chunk.id

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
      const localMessageId = streamMessageMapRef.current.get(chunk.id) ?? chunk.id
      dispatch({ type: 'APPEND_DELTA', id: localMessageId, content: chunk.content })
    },
    onEnd: (chunk) => {
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

  const handleSend = (content: string) => {
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
    startRequest({ messages: history })
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
      streamMessageMapRef.current.delete(streamId)
    }

    if (id) {
      dispatch({ type: 'STOP_MESSAGE', id })
      currentMessageIdRef.current = null
    }
  }

  return (
    <div className="chat-page">
      {state.error ? (
        <div className="chat-banner" role="alert">
          {state.error}
        </div>
      ) : null}
      <MessageList messages={state.messages} onRetryMessage={handleRetry} />
      <Composer onSend={handleSend} onStop={handleStop} isStreaming={isStreaming} />
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
