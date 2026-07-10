import { describe, expect, it } from 'vitest';

import { parseConfig } from '../../src/core/config.js';
import { GeminiProvider } from '../../src/providers/geminiProvider.js';
import { OpenAIProvider } from '../../src/providers/openaiProvider.js';
import {
  ProviderFactory,
  UnsupportedProviderError,
} from '../../src/providers/factory.js';
import { FakeProvider } from '../fakes/fakeProvider.js';

describe('ProviderFactory', () => {
  it('returns an injected fake provider deterministically', () => {
    const config = parseConfig({
      APP_ENV: 'test',
      LLM_PROVIDER: 'openai',
      OPENAI_API_KEY: 'test-key',
    });
    const fakeProvider = new FakeProvider('Fake response');

    const provider = new ProviderFactory(config, {
      openai: fakeProvider,
    }).getProvider();

    expect(provider).toBe(fakeProvider);
  });

  it('returns a Gemini provider when requested explicitly', () => {
    const config = parseConfig({
      APP_ENV: 'test',
      LLM_PROVIDER: 'openai',
      OPENAI_API_KEY: 'test-key',
      GEMINI_API_KEY: 'test-gemini-key',
    });

    const provider = new ProviderFactory(config).getProvider('gemini');

    expect(provider).toBeInstanceOf(GeminiProvider);
  });

  it('returns a Gemini provider when config selects gemini', () => {
    const config = parseConfig({
      APP_ENV: 'test',
      LLM_PROVIDER: 'gemini',
      GEMINI_API_KEY: 'test-key',
    });

    const provider = new ProviderFactory(config).getProvider();

    expect(provider).toBeInstanceOf(GeminiProvider);
  });

  it('keeps the OpenAI path compatible', () => {
    const config = parseConfig({
      APP_ENV: 'test',
      LLM_PROVIDER: 'openai',
      OPENAI_API_KEY: 'test-key',
    });

    const provider = new ProviderFactory(config).getProvider();

    expect(provider).toBeInstanceOf(OpenAIProvider);
  });

  it('throws for truly unsupported providers', () => {
    const config = parseConfig({
      APP_ENV: 'test',
      LLM_PROVIDER: 'openai',
      OPENAI_API_KEY: 'test-key',
    });

    const factory = new ProviderFactory(config);

    expect(() => factory.getProvider('anthropic' as never)).toThrow(
      UnsupportedProviderError,
    );
  });
});
