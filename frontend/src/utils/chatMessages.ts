import type { Message, PersistedChatMessage } from '../types/chat'

export const EMPTY_ASSISTANT_RESPONSE_MESSAGE =
  'The model returned an empty response. Please try again.'

export const PERSISTED_ASSISTANT_ERROR_MESSAGE =
  'The assistant could not generate a response. Please try again.'

/** Maps in-memory messages to the API payload, omitting failed or empty turns. */
export function toApiMessages(messages: Message[]): Pick<Message, 'role' | 'content'>[] {
  return messages
    .filter(
      (message) =>
        message.status === 'complete' &&
        message.content.trim().length > 0 &&
        (message.role === 'user' || message.role === 'assistant'),
    )
    .map(({ role, content }) => ({ role, content }))
}

export function toLocalMessage(message: PersistedChatMessage): Message {
  const base: Message = {
    id: message.id,
    role: message.role,
    content: message.content,
    status: message.status,
    createdAt: message.created_at,
  }

  if (message.status === 'error') {
    return {
      ...base,
      errorMessage:
        message.content.trim().length > 0 ? message.content : PERSISTED_ASSISTANT_ERROR_MESSAGE,
      canRetry: true,
    }
  }

  if (message.status === 'interrupted') {
    return {
      ...base,
      errorMessage: 'The stream was interrupted before completion.',
      canRetry: true,
    }
  }

  if (message.status === 'complete' && message.role === 'assistant' && !message.content.trim()) {
    return {
      ...base,
      status: 'error',
      errorMessage: EMPTY_ASSISTANT_RESPONSE_MESSAGE,
      canRetry: true,
    }
  }

  return base
}
