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
  toolsUsed?: string[]
  retrievedChunkCount?: number
}

export interface ChatRequest {
  messages: Pick<Message, 'role' | 'content'>[]
  model?: string
  provider?: 'openai' | 'gemini' | 'groq' | 'anthropic'
  temperature?: number
  use_web_search?: boolean
  use_documents?: boolean
  // Additive persistence fields (plan Sections 2.4, 6.5). Omitted by older
  // flows; when set, continues an existing owned session and/or makes the
  // append idempotent on retry.
  session_id?: string
  client_message_id?: string
}

export type ChatChunk =
  | { type: 'start'; id: string; session_id?: string | null; timestamp: string }
  | { type: 'delta'; id: string; content: string; timestamp: string }
  | { type: 'end'; id: string; finish_reason: string; timestamp: string }
  | {
      type: 'error'
      id: string
      code: string
      message: string
      timestamp: string
    }
  | {
      type: 'tool_start'
      id: string
      tool_name: string
      call_id: string
      timestamp: string
    }
  | {
      type: 'tool_end'
      id: string
      tool_name: string
      call_id: string
      success: boolean
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

/** Lean session metadata from `GET /api/chat/sessions` (plan Section 2.2) — no messages. */
export interface ChatSessionListItem {
  id: string
  title: string | null
  last_message_at: string | null
  created_at: string
}

/** A persisted message as returned by session resume/create (backend `ChatMessageOut`). */
export interface PersistedChatMessage {
  id: string
  seq: number
  role: Role
  content: string
  provider: string | null
  model: string | null
  status: 'complete' | 'stopped' | 'error' | 'interrupted'
  finish_reason: string | null
  created_at: string
}

/** A persisted session with its ordered transcript (backend `ChatSessionOut`). */
export interface ChatSessionDetail {
  id: string
  title: string | null
  last_message_at: string | null
  messages: PersistedChatMessage[]
}
