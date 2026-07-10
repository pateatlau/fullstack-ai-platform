import OpenAI from 'openai';

import type { ChatMessage, LLMProvider, ProviderChunk } from './base.js';

type OpenAIMessage = {
  role: ChatMessage['role'];
  content: string;
};

type OpenAIChoice = {
  message?: {
    content?: unknown;
  };
  delta?: {
    content?: string | null;
  };
  finish_reason?: string | null;
};

type OpenAICompletionResponse = {
  choices?: OpenAIChoice[];
};

type OpenAIStreamingResponse = AsyncIterable<OpenAICompletionResponse>;

type OpenAIClient = {
  chat: {
    completions: {
      create(
        request: Record<string, unknown>,
        signal?: AbortSignal,
      ): Promise<OpenAICompletionResponse | OpenAIStreamingResponse>;
    };
  };
};

function createOpenAIClient(apiKey: string): OpenAIClient {
  const client = new OpenAI({ apiKey });

  return {
    chat: {
      completions: {
        create(request, signal) {
          return client.chat.completions.create(
            request as unknown as Parameters<
              typeof client.chat.completions.create
            >[0],
            signal ? { signal } : undefined,
          ) as Promise<OpenAICompletionResponse | OpenAIStreamingResponse>;
        },
      },
    },
  };
}

function toOpenAIMessages(messages: ChatMessage[]): OpenAIMessage[] {
  return messages.map((message) => ({
    role: message.role,
    content: message.content,
  }));
}

function coerceMessageContent(content: unknown): string {
  if (typeof content === 'string') {
    return content;
  }

  if (!Array.isArray(content)) {
    return '';
  }

  return content
    .map((part) => {
      if (
        typeof part === 'object' &&
        part !== null &&
        'text' in part &&
        typeof part.text === 'string'
      ) {
        return part.text;
      }

      return '';
    })
    .join('');
}

export class OpenAIProvider implements LLMProvider {
  private readonly client: OpenAIClient;

  constructor(apiKey: string, client?: OpenAIClient) {
    this.client = client ?? createOpenAIClient(apiKey);
  }

  async completeChat(
    messages: ChatMessage[],
    model: string,
    temperature = 0.7,
  ): Promise<string> {
    const response = (await this.client.chat.completions.create({
      model,
      messages: toOpenAIMessages(messages),
      temperature,
      stream: false,
    })) as OpenAICompletionResponse;

    return coerceMessageContent(response.choices?.[0]?.message?.content);
  }

  async *streamChat(
    messages: ChatMessage[],
    model: string,
    temperature = 0.7,
    signal?: AbortSignal,
  ): AsyncIterable<ProviderChunk> {
    const stream = (await this.client.chat.completions.create(
      {
        model,
        messages: toOpenAIMessages(messages),
        temperature,
        stream: true,
      },
      signal,
    )) as OpenAIStreamingResponse;

    for await (const event of stream) {
      const choice = event.choices?.[0];

      if (!choice) {
        continue;
      }

      yield {
        content: choice.delta?.content ?? '',
        finishReason: choice.finish_reason ?? null,
      };
    }
  }
}
