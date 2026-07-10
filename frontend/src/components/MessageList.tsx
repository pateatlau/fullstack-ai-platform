import { useEffect, useRef } from 'react'
import type { Message } from '../types/chat'
import { MessageBubble } from './MessageBubble'

interface MessageListProps {
  messages: Message[]
}

const NEAR_BOTTOM_THRESHOLD_PX = 120

export function MessageList({ messages }: MessageListProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const isNearBottomRef = useRef(true)

  const handleScroll = () => {
    const container = containerRef.current
    if (!container) return
    const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight
    isNearBottomRef.current = distanceFromBottom < NEAR_BOTTOM_THRESHOLD_PX
  }

  useEffect(() => {
    if (isNearBottomRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [messages])

  return (
    <div className="message-list" ref={containerRef} onScroll={handleScroll}>
      {messages.length === 0 && (
        <p className="message-list__empty">Say hello to start the conversation.</p>
      )}
      {messages.map((message) => (
        <MessageBubble key={message.id} message={message} />
      ))}
      <div ref={bottomRef} />
    </div>
  )
}
