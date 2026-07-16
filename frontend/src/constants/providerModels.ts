export type ProviderName = 'openai' | 'gemini' | 'groq' | 'anthropic'

export interface ProviderModelOption {
  provider: ProviderName
  label: string
  model: string
}

export const providerModelOptions: ProviderModelOption[] = [
  { provider: 'openai', label: 'OpenAI', model: 'gpt-4o-mini' },
  { provider: 'gemini', label: 'Gemini', model: 'gemini-3.1-flash-lite' },
  { provider: 'groq', label: 'Groq', model: 'openai/gpt-oss-20b' },
  { provider: 'anthropic', label: 'Anthropic', model: 'claude-haiku-4-5-20251001' },
]

export function getProviderOption(provider: ProviderName): ProviderModelOption {
  const option = providerModelOptions.find((entry) => entry.provider === provider)
  if (!option) {
    return providerModelOptions[0]
  }
  return option
}
