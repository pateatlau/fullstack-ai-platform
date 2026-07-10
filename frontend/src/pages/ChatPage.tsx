import { useRef } from 'react'
import { ChatProvider, useChatContext } from '../context/ChatContext'
import { useChatStream } from '../hooks/useChatStream'
import { MessageList } from '../components/MessageList'
import { Composer } from '../components/Composer'
import type { Message } from '../types/chat'

function ChatPageContent() {
  const { state, dispatch } = useChatContext()
  const currentMessageIdRef = useRef<string | null>(null)

  const { start, stop, isStreaming } = useChatStream({
    onStart: (chunk) => {
      currentMessageIdRef.current = chunk.id
      dispatch({
        type: 'START_MESSAGE',
        id: chunk.id,
        createdAt: chunk.timestamp,
      })
    },
    onDelta: (chunk) => {
      dispatch({ type: 'APPEND_DELTA', id: chunk.id, content: chunk.content })
    },
    onEnd: (chunk) => {
      dispatch({ type: 'END_MESSAGE', id: chunk.id })
      currentMessageIdRef.current = null
    },
    onError: (error) => {
      const id = currentMessageIdRef.current
      if (id) {
        const message = error instanceof Error ? error.message : error.message
        dispatch({ type: 'STREAM_ERROR', id, message })
      }
      currentMessageIdRef.current = null
    },
  })

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
    void start({ messages: history })
  }

  const handleStop = () => {
    stop()
    const id = currentMessageIdRef.current
    if (id) {
      dispatch({ type: 'STOP_MESSAGE', id })
      currentMessageIdRef.current = null
    }
  }

  return (
    <div className="chat-page">
      <MessageList messages={state.messages} />
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
