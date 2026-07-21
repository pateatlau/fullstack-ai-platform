import { useEffect, useState } from 'react'
import { fetchHealth } from '../api/healthClient'

export interface ChatHealthFlags {
  chatStreamingEnabled: boolean
  toolsEnabled: boolean
}

/**
 * Reads feature flags from ``GET /api/health`` so the UI picks the correct chat
 * transport and pending-state labels without build-time flags. Defaults to
 * streaming enabled and tools disabled while loading or when the probe fails.
 */
export function useChatStreamingEnabled(): ChatHealthFlags {
  const [chatStreamingEnabled, setChatStreamingEnabled] = useState(true)
  const [toolsEnabled, setToolsEnabled] = useState(false)

  useEffect(() => {
    let cancelled = false

    void fetchHealth()
      .then((health) => {
        if (!cancelled) {
          setChatStreamingEnabled(health.chat_streaming_enabled)
          setToolsEnabled(health.tools_enabled)
        }
      })
      .catch(() => {
        // Keep defaults when health is unreachable.
      })

    return () => {
      cancelled = true
    }
  }, [])

  return { chatStreamingEnabled, toolsEnabled }
}
