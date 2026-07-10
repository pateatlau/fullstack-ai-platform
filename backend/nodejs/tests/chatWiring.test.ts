import request from 'supertest';
import { describe, expect, it } from 'vitest';

import { createApp } from '../src/app.js';
import { AppError } from '../src/core/errors.js';
import { parseConfig } from '../src/core/config.js';
import { FakeProvider } from './fakes/fakeProvider.js';

describe('provider wiring', () => {
  it('can wire POST /api/chat through the provider abstraction using a fake provider', async () => {
    const config = parseConfig({
      APP_ENV: 'test',
      LLM_PROVIDER: 'openai',
      OPENAI_API_KEY: 'test-key',
      OPENAI_MODEL: 'gpt-4o-mini',
      MAX_MESSAGE_LENGTH: '4000',
    });
    const fakeProvider = new FakeProvider('Fake completion response');
    const app = createApp(config, {
      providerOverrides: {
        openai: fakeProvider,
      },
    });

    const response = await request(app)
      .post('/api/chat')
      .send({
        messages: [{ role: 'user', content: 'Hello' }],
        model: 'gpt-4o-mini',
        provider: 'openai',
      });

    expect(response.status).toBe(200);
    expect(Object.keys(response.body).sort()).toEqual([
      'content',
      'created_at',
      'id',
      'model',
      'provider',
      'role',
    ]);
    expect(response.body).toMatchObject({
      id: expect.stringMatching(/^resp_[a-f0-9]{12}$/),
      role: 'assistant',
      content: 'Fake completion response',
      model: 'gpt-4o-mini',
      provider: 'openai',
      created_at: expect.any(String),
    });
    expect(new Date(response.body.created_at).toString()).not.toBe(
      'Invalid Date',
    );
    expect(fakeProvider.calls).toEqual([
      {
        messages: [{ role: 'user', content: 'Hello' }],
        model: 'gpt-4o-mini',
        temperature: 0.7,
      },
    ]);
  });

  it('surfaces validation failures through the shared error middleware', async () => {
    const config = parseConfig({
      APP_ENV: 'test',
      LLM_PROVIDER: 'openai',
      OPENAI_API_KEY: 'test-key',
      MAX_MESSAGE_LENGTH: '4000',
    });
    const app = createApp(config, {
      providerOverrides: {
        openai: new FakeProvider('unused'),
      },
    });

    const response = await request(app)
      .post('/api/chat')
      .send({ messages: [] });

    expect(response.status).toBe(422);
    expect(response.body).toEqual({
      error: {
        code: 'validation_error',
        message: 'messages must not be empty',
      },
    });
  });

  it('surfaces provider failures through the shared error middleware', async () => {
    class ErroringProvider extends FakeProvider {
      override async completeChat(): Promise<string> {
        throw new AppError(502, 'provider_error', 'Upstream provider failed.');
      }
    }

    const config = parseConfig({
      APP_ENV: 'test',
      LLM_PROVIDER: 'openai',
      OPENAI_API_KEY: 'test-key',
      MAX_MESSAGE_LENGTH: '4000',
    });
    const app = createApp(config, {
      providerOverrides: {
        openai: new ErroringProvider('unused'),
      },
    });

    const response = await request(app)
      .post('/api/chat')
      .send({
        messages: [{ role: 'user', content: 'Hello' }],
      });

    expect(response.status).toBe(502);
    expect(response.body).toEqual({
      error: {
        code: 'provider_error',
        message: 'Upstream provider failed.',
      },
    });
  });

  it('returns provider_timeout when provider completion exceeds request timeout', async () => {
    class HangingProvider extends FakeProvider {
      override async completeChat(): Promise<string> {
        return await new Promise<string>(() => {});
      }
    }

    const config = parseConfig({
      APP_ENV: 'test',
      LLM_PROVIDER: 'openai',
      OPENAI_API_KEY: 'test-key',
      MAX_MESSAGE_LENGTH: '4000',
      REQUEST_TIMEOUT_SECONDS: '1',
    });
    const app = createApp(config, {
      providerOverrides: {
        openai: new HangingProvider('unused'),
      },
    });

    const response = await request(app)
      .post('/api/chat')
      .send({
        messages: [{ role: 'user', content: 'Hello' }],
      });

    expect(response.status).toBe(504);
    expect(response.body).toEqual({
      error: {
        code: 'provider_timeout',
        message: 'Upstream provider timed out.',
      },
    });
  });

  it('uses the config-selected Gemini provider without router changes', async () => {
    const config = parseConfig({
      APP_ENV: 'test',
      LLM_PROVIDER: 'gemini',
      GEMINI_API_KEY: 'test-key',
      GEMINI_MODEL: 'gemini-3.1-flash-lite',
      MAX_MESSAGE_LENGTH: '4000',
    });
    const geminiProvider = new FakeProvider('Gemini config response');
    const app = createApp(config, {
      providerOverrides: {
        gemini: geminiProvider,
      },
    });

    const response = await request(app)
      .post('/api/chat')
      .send({
        messages: [{ role: 'user', content: 'Hello' }],
      });

    expect(response.status).toBe(200);
    expect(Object.keys(response.body).sort()).toEqual([
      'content',
      'created_at',
      'id',
      'model',
      'provider',
      'role',
    ]);
    expect(response.body).toMatchObject({
      role: 'assistant',
      content: 'Gemini config response',
      model: 'gemini-3.1-flash-lite',
      provider: 'gemini',
    });
    expect(geminiProvider.calls).toEqual([
      {
        messages: [{ role: 'user', content: 'Hello' }],
        model: 'gemini-3.1-flash-lite',
        temperature: 0.7,
      },
    ]);
  });

  it('uses the request-level Gemini override without router changes', async () => {
    const config = parseConfig({
      APP_ENV: 'test',
      LLM_PROVIDER: 'openai',
      OPENAI_API_KEY: 'test-key',
      GEMINI_API_KEY: 'test-gemini-key',
      OPENAI_MODEL: 'gpt-4o-mini',
      GEMINI_MODEL: 'gemini-3.1-flash-lite',
      MAX_MESSAGE_LENGTH: '4000',
    });
    const geminiProvider = new FakeProvider('Gemini override response');
    const app = createApp(config, {
      providerOverrides: {
        gemini: geminiProvider,
      },
    });

    const response = await request(app)
      .post('/api/chat')
      .send({
        messages: [{ role: 'user', content: 'Hello' }],
        provider: 'gemini',
      });

    expect(response.status).toBe(200);
    expect(Object.keys(response.body).sort()).toEqual([
      'content',
      'created_at',
      'id',
      'model',
      'provider',
      'role',
    ]);
    expect(response.body).toMatchObject({
      role: 'assistant',
      content: 'Gemini override response',
      model: 'gemini-3.1-flash-lite',
      provider: 'gemini',
    });
    expect(geminiProvider.calls).toEqual([
      {
        messages: [{ role: 'user', content: 'Hello' }],
        model: 'gemini-3.1-flash-lite',
        temperature: 0.7,
      },
    ]);
  });
});
