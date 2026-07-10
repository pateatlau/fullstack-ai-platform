import { describe, expect, it, vi } from 'vitest';

import { OpenAIProvider } from '../../src/providers/openaiProvider.js';

describe('OpenAIProvider', () => {
  it('maps non-streaming OpenAI responses into plain text', async () => {
    const create = vi.fn().mockResolvedValue({
      choices: [
        {
          message: {
            content: 'Hello from OpenAI',
          },
        },
      ],
    });

    const provider = new OpenAIProvider('test-key', {
      chat: {
        completions: {
          create,
        },
      },
    });

    const result = await provider.completeChat(
      [{ role: 'user', content: 'Hello' }],
      'gpt-4o-mini',
    );

    expect(result).toBe('Hello from OpenAI');
    expect(create).toHaveBeenCalledWith({
      model: 'gpt-4o-mini',
      messages: [{ role: 'user', content: 'Hello' }],
      temperature: 0.7,
      stream: false,
    });
  });

  it('maps streaming OpenAI responses into normalized chunks', async () => {
    const create = vi.fn().mockResolvedValue(
      (async function* () {
        yield {
          choices: [{ delta: { content: 'Hello ' }, finish_reason: null }],
        };
        yield {
          choices: [{ delta: { content: 'world' }, finish_reason: 'stop' }],
        };
      })(),
    );

    const provider = new OpenAIProvider('test-key', {
      chat: {
        completions: {
          create,
        },
      },
    });

    const chunks: Array<{ content: string; finishReason: string | null }> = [];

    for await (const chunk of provider.streamChat(
      [{ role: 'user', content: 'Hello' }],
      'gpt-4o-mini',
    )) {
      chunks.push(chunk);
    }

    expect(chunks).toEqual([
      { content: 'Hello ', finishReason: null },
      { content: 'world', finishReason: 'stop' },
    ]);
  });
});
