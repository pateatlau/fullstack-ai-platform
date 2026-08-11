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
  voiceEnabled: boolean
  memoryEnabled: boolean
  workflowEngineEnabled: boolean
  observabilityEnabled: boolean
  pluginsEnabled: boolean
  healthLoading: boolean
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
  const [voiceEnabled, setVoiceEnabled] = useState(false)
  const [memoryEnabled, setMemoryEnabled] = useState(false)
  const [workflowEngineEnabled, setWorkflowEngineEnabled] = useState(false)
  const [observabilityEnabled, setObservabilityEnabled] = useState(false)
  const [pluginsEnabled, setPluginsEnabled] = useState(false)
  const [healthLoading, setHealthLoading] = useState(true)
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
          setVoiceEnabled(health.voice_enabled)
          setMemoryEnabled(health.memory_enabled)
          setWorkflowEngineEnabled(health.workflow_engine_enabled)
          setObservabilityEnabled(health.observability_enabled)
          setPluginsEnabled(health.plugins_enabled ?? false)
          setCapabilitiesByProvider(
            (health.capabilities?.by_provider as
              Partial<Record<ProviderName, ProviderCapabilityFlags>> | undefined) ??
              DEFAULT_CAPABILITIES,
          )
          setHealthLoading(false)
        }
      })
      .catch(() => {
        if (!cancelled) {
          // Keep defaults when health is unreachable.
          setHealthLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [])

  return {
    chatStreamingEnabled,
    toolsEnabled,
    ragEnabled,
    voiceEnabled,
    memoryEnabled,
    workflowEngineEnabled,
    observabilityEnabled,
    pluginsEnabled,
    healthLoading,
    capabilitiesByProvider,
  }
}
