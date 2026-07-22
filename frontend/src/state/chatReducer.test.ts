import { describe, expect, it } from 'vitest'
import { chatReducer, initialChatState } from './chatReducer'
import type { Message } from '../types/chat'

const userMessage: Message = {
  id: 'user-1',
  role: 'user',
  content: 'Hi',
  status: 'complete',
  createdAt: 't0',
}

describe('chatReducer', () => {
  it('ADD_USER_MESSAGE appends the message', () => {
    const state = chatReducer(initialChatState, {
      type: 'ADD_USER_MESSAGE',
      message: userMessage,
    })

    expect(state.messages).toEqual([userMessage])
  })

  it('SET_ERROR and CLEAR_ERROR update the top-level error banner state', () => {
    const withError = chatReducer(initialChatState, {
      type: 'SET_ERROR',
      message: 'Could not reach the backend.',
    })

    expect(withError.error).toBe('Could not reach the backend.')

    const cleared = chatReducer(withError, { type: 'CLEAR_ERROR' })

    expect(cleared.error).toBeNull()
  })

  it('SET_QUOTA_BLOCKED and CLEAR_QUOTA_BLOCKED toggle the guest quota flag', () => {
    expect(initialChatState.quotaBlocked).toBe(false)

    const blocked = chatReducer(initialChatState, { type: 'SET_QUOTA_BLOCKED' })
    expect(blocked.quotaBlocked).toBe(true)

    const cleared = chatReducer(blocked, { type: 'CLEAR_QUOTA_BLOCKED' })
    expect(cleared.quotaBlocked).toBe(false)
  })

  it('START_MESSAGE appends an empty streaming assistant message', () => {
    const state = chatReducer(initialChatState, {
      type: 'START_MESSAGE',
      id: 'resp_1',
      createdAt: 't1',
    })

    expect(state.messages).toEqual([
      {
        id: 'resp_1',
        role: 'assistant',
        content: '',
        status: 'streaming',
        createdAt: 't1',
        canRetry: false,
      },
    ])
    expect(state.error).toBeNull()
  })

  it('APPEND_DELTA accumulates content on the matching message only', () => {
    const started = chatReducer(initialChatState, {
      type: 'START_MESSAGE',
      id: 'resp_1',
      createdAt: 't1',
    })

    const afterFirst = chatReducer(started, {
      type: 'APPEND_DELTA',
      id: 'resp_1',
      content: 'Fast',
    })
    const afterSecond = chatReducer(afterFirst, {
      type: 'APPEND_DELTA',
      id: 'resp_1',
      content: 'API',
    })

    expect(afterSecond.messages[0].content).toBe('FastAPI')
    expect(afterSecond.messages[0].canRetry).toBe(false)
  })

  it('END_MESSAGE marks the message complete', () => {
    const started = chatReducer(initialChatState, {
      type: 'START_MESSAGE',
      id: 'resp_1',
      createdAt: 't1',
    })

    const withContent = chatReducer(started, {
      type: 'APPEND_DELTA',
      id: 'resp_1',
      content: 'Done',
    })

    const ended = chatReducer(withContent, { type: 'END_MESSAGE', id: 'resp_1' })

    expect(ended.messages[0].status).toBe('complete')
    expect(ended.messages[0].canRetry).toBe(false)
  })

  it('RETRY_MESSAGE clears error metadata and returns the assistant message to streaming', () => {
    const interruptedState = {
      error: 'Could not reach the backend.',
      quotaBlocked: false,
      activeSessionId: null,
      sessions: [],
      messages: [
        {
          id: 'resp_1',
          role: 'assistant',
          content: 'Partial answer',
          status: 'interrupted',
          createdAt: 't1',
          errorMessage: 'The connection dropped before the response finished.',
          errorCode: 'provider_timeout',
          canRetry: true,
        } satisfies Message,
      ],
    }

    const retried = chatReducer(interruptedState, {
      type: 'RETRY_MESSAGE',
      id: 'resp_1',
    })

    expect(retried.error).toBeNull()
    expect(retried.messages[0]).toMatchObject({
      id: 'resp_1',
      content: '',
      status: 'streaming',
      canRetry: false,
      errorMessage: undefined,
      errorCode: undefined,
    })
  })

  it('STOP_MESSAGE marks the message stopped', () => {
    const started = chatReducer(initialChatState, {
      type: 'START_MESSAGE',
      id: 'resp_1',
      createdAt: 't1',
    })

    const stopped = chatReducer(started, {
      type: 'STOP_MESSAGE',
      id: 'resp_1',
    })

    expect(stopped.messages[0].status).toBe('stopped')
  })

  it('INTERRUPT_MESSAGE preserves partial content and enables retry', () => {
    const started = chatReducer(initialChatState, {
      type: 'START_MESSAGE',
      id: 'resp_1',
      createdAt: 't1',
    })

    const withPartial = chatReducer(started, {
      type: 'APPEND_DELTA',
      id: 'resp_1',
      content: 'Partial answer',
    })

    const interrupted = chatReducer(withPartial, {
      type: 'INTERRUPT_MESSAGE',
      id: 'resp_1',
      message: 'The connection dropped before the response finished.',
    })

    expect(interrupted.messages[0]).toMatchObject({
      content: 'Partial answer',
      status: 'interrupted',
      errorMessage: 'The connection dropped before the response finished.',
      canRetry: true,
    })
  })

  it('END_MESSAGE marks empty assistant responses as retryable errors', () => {
    const started = chatReducer(initialChatState, {
      type: 'START_MESSAGE',
      id: 'resp_1',
      createdAt: 't1',
    })

    const completed = chatReducer(started, {
      type: 'END_MESSAGE',
      id: 'resp_1',
    })

    expect(completed.messages[0]).toMatchObject({
      status: 'error',
      content: '',
      errorCode: 'empty_provider_response',
      canRetry: true,
    })
    expect(completed.messages[0].errorMessage).toContain('empty response')
  })

  it('STREAM_ERROR marks the message errored and stores provider metadata', () => {
    const started = chatReducer(initialChatState, {
      type: 'START_MESSAGE',
      id: 'resp_1',
      createdAt: 't1',
    })

    const errored = chatReducer(started, {
      type: 'STREAM_ERROR',
      id: 'resp_1',
      message: 'Upstream provider failed',
      code: 'provider_error',
    })

    expect(errored.messages[0]).toMatchObject({
      status: 'error',
      errorMessage: 'Upstream provider failed',
      errorCode: 'provider_error',
      canRetry: true,
    })
    expect(errored.error).toBeNull()
  })
})
