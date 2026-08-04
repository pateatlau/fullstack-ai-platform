import { afterEach, describe, expect, it, vi } from 'vitest'

describe('healthClient', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllEnvs()
    vi.resetModules()
  })

  it('fetchHealth returns chat_streaming_enabled from the server', async () => {
    vi.stubEnv('VITE_API_BASE_URL', '')
    const { fetchHealth } = await import('./healthClient')
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          status: 'ok',
          provider: 'openai',
          version: '0.1.0',
          chat_streaming_enabled: false,
          tools_enabled: true,
          rag_enabled: true,
          voice_enabled: false,
          memory_enabled: true,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    const health = await fetchHealth()

    expect(health.chat_streaming_enabled).toBe(false)
    expect(health.tools_enabled).toBe(true)
    expect(health.rag_enabled).toBe(true)
    expect(health.voice_enabled).toBe(false)
    expect(health.memory_enabled).toBe(true)
    expect(fetchMock).toHaveBeenCalledWith('/api/health')
  })
})
