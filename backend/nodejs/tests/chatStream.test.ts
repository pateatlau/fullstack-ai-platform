import request from 'supertest';
import { describe, expect, it } from 'vitest';

import { createApp } from '../src/app.js';
import { AppError } from '../src/core/errors.js';
import { parseConfig } from '../src/core/config.js';
import { ChatService } from '../src/services/chatService.js';
import type { ChatMessage, ProviderChunk } from '../src/providers/base.js';
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

function createTestConfig() {
  return parseConfig({
    APP_ENV: 'test',
    LLM_PROVIDER: 'openai',
    OPENAI_API_KEY: 'test-key',
    OPENAI_MODEL: 'gpt-4o-mini',
    MAX_MESSAGE_LENGTH: '4000',
  });
}

describe('POST /api/chat/stream', () => {
  it('emits start, delta, and end frames in order', async () => {
    const app = createApp(createTestConfig(), {
      providerOverrides: {
        openai: new FakeProvider('Hello from stream'),
      },
    });

    const response = await request(app)
      .post('/api/chat/stream')
      .send({
        messages: [{ role: 'user', content: 'Hello' }],
      });

    const frames = parseSseFrames(response.text);

    expect(response.status).toBe(200);
    expect(response.headers['content-type']).toContain('text/event-stream');
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
    expect(Object.keys(frames[1]?.data ?? {}).sort()).toEqual([
      'content',
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
    ).toBe('Hello from stream');
    expect(frames.at(-1)?.data.finish_reason).toBe('stop');
  });

  it('emits an error frame when the provider fails mid-stream', async () => {
    class ErroringProvider extends FakeProvider {
      override streamChat(
        _messages: ChatMessage[],
        _model: string,
        _temperature = 0.7,
      ): AsyncIterable<ProviderChunk> {
        return {
          [Symbol.asyncIterator]() {
            return {
              async next() {
                throw new AppError(
                  502,
                  'provider_error',
                  'Upstream provider failed.',
                );
              },
            };
          },
        };
      }
    }

    const app = createApp(createTestConfig(), {
      providerOverrides: {
        openai: new ErroringProvider('unused'),
      },
    });

    const response = await request(app)
      .post('/api/chat/stream')
      .send({
        messages: [{ role: 'user', content: 'Hello' }],
      });

    const frames = parseSseFrames(response.text);

    expect(response.status).toBe(200);
    expect(frames.map((frame) => frame.event)).toEqual(['start', 'error']);
    expect(Object.keys(frames[1]?.data ?? {}).sort()).toEqual([
      'code',
      'id',
      'message',
      'timestamp',
      'type',
    ]);
    expect(frames.at(-1)?.data.code).toBe('provider_error');
    expect(frames.at(-1)?.data.message).toBe('Upstream provider failed.');
  });

  it('emits a provider_timeout error frame when streaming stalls', async () => {
    class HangingProvider extends FakeProvider {
      override streamChat(): AsyncIterable<ProviderChunk> {
        return {
          [Symbol.asyncIterator]() {
            return {
              async next() {
                return await new Promise<IteratorResult<ProviderChunk>>(() => {});
              },
            };
          },
        };
      }
    }

    const config = parseConfig({
      APP_ENV: 'test',
      LLM_PROVIDER: 'openai',
      OPENAI_API_KEY: 'test-key',
      OPENAI_MODEL: 'gpt-4o-mini',
      MAX_MESSAGE_LENGTH: '4000',
      REQUEST_TIMEOUT_SECONDS: '1',
    });

    const app = createApp(config, {
      providerOverrides: {
        openai: new HangingProvider('unused'),
      },
    });

    const response = await request(app)
      .post('/api/chat/stream')
      .send({
        messages: [{ role: 'user', content: 'Hello' }],
      });

    const frames = parseSseFrames(response.text);

    expect(response.status).toBe(200);
    expect(frames.map((frame) => frame.event)).toEqual(['start', 'error']);
    expect(frames.at(-1)?.data.code).toBe('provider_timeout');
    expect(frames.at(-1)?.data.message).toBe('Upstream provider timed out.');
  });
});

describe('ChatService.streamChat', () => {
  it('stops streaming when the client disconnects and closes the iterator', async () => {
    class RecordingProvider extends FakeProvider {
      public chunksSeen = 0;
      public closed = false;

      override async *streamChat(
        _messages: ChatMessage[],
        _model: string,
        _temperature = 0.7,
      ): AsyncIterable<ProviderChunk> {
        try {
          for (const chunk of [
            { content: 'first ', finishReason: null },
            { content: 'second', finishReason: 'stop' },
          ] satisfies ProviderChunk[]) {
            this.chunksSeen += 1;
            yield chunk;
          }
        } finally {
          this.closed = true;
        }
      }
    }

    let disconnectChecks = 0;
    const provider = new RecordingProvider('unused');
    const service = new ChatService(createTestConfig(), {
      openai: provider,
    });

    const frames: Array<{ event: string; data: Record<string, unknown> }> = [];

    for await (const frame of service.streamChat(
      { messages: [{ role: 'user', content: 'Hello' }] },
      {
        isDisconnected: () => {
          disconnectChecks += 1;
          return disconnectChecks > 1;
        },
      },
    )) {
      frames.push({
        event: frame.event,
        data: frame.data as Record<string, unknown>,
      });
    }

    expect(frames.map((frame) => frame.event)).toEqual(['start', 'delta']);
    expect(provider.chunksSeen).toBe(1);
    expect(provider.closed).toBe(true);
  });
});
