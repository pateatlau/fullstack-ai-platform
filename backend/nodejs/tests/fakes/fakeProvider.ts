import type {
  ChatMessage,
  LLMProvider,
  ProviderChunk,
} from '../../src/providers/base.js';

export class FakeProvider implements LLMProvider {
  public readonly calls: Array<{
    messages: ChatMessage[];
    model: string;
    temperature: number;
  }> = [];

  constructor(private readonly response = 'Hello from the fake provider.') {}

  async completeChat(
    messages: ChatMessage[],
    model: string,
    temperature = 0.7,
  ): Promise<string> {
    this.calls.push({ messages, model, temperature });
    return this.response;
  }

  async *streamChat(
    messages: ChatMessage[],
    model: string,
    temperature = 0.7,
    _signal?: AbortSignal,
  ): AsyncIterable<ProviderChunk> {
    this.calls.push({ messages, model, temperature });

    const words = this.response.split(' ');

    for (const [index, word] of words.entries()) {
      const isLast = index === words.length - 1;

      yield {
        content: isLast ? word : `${word} `,
        finishReason: isLast ? 'stop' : null,
      };
    }
  }
}
