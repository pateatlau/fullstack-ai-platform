import cors from 'cors';
import express from 'express';

import { errorMiddleware } from './core/errors.js';
import type { AppConfig } from './core/config.js';
import type { LLMProvider, ProviderName } from './providers/base.js';
import { createChatRouter } from './routers/chat.js';
import { createHealthRouter } from './routers/health.js';

type AppOptions = {
  providerOverrides?: Partial<Record<ProviderName, LLMProvider>>;
};

export function createApp(config: AppConfig, options: AppOptions = {}) {
  const app = express();

  app.disable('x-powered-by');
  app.use(
    cors({
      origin: config.corsAllowedOrigins,
    }),
  );
  app.use(express.json({ limit: config.requestBodyLimitBytes }));
  app.use('/api', createHealthRouter(config));
  app.use('/api', createChatRouter(config, options.providerOverrides));
  app.use(errorMiddleware);

  return app;
}
