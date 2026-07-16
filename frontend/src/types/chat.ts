export type Role = 'system' | 'user' | 'assistant'

export interface Message {
  id: string
  role: Role
  content: string
  status: 'complete' | 'streaming' | 'stopped' | 'error' | 'interrupted'
  createdAt: string
  errorMessage?: string
  errorCode?: string
  canRetry?: boolean
}

export interface ChatRequest {
  messages: Pick<Message, 'role' | 'content'>[]
  model?: string
  provider?: 'openai' | 'gemini' | 'groq' | 'anthropic'
  temperature?: number
}

export type ChatChunk =
  | { type: 'start'; id: string; timestamp: string }
  | { type: 'delta'; id: string; content: string; timestamp: string }
  | { type: 'end'; id: string; finish_reason: string; timestamp: string }
  | {
      type: 'error'
      id: string
      code: string
      message: string
      timestamp: string
    }

export interface ChatSession {
  id: string // anticipates future persistence; unused server-side in MVP
  messages: Message[]
}

export interface ChatSessionSummary {
  id: string
  title: string
  preview: string
  updatedLabel: string
  messageCount: number
  isSelectable: boolean
}
