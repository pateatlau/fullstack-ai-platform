/* @vitest-environment jsdom */

import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useChatStreamingEnabled } from './useChatStreamingEnabled'

describe('useChatStreamingEnabled', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('reads chat and tool flags from GET /api/health', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          status: 'ok',
          provider: 'openai',
          version: '0.1.0',
          chat_streaming_enabled: false,
          tools_enabled: true,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useChatStreamingEnabled())

    await waitFor(() => {
      expect(result.current.chatStreamingEnabled).toBe(false)
      expect(result.current.toolsEnabled).toBe(true)
    })
  })
})
