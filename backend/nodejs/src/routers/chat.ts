import { Router } from 'express';

import type { AppConfig } from '../core/config.js';
import type { LLMProvider, ProviderName } from '../providers/base.js';
import { ChatService } from '../services/chatService.js';
import { initializeSse, writeSseFrame } from '../services/sse.js';

type ProviderOverrides = Partial<Record<ProviderName, LLMProvider>>;

export function createChatRouter(
  config: AppConfig,
  providerOverrides: ProviderOverrides = {},
) {
  const router = Router();
  const service = new ChatService(config, providerOverrides);

  router.post('/chat', async (request, response, next) => {
    try {
      const chatResponse = await service.completeChat(request.body);
      response.json(chatResponse);
    } catch (error) {
      next(error);
    }
  });

  router.post('/chat/stream', async (request, response, next) => {
    try {
      const stream = service.streamChat(request.body, {
        isDisconnected: () =>
          request.aborted || response.writableEnded || response.destroyed,
      });

      initializeSse(response);

      for await (const frame of stream) {
        if (response.writableEnded || response.destroyed) {
          break;
        }

        writeSseFrame(response, frame);
      }

      response.end();
    } catch (error) {
      next(error);
    }
  });

  return router;
}
