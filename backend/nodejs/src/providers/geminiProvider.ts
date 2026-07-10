import { GoogleGenAI } from '@google/genai';

import type { ChatMessage, LLMProvider, ProviderChunk } from './base.js';

type GeminiResponse = {
  text?: string | null;
  candidates?: Array<{
    content?: {
      parts?: Array<{
        text?: string | null;
      }>;
    };
  }>;
};

type GeminiStreamChunk = GeminiResponse;

type GeminiClient = {
  models: {
    generateContent(request: Record<string, unknown>): Promise<GeminiResponse>;
    generateContentStream(
      request: Record<string, unknown>,
    ): Promise<AsyncIterable<GeminiStreamChunk> | Iterable<GeminiStreamChunk>>;
  };
};

function messageToLine(message: ChatMessage): string {
  return `${message.role}: ${message.content}`;
}

function messagesToPrompt(messages: ChatMessage[]): string {
  return messages.map(messageToLine).join('\n');
}

function extractText(payload: GeminiResponse): string {
  if (typeof payload.text === 'string' && payload.text) {
    return payload.text;
  }

  const parts = payload.candidates?.[0]?.content?.parts ?? [];

  return parts
    .map((part) => (typeof part.text === 'string' ? part.text : ''))
    .join('');
}

function createGeminiClient(apiKey: string): GeminiClient {
  const client = new GoogleGenAI({ apiKey });

  return {
    models: {
      async generateContent(request) {
        return (await client.models.generateContent(
          request as unknown as Parameters<
            typeof client.models.generateContent
          >[0],
        )) as GeminiResponse;
      },
      async generateContentStream(request) {
        return (await client.models.generateContentStream(
          request as unknown as Parameters<
            typeof client.models.generateContentStream
          >[0],
        )) as AsyncIterable<GeminiStreamChunk> | Iterable<GeminiStreamChunk>;
      },
    },
  };
}

async function* toAsyncIterable<T>(
  stream: AsyncIterable<T> | Iterable<T>,
): AsyncIterable<T> {
  if (Symbol.asyncIterator in stream) {
    for await (const chunk of stream as AsyncIterable<T>) {
      yield chunk;
    }
    return;
  }

  for (const chunk of stream as Iterable<T>) {
    yield chunk;
  }
}

export class GeminiProvider implements LLMProvider {
  private readonly client: GeminiClient;

  constructor(apiKey: string, client?: GeminiClient) {
    this.client = client ?? createGeminiClient(apiKey);
  }

  async completeChat(
    messages: ChatMessage[],
    model: string,
    temperature = 0.7,
  ): Promise<string> {
    const response = await this.client.models.generateContent({
      model,
      contents: messagesToPrompt(messages),
      config: { temperature },
    });

    return extractText(response);
  }

  async *streamChat(
    messages: ChatMessage[],
    model: string,
    temperature = 0.7,
    signal?: AbortSignal,
  ): AsyncIterable<ProviderChunk> {
    const stream = await this.client.models.generateContentStream({
      model,
      contents: messagesToPrompt(messages),
      config: { temperature },
    });

    for await (const chunk of toAsyncIterable(stream)) {
      if (signal?.aborted) {
        return;
      }

      const content = extractText(chunk);

      if (content) {
        yield {
          content,
          finishReason: null,
        };
      }
    }
  }
}
