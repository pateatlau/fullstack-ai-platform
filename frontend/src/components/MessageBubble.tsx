import type { Message } from '../types/chat'
import { StreamingIndicator } from './StreamingIndicator'

interface MessageBubbleProps {
  message: Message
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const isWaitingForFirstToken = message.status === 'streaming' && message.content === ''

  return (
    <div
      className={`message-bubble message-bubble--${message.role} message-bubble--${message.status}`}
    >
      {isWaitingForFirstToken ? (
        <StreamingIndicator />
      ) : (
        <p className="message-bubble__content">{message.content}</p>
      )}
      {message.status === 'error' && <p className="message-bubble__error">Something went wrong.</p>}
      {message.status === 'stopped' && <p className="message-bubble__stopped">Stopped.</p>}
    </div>
  )
}
