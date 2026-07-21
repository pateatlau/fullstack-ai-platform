import { useEffect, useState } from 'react'
import { fetchHealth } from '../api/healthClient'
import type { ProviderName } from '../constants/providerModels'

export interface ProviderCapabilityFlags {
  supports_streaming: boolean
  supports_tool_calling: boolean
}

export interface ChatHealthFlags {
  chatStreamingEnabled: boolean
  toolsEnabled: boolean
  ragEnabled: boolean
  capabilitiesByProvider: Partial<Record<ProviderName, ProviderCapabilityFlags>>
}

const DEFAULT_CAPABILITIES: Partial<Record<ProviderName, ProviderCapabilityFlags>> = {}

/**
 * Reads feature flags from ``GET /api/health`` so the UI picks the correct chat
 * transport and toggle disabled states without build-time flags.
 */
export function useChatStreamingEnabled(): ChatHealthFlags {
  const [chatStreamingEnabled, setChatStreamingEnabled] = useState(true)
  const [toolsEnabled, setToolsEnabled] = useState(false)
  const [ragEnabled, setRagEnabled] = useState(false)
  const [capabilitiesByProvider, setCapabilitiesByProvider] =
    useState<Partial<Record<ProviderName, ProviderCapabilityFlags>>>(DEFAULT_CAPABILITIES)

  useEffect(() => {
    let cancelled = false

    void fetchHealth()
      .then((health) => {
        if (!cancelled) {
          setChatStreamingEnabled(health.chat_streaming_enabled)
          setToolsEnabled(health.tools_enabled)
          setRagEnabled(health.rag_enabled)
          setCapabilitiesByProvider(
            (health.capabilities?.by_provider as
              Partial<Record<ProviderName, ProviderCapabilityFlags>> | undefined) ??
              DEFAULT_CAPABILITIES,
          )
        }
      })
      .catch(() => {
        // Keep defaults when health is unreachable.
      })

    return () => {
      cancelled = true
    }
  }, [])

  return { chatStreamingEnabled, toolsEnabled, ragEnabled, capabilitiesByProvider }
}
