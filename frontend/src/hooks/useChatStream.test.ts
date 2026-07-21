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
})
