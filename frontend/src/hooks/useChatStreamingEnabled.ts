import { useEffect, useState } from 'react'
import { fetchHealth } from '../api/healthClient'

/**
 * Reads ``chat_streaming_enabled`` from ``GET /api/health`` so the UI picks the
 * correct chat transport without a build-time flag. Defaults to streaming while
 * loading or when the health probe fails (matches backend default).
 */
export function useChatStreamingEnabled() {
  const [chatStreamingEnabled, setChatStreamingEnabled] = useState(true)

  useEffect(() => {
    let cancelled = false

    void fetchHealth()
      .then((health) => {
        if (!cancelled) {
          setChatStreamingEnabled(health.chat_streaming_enabled)
        }
      })
      .catch(() => {
        // Keep streaming as the default when health is unreachable.
      })

    return () => {
      cancelled = true
    }
  }, [])

  return chatStreamingEnabled
}
