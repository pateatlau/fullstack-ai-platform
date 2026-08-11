export type PluginContributionKind = 'tool' | 'prompt' | 'workflow_node' | 'mcp_server'

export type PluginStatus = 'loaded' | 'failed'

export interface PluginLoadFailure {
  code: string
  message: string
  expected_api_versions?: string[] | null
  manifest_api_version?: string | null
}

export interface PluginInventoryItem {
  plugin_id: string | null
  name: string | null
  version: string | null
  api_version: string | null
  status: PluginStatus
  contributions: PluginContributionKind[]
  load_duration_ms: number
  author?: string | null
  homepage?: string | null
  repository?: string | null
  documentation?: string | null
  license?: string | null
  failure?: PluginLoadFailure | null
}

export interface PluginInventoryListResponse {
  plugins: PluginInventoryItem[]
}

export function displayPluginId(pluginId: string | null): string {
  return pluginId ?? 'Unknown plugin'
}

export function displayPluginName(item: PluginInventoryItem): string {
  return item.name ?? displayPluginId(item.plugin_id)
}

export function formatContributionKind(kind: PluginContributionKind): string {
  switch (kind) {
    case 'tool':
      return 'Tool'
    case 'prompt':
      return 'Prompt'
    case 'workflow_node':
      return 'Workflow node'
    case 'mcp_server':
      return 'MCP server'
  }
}

export function formatLoadDurationMs(durationMs: number): string {
  if (durationMs <= 0) {
    return '—'
  }
  if (durationMs < 1) {
    return '<1 ms'
  }
  if (durationMs < 100) {
    return `${durationMs.toFixed(1)} ms`
  }
  return `${Math.round(durationMs)} ms`
}

export function formatPluginStatus(status: PluginStatus): string {
  return status === 'loaded' ? 'Loaded' : 'Failed'
}
