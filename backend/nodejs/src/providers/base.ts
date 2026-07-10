import type { ChatRequest } from '../schemas/chat.js';

export type ProviderName = 'openai' | 'gemini';

export type ChatMessage = ChatRequest['messages'][number];

export type ProviderChunk = {
  content: string;
  finishReason: string | null;
};

export interface LLMProvider {
  streamChat(
    messages: ChatMessage[],
    model: string,
    temperature?: number,
    signal?: AbortSignal,
  ): AsyncIterable<ProviderChunk>;

  completeChat(
    messages: ChatMessage[],
    model: string,
    temperature?: number,
  ): Promise<string>;
}
