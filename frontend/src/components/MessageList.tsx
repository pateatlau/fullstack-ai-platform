import { useEffect, useRef, useState } from 'react'
import type { Message } from '../types/chat'
import { ArrowDownIcon } from './icons/ShellIcons'
import { MessageBubble } from './MessageBubble'

interface MessageListProps {
  messages: Message[]
  onRetryMessage?: (messageId: string) => void
  isStreaming?: boolean
  showStreamingStatus?: boolean
}

const NEAR_BOTTOM_THRESHOLD_PX = 120

export function MessageList({
  messages,
  onRetryMessage,
  isStreaming = false,
  showStreamingStatus = true,
}: MessageListProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const isNearBottomRef = useRef(true)
  const [showScrollToLatest, setShowScrollToLatest] = useState(false)

  const handleScroll = () => {
    const container = containerRef.current
    if (!container) return
    const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight
    const isNearBottom = distanceFromBottom < NEAR_BOTTOM_THRESHOLD_PX
    isNearBottomRef.current = isNearBottom
    setShowScrollToLatest(!isNearBottom)
  }

  const scrollToLatest = () => {
    isNearBottomRef.current = true
    setShowScrollToLatest(false)
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    if (isNearBottomRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [messages])

  return (
    <section
      className="relative flex-1 overflow-y-auto rounded-chat border border-shell-800/15 bg-white/75 px-3 py-4 shadow-chat-card backdrop-blur sm:px-4 sm:py-5"
      ref={containerRef}
      onScroll={handleScroll}
      aria-label="Message thread"
    >
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-4">
        {messages.length === 0 && (
          <div className="mx-auto mt-4 max-w-xl rounded-[1.25rem] border border-zinc-200 bg-zinc-50/90 px-4 py-5 text-center shadow-sm sm:mt-12 sm:rounded-[1.5rem] sm:px-6 sm:py-8">
            <p className="text-sm font-semibold text-zinc-950 sm:text-base">
              Start the conversation
            </p>
            <p className="mt-1.5 text-xs leading-5 text-zinc-600 sm:mt-2 sm:text-sm sm:leading-6">
              Ask a question, iterate on an idea, or test a prompt to see streaming responses here.
            </p>
          </div>
        )}
        {messages.map((message) => (
          <MessageBubble
            key={message.id}
            message={message}
            onRetry={onRetryMessage}
            showStreamingStatus={showStreamingStatus}
          />
        ))}
      </div>
      {showScrollToLatest && isStreaming ? (
        <div className="pointer-events-none sticky bottom-3 flex justify-center">
          <button
            type="button"
            className="pointer-events-auto inline-flex items-center gap-1.5 rounded-full border border-shell-800/15 bg-white/95 px-3 py-1.5 text-xs font-semibold text-shell-900 shadow-chat-card backdrop-blur transition hover:bg-shell-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
            onClick={scrollToLatest}
          >
            <ArrowDownIcon />
            Jump to latest
          </button>
        </div>
      ) : null}
      <div ref={bottomRef} />
    </section>
  )
}
