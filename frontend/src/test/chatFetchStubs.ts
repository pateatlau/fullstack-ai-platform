import { vi } from 'vitest'

/** Default health payload returned by ``GET /api/health`` in component tests. */
export function jsonHealthResponse(chatStreamingEnabled = true, toolsEnabled = false): Response {
  return new Response(
    JSON.stringify({
      status: 'ok',
      provider: 'openai',
      version: '0.1.0',
      chat_streaming_enabled: chatStreamingEnabled,
      tools_enabled: toolsEnabled,
    }),
    { status: 200, headers: { 'Content-Type': 'application/json' } },
  )
}

/**
 * Wraps a chat-focused fetch mock with the background probes ``ChatPage`` issues
 * on mount (health + authenticated session list).
 */
export function withChatPageFetchStubs(
  chatFetchMock: (input: RequestInfo | URL, init?: RequestInit) => unknown,
  options?: { chatStreamingEnabled?: boolean; toolsEnabled?: boolean },
): ReturnType<typeof vi.fn> {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    const method = init?.method ?? 'GET'

    if (url.endsWith('/api/health') && method === 'GET') {
      return jsonHealthResponse(
        options?.chatStreamingEnabled ?? true,
        options?.toolsEnabled ?? false,
      )
    }

    if (url.endsWith('/api/chat/sessions') && method === 'GET') {
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }

    return chatFetchMock(input, init)
  })
}
