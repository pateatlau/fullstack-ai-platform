import { randomUUID } from 'node:crypto';
import { clearTimeout, setTimeout } from 'node:timers';

import { AppError } from '../core/errors.js';
import type { AppConfig } from '../core/config.js';
import type { LLMProvider, ProviderName } from '../providers/base.js';
import { ProviderFactory } from '../providers/factory.js';
import {
  createChatRequestSchema,
  chatResponseSchema,
  type ChatRequest,
  type ChatResponse,
  type StreamDeltaFrame,
  type StreamEndFrame,
  type StreamErrorFrame,
  type StreamFrame,
  type StreamStartFrame,
} from '../schemas/chat.js';
import type { SseFrame } from './sse.js';

type ProviderOverrides = Partial<Record<ProviderName, LLMProvider>>;
type DisconnectCheck = () => boolean | Promise<boolean>;

type StreamOptions = {
  isDisconnected?: DisconnectCheck;
};

function createResponseId(): string {
  return `resp_${randomUUID().replace(/-/g, '').slice(0, 12)}`;
}

function normalizeProviderError(error: unknown): AppError {
  if (error instanceof AppError) {
    return error;
  }

  const errorName =
    typeof error === 'object' && error !== null && 'name' in error
      ? String(error.name).toLowerCase()
      : '';

  if (error instanceof Error && /timeout/.test(errorName)) {
    return new AppError(
      504,
      'provider_timeout',
      'Upstream provider timed out.',
    );
  }

  if (
    error instanceof Error &&
    /(ratelimit|too_many_requests|toomanyrequests|resourceexhausted)/.test(
      errorName,
    )
  ) {
    return new AppError(
      429,
      'provider_rate_limited',
      'Upstream rate limit hit, please retry shortly.',
    );
  }

  return new AppError(502, 'provider_error', 'Upstream provider failed.');
}

function createTimestamp(): string {
  return new Date().toISOString();
}

function createStartFrame(id: string): StreamStartFrame {
  return {
    type: 'start',
    id,
    timestamp: createTimestamp(),
  };
}

function createDeltaFrame(id: string, content: string): StreamDeltaFrame {
  return {
    type: 'delta',
    id,
    content,
    timestamp: createTimestamp(),
  };
}

function createEndFrame(id: string, finishReason: string): StreamEndFrame {
  return {
    type: 'end',
    id,
    finish_reason: finishReason,
    timestamp: createTimestamp(),
  };
}

function createErrorFrame(id: string, error: AppError): StreamErrorFrame {
  return {
    type: 'error',
    id,
    code: error.code,
    message: error.message,
    timestamp: createTimestamp(),
  };
}

function createTimeoutError(): Error {
  const error = new Error('Upstream provider timed out.');
  error.name = 'TimeoutError';
  return error;
}

async function withTimeout<T>(
  operation: Promise<T>,
  timeoutSeconds: number,
  onTimeout?: () => void,
): Promise<T> {
  return await new Promise<T>((resolve, reject) => {
    const timeout = setTimeout(() => {
      onTimeout?.();
      reject(createTimeoutError());
    }, timeoutSeconds * 1000);

    operation
      .then(resolve)
      .catch(reject)
      .finally(() => {
        clearTimeout(timeout);
      });
  });
}

async function isDisconnected(check?: DisconnectCheck): Promise<boolean> {
  if (!check) {
    return false;
  }

  return await check();
}

export class ChatService {
  private readonly requestSchema;
  private readonly providerFactory: ProviderFactory;

  constructor(config: AppConfig, providerOverrides: ProviderOverrides = {}) {
    this.requestSchema = createChatRequestSchema(config.maxMessageLength);
    this.providerFactory = new ProviderFactory(config, providerOverrides);
    this.config = config;
  }

  private readonly config: AppConfig;

  async completeChat(payload: unknown): Promise<ChatResponse> {
    const request = this.requestSchema.parse(payload);
    const providerName = request.provider ?? this.config.llmProvider;
    const model = this.resolveModel(providerName, request);
    const provider = this.providerFactory.getProvider(providerName);

    try {
      const content = await withTimeout(
        provider.completeChat(request.messages, model, request.temperature),
        this.config.requestTimeoutSeconds,
      );

      return chatResponseSchema.parse({
        id: createResponseId(),
        role: 'assistant',
        content,
        model,
        provider: providerName,
        created_at: new Date().toISOString(),
      });
    } catch (error) {
      throw normalizeProviderError(error);
    }
  }

  async *streamChat(
    payload: unknown,
    options: StreamOptions = {},
  ): AsyncIterable<SseFrame<StreamFrame>> {
    const request = this.requestSchema.parse(payload);
    const providerName = request.provider ?? this.config.llmProvider;
    const model = this.resolveModel(providerName, request);
    const provider = this.providerFactory.getProvider(providerName);
    const responseId = createResponseId();
    const abortController = new AbortController();
    const iterator = provider
      .streamChat(
        request.messages,
        model,
        request.temperature,
        abortController.signal,
      )
      [Symbol.asyncIterator]();

    yield {
      event: 'start',
      data: createStartFrame(responseId),
    };

    let finishReason = 'stop';

    try {
      while (true) {
        if (await isDisconnected(options.isDisconnected)) {
          abortController.abort();
          return;
        }

        const result = await withTimeout(
          iterator.next(),
          this.config.requestTimeoutSeconds,
          () => {
            abortController.abort();
          },
        );

        if (result.done) {
          break;
        }

        const chunk = result.value;

        if (chunk.content) {
          yield {
            event: 'delta',
            data: createDeltaFrame(responseId, chunk.content),
          };
        }

        if (chunk.finishReason) {
          finishReason = chunk.finishReason;
        }
      }

      yield {
        event: 'end',
        data: createEndFrame(responseId, finishReason),
      };
    } catch (error) {
      const normalizedError = normalizeProviderError(error);

      yield {
        event: 'error',
        data: createErrorFrame(responseId, normalizedError),
      };
    } finally {
      abortController.abort();
      if (typeof iterator.return === 'function') {
        await iterator.return();
      }
    }
  }

  private resolveModel(
    providerName: ProviderName,
    request: ChatRequest,
  ): string {
    if (request.model) {
      return request.model;
    }

    if (providerName === 'gemini') {
      return this.config.geminiModel;
    }

    return this.config.openaiModel;
  }
}
