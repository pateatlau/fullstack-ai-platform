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
  })

  it('END_MESSAGE marks the message complete', () => {
    const started = chatReducer(initialChatState, {
      type: 'START_MESSAGE',
      id: 'resp_1',
      createdAt: 't1',
    })

    const ended = chatReducer(started, { type: 'END_MESSAGE', id: 'resp_1' })

    expect(ended.messages[0].status).toBe('complete')
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

  it('STREAM_ERROR marks the message errored and sets state.error', () => {
    const started = chatReducer(initialChatState, {
      type: 'START_MESSAGE',
      id: 'resp_1',
      createdAt: 't1',
    })

    const errored = chatReducer(started, {
      type: 'STREAM_ERROR',
      id: 'resp_1',
      message: 'Upstream provider failed',
    })

    expect(errored.messages[0].status).toBe('error')
    expect(errored.error).toBe('Upstream provider failed')
  })
})
