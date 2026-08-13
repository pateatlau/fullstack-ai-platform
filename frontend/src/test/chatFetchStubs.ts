import { vi } from 'vitest'

/** Default health payload returned by ``GET /api/health`` in component tests. */
export function jsonHealthResponse(
  chatStreamingEnabled = true,
  toolsEnabled = false,
  ragEnabled = false,
  voiceEnabled = false,
  memoryEnabled = false,
  workflowEngineEnabled = false,
  observabilityEnabled = false,
  pluginsEnabled = false,
  hitlEnabled = false,
  hitlPendingApprovalsCount = 0,
  backgroundJobsEnabled = false,
): Response {
  return new Response(
    JSON.stringify({
      status: 'ok',
      provider: 'openai',
      version: '0.1.0',
      chat_streaming_enabled: chatStreamingEnabled,
      tools_enabled: toolsEnabled,
      rag_enabled: ragEnabled,
      voice_enabled: voiceEnabled,
      memory_enabled: memoryEnabled,
      workflow_engine_enabled: workflowEngineEnabled,
      observability_enabled: observabilityEnabled,
      plugins_enabled: pluginsEnabled,
      hitl_enabled: hitlEnabled,
      hitl_pending_approvals_count: hitlPendingApprovalsCount,
      background_jobs_enabled: backgroundJobsEnabled,
      capabilities: {
        by_provider: {
          openai: { supports_streaming: true, supports_tool_calling: true },
          gemini: { supports_streaming: true, supports_tool_calling: true },
          groq: { supports_streaming: true, supports_tool_calling: true },
          anthropic: { supports_streaming: true, supports_tool_calling: true },
        },
      },
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
  options?: {
    chatStreamingEnabled?: boolean
    toolsEnabled?: boolean
    ragEnabled?: boolean
    voiceEnabled?: boolean
    memoryEnabled?: boolean
    workflowEngineEnabled?: boolean
    observabilityEnabled?: boolean
    pluginsEnabled?: boolean
    hitlEnabled?: boolean
    hitlPendingApprovalsCount?: number
    backgroundJobsEnabled?: boolean
  },
): ReturnType<typeof vi.fn> {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    const method = init?.method ?? 'GET'

    if (url.endsWith('/api/health') && method === 'GET') {
      return jsonHealthResponse(
        options?.chatStreamingEnabled ?? true,
        options?.toolsEnabled ?? false,
        options?.ragEnabled ?? false,
        options?.voiceEnabled ?? false,
        options?.memoryEnabled ?? false,
        options?.workflowEngineEnabled ?? false,
        options?.observabilityEnabled ?? false,
        options?.pluginsEnabled ?? false,
        options?.hitlEnabled ?? false,
        options?.hitlPendingApprovalsCount ?? 0,
        options?.backgroundJobsEnabled ?? false,
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
