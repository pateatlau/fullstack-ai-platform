import type { ChatSessionListItem, Message } from '../types/chat'
import { EMPTY_ASSISTANT_RESPONSE_MESSAGE } from '../utils/chatMessages'

export interface ChatState {
  messages: Message[]
  error: string | null
  /** Set on `429 quota_exceeded` (plan Section 3.1/6.4): blocks the composer
   * and surfaces a login prompt until the caller authenticates. */
  quotaBlocked: boolean
  /** The backend session id the current transcript belongs to (plan Section
   * 2.2); `null` for an unsaved/guest-local conversation. */
  activeSessionId: string | null
  /** Owner-scoped session list for the sidebar (plan Section 2.2), lean metadata only. */
  sessions: ChatSessionListItem[]
}

export const initialChatState: ChatState = {
  messages: [],
  error: null,
  quotaBlocked: false,
  activeSessionId: null,
  sessions: [],
}

export type ChatAction =
  | { type: 'ADD_USER_MESSAGE'; message: Message }
  | { type: 'CLEAR_ERROR' }
  | { type: 'SET_ERROR'; message: string }
  | { type: 'START_MESSAGE'; id: string; createdAt: string }
  | { type: 'APPEND_DELTA'; id: string; content: string }
  | { type: 'END_MESSAGE'; id: string; toolsUsed?: string[]; retrievedChunkCount?: number }
  | { type: 'RETRY_MESSAGE'; id: string }
  | { type: 'STOP_MESSAGE'; id: string }
  | { type: 'INTERRUPT_MESSAGE'; id: string; message: string }
  | { type: 'STREAM_ERROR'; id: string; message: string; code?: string }
  | { type: 'SET_QUOTA_BLOCKED' }
  | { type: 'CLEAR_QUOTA_BLOCKED' }
  | { type: 'SET_SESSIONS'; sessions: ChatSessionListItem[] }
  | { type: 'SET_ACTIVE_SESSION'; sessionId: string | null }
  | { type: 'LOAD_SESSION'; sessionId: string | null; messages: Message[] }

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case 'ADD_USER_MESSAGE':
      return {
        ...state,
        messages: [...state.messages, action.message],
      }

    case 'CLEAR_ERROR':
      return {
        ...state,
        error: null,
      }

    case 'SET_QUOTA_BLOCKED':
      return {
        ...state,
        quotaBlocked: true,
      }

    case 'CLEAR_QUOTA_BLOCKED':
      return {
        ...state,
        quotaBlocked: false,
      }

    case 'SET_SESSIONS':
      return {
        ...state,
        sessions: action.sessions,
      }

    case 'SET_ACTIVE_SESSION':
      return {
        ...state,
        activeSessionId: action.sessionId,
      }

    case 'LOAD_SESSION':
      return {
        ...state,
        activeSessionId: action.sessionId,
        messages: action.messages,
        error: null,
      }

    case 'SET_ERROR':
      return {
        ...state,
        error: action.message,
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
            canRetry: false,
          },
        ],
      }

    case 'APPEND_DELTA':
      return {
        ...state,
        messages: state.messages.map((message) =>
          message.id === action.id
            ? {
                ...message,
                content: message.content + action.content,
                errorMessage: undefined,
                errorCode: undefined,
                canRetry: false,
              }
            : message,
        ),
      }

    case 'END_MESSAGE':
      return {
        ...state,
        messages: state.messages.map((message) => {
          if (message.id !== action.id) {
            return message
          }

          if (message.role === 'assistant' && message.content.trim().length === 0) {
            return {
              ...message,
              status: 'error',
              errorMessage: EMPTY_ASSISTANT_RESPONSE_MESSAGE,
              errorCode: 'empty_provider_response',
              canRetry: true,
            }
          }

          return {
            ...message,
            status: 'complete',
            errorMessage: undefined,
            errorCode: undefined,
            canRetry: false,
            toolsUsed: action.toolsUsed,
            retrievedChunkCount: action.retrievedChunkCount,
          }
        }),
      }

    case 'RETRY_MESSAGE':
      return {
        ...state,
        error: null,
        messages: state.messages.map((message) =>
          message.id === action.id
            ? {
                ...message,
                content: '',
                status: 'streaming',
                errorMessage: undefined,
                errorCode: undefined,
                canRetry: false,
              }
            : message,
        ),
      }

    case 'STOP_MESSAGE':
      return {
        ...state,
        messages: state.messages.map((message) =>
          message.id === action.id
            ? {
                ...message,
                status: 'stopped',
                errorMessage: undefined,
                errorCode: undefined,
                canRetry: false,
              }
            : message,
        ),
      }

    case 'INTERRUPT_MESSAGE':
      return {
        ...state,
        messages: state.messages.map((message) =>
          message.id === action.id
            ? {
                ...message,
                status: 'interrupted',
                errorMessage: action.message,
                canRetry: true,
              }
            : message,
        ),
      }

    case 'STREAM_ERROR':
      return {
        ...state,
        messages: state.messages.map((message) =>
          message.id === action.id
            ? {
                ...message,
                status: 'error',
                errorMessage: action.message,
                errorCode: action.code,
                canRetry: true,
              }
            : message,
        ),
      }

    default:
      return state
  }
}
