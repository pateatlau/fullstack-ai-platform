import { describe, expect, it } from 'vitest'
import {
  EMPTY_ASSISTANT_RESPONSE_MESSAGE,
  PERSISTED_ASSISTANT_ERROR_MESSAGE,
  toApiMessages,
  toLocalMessage,
} from './chatMessages'
import type { Message } from '../types/chat'

describe('toApiMessages', () => {
  it('includes only completed messages with non-empty content', () => {
    const messages: Message[] = [
      {
        id: '1',
        role: 'user',
        content: 'Hello',
        status: 'complete',
        createdAt: 't0',
      },
      {
        id: '2',
        role: 'assistant',
        content: '',
        status: 'error',
        createdAt: 't1',
        errorMessage: 'Upstream provider failed.',
      },
      {
        id: '3',
        role: 'user',
        content: 'Again',
        status: 'complete',
        createdAt: 't2',
      },
    ]

    expect(toApiMessages(messages)).toEqual([
      { role: 'user', content: 'Hello' },
      { role: 'user', content: 'Again' },
    ])
  })
})

describe('toLocalMessage', () => {
  it('maps persisted error messages without content to a friendly retry prompt', () => {
    expect(
      toLocalMessage({
        id: 'm1',
        seq: 2,
        role: 'assistant',
        content: '',
        provider: 'gemini',
        model: 'gemini-3.1-flash-lite',
        status: 'error',
        finish_reason: null,
        created_at: 't0',
      }),
    ).toMatchObject({
      status: 'error',
      errorMessage: PERSISTED_ASSISTANT_ERROR_MESSAGE,
      canRetry: true,
    })
  })

  it('maps empty completed assistant messages to an inline error state', () => {
    expect(
      toLocalMessage({
        id: 'm2',
        seq: 2,
        role: 'assistant',
        content: '',
        provider: 'gemini',
        model: 'gemini-3.1-flash-lite',
        status: 'complete',
        finish_reason: 'stop',
        created_at: 't0',
      }),
    ).toMatchObject({
      status: 'error',
      errorMessage: EMPTY_ASSISTANT_RESPONSE_MESSAGE,
      canRetry: true,
    })
  })
})
