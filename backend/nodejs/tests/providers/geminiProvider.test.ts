import { describe, expect, it, vi } from 'vitest';

import { GeminiProvider } from '../../src/providers/geminiProvider.js';

describe('GeminiProvider', () => {
  it('maps non-streaming Gemini responses into plain text', async () => {
    const generateContent = vi.fn().mockResolvedValue({
      text: 'Gemini full response',
    });

    const provider = new GeminiProvider('test-key', {
      models: {
        generateContent,
        generateContentStream: vi.fn(),
      },
    });

    const result = await provider.completeChat(
      [{ role: 'user', content: 'hello' }],
      'gemini-3.1-flash-lite',
      0.7,
    );

    expect(result).toBe('Gemini full response');
    expect(generateContent).toHaveBeenCalledWith({
      model: 'gemini-3.1-flash-lite',
      contents: 'user: hello',
      config: { temperature: 0.7 },
    });
  });

  it('maps streaming Gemini responses into normalized chunks', async () => {
    const generateContentStream = vi.fn().mockResolvedValue(
      (async function* () {
        yield { text: 'Gemini ' };
        yield { text: 'stream' };
      })(),
    );

    const provider = new GeminiProvider('test-key', {
      models: {
        generateContent: vi.fn(),
        generateContentStream,
      },
    });

    const chunks: Array<{ content: string; finishReason: string | null }> = [];

    for await (const chunk of provider.streamChat(
      [{ role: 'user', content: 'hello' }],
      'gemini-3.1-flash-lite',
      0.7,
    )) {
      chunks.push(chunk);
    }

    expect(chunks).toEqual([
      { content: 'Gemini ', finishReason: null },
      { content: 'stream', finishReason: null },
    ]);
  });
});
