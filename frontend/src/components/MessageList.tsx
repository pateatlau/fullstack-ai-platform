import { useEffect, useRef } from 'react'
import type { Message } from '../types/chat'
import { MessageBubble } from './MessageBubble'

interface MessageListProps {
  messages: Message[]
  onRetryMessage?: (messageId: string) => void
}

const NEAR_BOTTOM_THRESHOLD_PX = 120

export function MessageList({ messages, onRetryMessage }: MessageListProps) {
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
    <section
      className="flex-1 overflow-y-auto rounded-chat border border-shell-800/15 bg-white/75 px-3 py-4 shadow-chat-card backdrop-blur sm:px-4 sm:py-5"
      ref={containerRef}
      onScroll={handleScroll}
      aria-label="Message thread"
    >
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-4">
        {messages.length === 0 && (
          <div className="mx-auto mt-12 max-w-xl rounded-[1.5rem] border border-zinc-200 bg-zinc-50/90 px-6 py-8 text-center shadow-sm">
            <p className="text-base font-semibold text-zinc-950">Start the conversation</p>
            <p className="mt-2 text-sm leading-6 text-zinc-600">
              Ask a question, iterate on an idea, or test a prompt to see streaming responses here.
            </p>
          </div>
        )}
        {messages.map((message) => (
          <MessageBubble key={message.id} message={message} onRetry={onRetryMessage} />
        ))}
      </div>
      <div ref={bottomRef} />
    </section>
  )
}
