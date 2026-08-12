/* @vitest-environment jsdom */

import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useChatStream } from './useChatStream'
import * as chatClient from '../api/chatClient'

function createSseResponse(body: string): Response {
  const encoder = new TextEncoder()
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(body))
        controller.close()
      },
    }),
    {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    },
  )
}

describe('useChatStream', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('invokes tool lifecycle callbacks for tool_start and tool_end frames', async () => {
    const onToolStart = vi.fn()
    const onToolEnd = vi.fn()
    const onStart = vi.fn()
    const onEnd = vi.fn()

    vi.spyOn(chatClient, 'streamChat').mockResolvedValue(
      createSseResponse(
        [
          'event: tool_start',
          'data: {"type":"tool_start","id":"resp_1","tool_name":"web_search","call_id":"call-1","timestamp":"t0"}',
          '',
          '',
          'event: tool_end',
          'data: {"type":"tool_end","id":"resp_1","tool_name":"web_search","call_id":"call-1","success":true,"timestamp":"t1"}',
          '',
          '',
          'event: start',
          'data: {"type":"start","id":"resp_1","timestamp":"t2"}',
          '',
          '',
          'event: end',
          'data: {"type":"end","id":"resp_1","finish_reason":"stop","timestamp":"t3"}',
          '',
          '',
        ].join('\n'),
      ),
    )

    const { result } = renderHook(() => useChatStream({ onToolStart, onToolEnd, onStart, onEnd }))

    await result.current.start({
      messages: [{ role: 'user', content: 'Search' }],
      use_web_search: true,
    })

    await waitFor(() => {
      expect(onToolStart).toHaveBeenCalledWith(
        expect.objectContaining({ type: 'tool_start', tool_name: 'web_search' }),
      )
      expect(onToolEnd).toHaveBeenCalledWith(
        expect.objectContaining({ type: 'tool_end', success: true }),
      )
      expect(onStart).toHaveBeenCalled()
      expect(onEnd).toHaveBeenCalled()
    })
  })

  it('invokes onRetrievalComplete for retrieval_complete frames', async () => {
    const onRetrievalComplete = vi.fn()
    const onStart = vi.fn()

    vi.spyOn(chatClient, 'streamChat').mockResolvedValue(
      createSseResponse(
        [
          'event: retrieval_complete',
          'data: {"type":"retrieval_complete","id":"resp_1","chunk_count":2,"timestamp":"t0"}',
          '',
          '',
          'event: start',
          'data: {"type":"start","id":"resp_1","timestamp":"t1"}',
          '',
          '',
          'event: end',
          'data: {"type":"end","id":"resp_1","finish_reason":"stop","timestamp":"t2"}',
          '',
          '',
        ].join('\n'),
      ),
    )

    const { result } = renderHook(() => useChatStream({ onRetrievalComplete, onStart }))

    await result.current.start({
      messages: [{ role: 'user', content: 'Search my docs' }],
      use_documents: true,
    })

    await waitFor(() => {
      expect(onRetrievalComplete).toHaveBeenCalledWith(
        expect.objectContaining({ type: 'retrieval_complete', chunk_count: 2 }),
      )
      expect(onStart).toHaveBeenCalled()
    })
  })

  it('invokes onApprovalRequired and stops before end when approval_required arrives', async () => {
    const onApprovalRequired = vi.fn()
    const onEnd = vi.fn()

    vi.spyOn(chatClient, 'streamChat').mockResolvedValue(
      createSseResponse(
        [
          'event: start',
          'data: {"type":"start","id":"resp_1","timestamp":"t0"}',
          '',
          '',
          'event: approval_required',
          'data: {"type":"approval_required","id":"resp_1","approval_id":"a1","approval_correlation_id":"c1","proposed_calls":[{"name":"echo","arguments":{},"call_id":"call-1"}],"timestamp":"t1"}',
          '',
          '',
        ].join('\n'),
      ),
    )

    const { result } = renderHook(() => useChatStream({ onApprovalRequired, onEnd }))

    await result.current.start({
      messages: [{ role: 'user', content: 'Notify me' }],
    })

    await waitFor(() => {
      expect(onApprovalRequired).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'approval_required',
          approval_id: 'a1',
        }),
      )
    })
    expect(onEnd).not.toHaveBeenCalled()
  })

  it('aborts the fetch when approval_required arrives on an open stream', async () => {
    const onApprovalRequired = vi.fn()
    let capturedSignal: AbortSignal | undefined

    vi.spyOn(chatClient, 'streamChat').mockImplementation((_request, signal) => {
      capturedSignal = signal
      const encoder = new TextEncoder()
      return Promise.resolve(
        new Response(
          new ReadableStream<Uint8Array>({
            start(controller) {
              controller.enqueue(
                encoder.encode(
                  [
                    'event: approval_required',
                    'data: {"type":"approval_required","id":"resp_1","approval_id":"a1","approval_correlation_id":"c1","proposed_calls":[{"name":"echo","arguments":{},"call_id":"call-1"}],"timestamp":"t1"}',
                    '',
                    '',
                  ].join('\n'),
                ),
              )
            },
          }),
          {
            status: 200,
            headers: { 'Content-Type': 'text/event-stream' },
          },
        ),
      )
    })

    const { result } = renderHook(() => useChatStream({ onApprovalRequired }))

    await result.current.start({
      messages: [{ role: 'user', content: 'Notify me' }],
    })

    await waitFor(() => {
      expect(onApprovalRequired).toHaveBeenCalled()
      expect(capturedSignal?.aborted).toBe(true)
    })
  })
})
