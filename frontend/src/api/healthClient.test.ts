import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchHealth } from './healthClient'

describe('healthClient', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('fetchHealth returns chat_streaming_enabled from the server', async () => {
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

    const health = await fetchHealth()

    expect(health.chat_streaming_enabled).toBe(false)
    expect(health.tools_enabled).toBe(true)
    expect(fetchMock).toHaveBeenCalledWith('http://localhost:8000/api/health')
  })
})
