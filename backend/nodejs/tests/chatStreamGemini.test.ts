import request from 'supertest';
import { describe, expect, it } from 'vitest';

import { createApp } from '../src/app.js';
import { parseConfig } from '../src/core/config.js';
import { FakeProvider } from './fakes/fakeProvider.js';

function parseSseFrames(
  payload: string,
): Array<{ event: string; data: Record<string, unknown> }> {
  return payload
    .trim()
    .split('\n\n')
    .filter(Boolean)
    .map((block) => {
      const lines = block.split('\n');
      const event =
        lines
          .find((line) => line.startsWith('event:'))
          ?.slice('event:'.length)
          .trim() ?? 'message';
      const dataLine =
        lines
          .find((line) => line.startsWith('data:'))
          ?.slice('data:'.length)
          .trim() ?? '{}';

      return {
        event,
        data: JSON.parse(dataLine) as Record<string, unknown>,
      };
    });
}

describe('Gemini provider switching', () => {
  it('preserves the streaming API behavior when config selects gemini', async () => {
    const config = parseConfig({
      APP_ENV: 'test',
      LLM_PROVIDER: 'gemini',
      GEMINI_API_KEY: 'test-key',
      GEMINI_MODEL: 'gemini-3.1-flash-lite',
      MAX_MESSAGE_LENGTH: '4000',
    });
    const app = createApp(config, {
      providerOverrides: {
        gemini: new FakeProvider('Gemini stream response'),
      },
    });

    const response = await request(app)
      .post('/api/chat/stream')
      .send({
        messages: [{ role: 'user', content: 'Hello' }],
      });

    const frames = parseSseFrames(response.text);

    expect(response.status).toBe(200);
    expect(frames.map((frame) => frame.event)).toEqual([
      'start',
      'delta',
      'delta',
      'delta',
      'end',
    ]);
    expect(Object.keys(frames[0]?.data ?? {}).sort()).toEqual([
      'id',
      'timestamp',
      'type',
    ]);
    expect(Object.keys(frames.at(-1)?.data ?? {}).sort()).toEqual([
      'finish_reason',
      'id',
      'timestamp',
      'type',
    ]);
    expect(
      frames
        .filter((frame) => frame.event === 'delta')
        .map((frame) => String(frame.data.content))
        .join(''),
    ).toBe('Gemini stream response');
  });
});
