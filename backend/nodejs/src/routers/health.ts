import { Router } from 'express';

import type { AppConfig } from '../core/config.js';

export function createHealthRouter(config: AppConfig) {
  const router = Router();

  router.get('/health', (_request, response) => {
    response.json({
      status: 'ok',
      provider: config.llmProvider,
      version: config.appVersion,
    });
  });

  return router;
}
