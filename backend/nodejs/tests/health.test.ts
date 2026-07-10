import request from 'supertest';
import { describe, expect, it } from 'vitest';

import { createApp } from '../src/app.js';
import { parseConfig } from '../src/core/config.js';

describe('GET /api/health', () => {
  it('returns the expected contract', async () => {
    const app = createApp(
      parseConfig({
        APP_ENV: 'test',
        LLM_PROVIDER: 'openai',
        OPENAI_API_KEY: 'test-key',
      }),
    );

    const response = await request(app).get('/api/health');

    expect(response.status).toBe(200);
    expect(response.body).toEqual({
      provider: 'openai',
      status: 'ok',
      version: '0.1.0',
    });
  });
});
