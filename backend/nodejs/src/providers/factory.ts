import type { AppConfig } from '../core/config.js';

import type { LLMProvider, ProviderName } from './base.js';
import { GeminiProvider } from './geminiProvider.js';
import { OpenAIProvider } from './openaiProvider.js';

export class UnsupportedProviderError extends Error {
  constructor(providerName: string) {
    super(`Unsupported provider: ${providerName}`);
    this.name = 'UnsupportedProviderError';
  }
}

type ProviderFactoryOverrides = Partial<Record<ProviderName, LLMProvider>>;

export class ProviderFactory {
  constructor(
    private readonly config: AppConfig,
    private readonly overrides: ProviderFactoryOverrides = {},
  ) {}

  getProvider(name?: ProviderName): LLMProvider {
    const providerName = name ?? this.config.llmProvider;
    const override = this.overrides[providerName];

    if (override) {
      return override;
    }

    if (providerName === 'openai') {
      return new OpenAIProvider(this.config.openaiApiKey ?? '');
    }

    if (providerName === 'gemini') {
      return new GeminiProvider(this.config.geminiApiKey ?? '');
    }

    throw new UnsupportedProviderError(providerName);
  }
}
