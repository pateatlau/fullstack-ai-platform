import type { Message } from '../types/chat'

export interface ChatState {
  messages: Message[]
  error: string | null
}

export const initialChatState: ChatState = {
  messages: [],
  error: null,
}

export type ChatAction =
  | { type: 'ADD_USER_MESSAGE'; message: Message }
  | { type: 'START_MESSAGE'; id: string; createdAt: string }
  | { type: 'APPEND_DELTA'; id: string; content: string }
  | { type: 'END_MESSAGE'; id: string }
  | { type: 'STOP_MESSAGE'; id: string }
  | { type: 'STREAM_ERROR'; id: string; message: string }

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case 'ADD_USER_MESSAGE':
      return {
        ...state,
        messages: [...state.messages, action.message],
      }

    case 'START_MESSAGE':
      return {
        ...state,
        error: null,
        messages: [
          ...state.messages,
          {
            id: action.id,
            role: 'assistant',
            content: '',
            status: 'streaming',
            createdAt: action.createdAt,
          },
        ],
      }

    case 'APPEND_DELTA':
      return {
        ...state,
        messages: state.messages.map((message) =>
          message.id === action.id
            ? { ...message, content: message.content + action.content }
            : message,
        ),
      }

    case 'END_MESSAGE':
      return {
        ...state,
        messages: state.messages.map((message) =>
          message.id === action.id ? { ...message, status: 'complete' } : message,
        ),
      }

    case 'STOP_MESSAGE':
      return {
        ...state,
        messages: state.messages.map((message) =>
          message.id === action.id ? { ...message, status: 'stopped' } : message,
        ),
      }

    case 'STREAM_ERROR':
      return {
        ...state,
        error: action.message,
        messages: state.messages.map((message) =>
          message.id === action.id ? { ...message, status: 'error' } : message,
        ),
      }

    default:
      return state
  }
}
