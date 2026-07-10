import request from 'supertest';
import { describe, expect, it } from 'vitest';

import { createApp } from '../src/app.js';
import { parseConfig } from '../src/core/config.js';

function createTestApp(requestBodyLimitBytes = 128) {
  return createApp(
    parseConfig({
      APP_ENV: 'test',
      LLM_PROVIDER: 'openai',
      OPENAI_API_KEY: 'test-key',
      REQUEST_BODY_LIMIT_BYTES: String(requestBodyLimitBytes),
      CORS_ALLOWED_ORIGINS: 'http://localhost:5173',
    }),
  );
}

describe('global middleware', () => {
  it('returns the shared error envelope for malformed JSON', async () => {
    const app = createTestApp();

    const response = await request(app)
      .post('/api/health')
      .set('Content-Type', 'application/json')
      .send('{"messages":[');

    expect(response.status).toBe(400);
    expect(response.body).toEqual({
      error: {
        code: 'validation_error',
        message: 'Request body must be valid JSON.',
      },
    });
  });

  it('returns the shared error envelope for oversized JSON bodies', async () => {
    const app = createTestApp(64);
    const payload = JSON.stringify({ content: 'x'.repeat(128) });

    const response = await request(app)
      .post('/api/health')
      .set('Content-Type', 'application/json')
      .send(payload);

    expect(response.status).toBe(413);
    expect(response.body).toEqual({
      error: {
        code: 'validation_error',
        message:
          'Request body exceeds the configured size limit. Reduce message size and retry.',
      },
    });
  });
});
