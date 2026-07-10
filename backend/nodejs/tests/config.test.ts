import { describe, expect, it } from 'vitest';
import { ZodError } from 'zod';

import { parseConfig } from '../src/core/config.js';

describe('parseConfig', () => {
  it('fails fast when the active OpenAI provider key is missing', () => {
    expect(() =>
      parseConfig({
        APP_ENV: 'test',
        LLM_PROVIDER: 'openai',
      }),
    ).toThrow(ZodError);
  });

  it('fails fast when the active Gemini provider key is missing', () => {
    expect(() =>
      parseConfig({
        APP_ENV: 'test',
        LLM_PROVIDER: 'gemini',
      }),
    ).toThrow(ZodError);
  });

  it('parses a valid environment into app config', () => {
    const config = parseConfig({
      APP_ENV: 'test',
      LLM_PROVIDER: 'openai',
      OPENAI_API_KEY: 'test-key',
      CORS_ALLOWED_ORIGINS: 'http://localhost:5173, http://localhost:4173',
    });

    expect(config).toMatchObject({
      appEnv: 'test',
      appVersion: '0.1.0',
      corsAllowedOrigins: ['http://localhost:5173', 'http://localhost:4173'],
      llmProvider: 'openai',
      openaiApiKey: 'test-key',
      port: 8000,
      requestBodyLimitBytes: 16384,
    });
  });
});
