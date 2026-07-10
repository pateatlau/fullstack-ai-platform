import { config as loadDotEnv } from 'dotenv';
import { z } from 'zod';

export const APP_VERSION = '0.1.0';
type EnvSource = Record<string, string | undefined>;

const envSchema = z
  .object({
    PORT: z.coerce.number().int().positive().default(8000),
    APP_ENV: z
      .enum(['development', 'test', 'production'])
      .default('development'),
    LLM_PROVIDER: z.enum(['openai', 'gemini']).default('openai'),
    OPENAI_API_KEY: z.string().trim().min(1).optional(),
    OPENAI_MODEL: z.string().trim().min(1).default('gpt-4o-mini'),
    GEMINI_API_KEY: z.string().trim().min(1).optional(),
    GEMINI_MODEL: z.string().trim().min(1).default('gemini-3.1-flash-lite'),
    CORS_ALLOWED_ORIGINS: z.string().default('http://localhost:5173'),
    MAX_MESSAGE_LENGTH: z.coerce.number().int().positive().default(4000),
    REQUEST_TIMEOUT_SECONDS: z.coerce.number().int().positive().default(30),
    REQUEST_BODY_LIMIT_BYTES: z.coerce.number().int().positive().default(16384),
  })
  .superRefine((env, context) => {
    if (env.LLM_PROVIDER === 'openai' && !env.OPENAI_API_KEY) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'OPENAI_API_KEY is required when LLM_PROVIDER is openai.',
        path: ['OPENAI_API_KEY'],
      });
    }

    if (env.LLM_PROVIDER === 'gemini' && !env.GEMINI_API_KEY) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'GEMINI_API_KEY is required when LLM_PROVIDER is gemini.',
        path: ['GEMINI_API_KEY'],
      });
    }
  });

export type AppConfig = {
  port: number;
  appEnv: 'development' | 'test' | 'production';
  llmProvider: 'openai' | 'gemini';
  openaiApiKey?: string;
  openaiModel: string;
  geminiApiKey?: string;
  geminiModel: string;
  corsAllowedOrigins: string[];
  maxMessageLength: number;
  requestTimeoutSeconds: number;
  requestBodyLimitBytes: number;
  appVersion: string;
};

export function parseConfig(env: EnvSource = process.env): AppConfig {
  const parsedEnv = envSchema.parse(env);

  return {
    port: parsedEnv.PORT,
    appEnv: parsedEnv.APP_ENV,
    llmProvider: parsedEnv.LLM_PROVIDER,
    openaiApiKey: parsedEnv.OPENAI_API_KEY,
    openaiModel: parsedEnv.OPENAI_MODEL,
    geminiApiKey: parsedEnv.GEMINI_API_KEY,
    geminiModel: parsedEnv.GEMINI_MODEL,
    corsAllowedOrigins: parsedEnv.CORS_ALLOWED_ORIGINS.split(',')
      .map((origin) => origin.trim())
      .filter(Boolean),
    maxMessageLength: parsedEnv.MAX_MESSAGE_LENGTH,
    requestTimeoutSeconds: parsedEnv.REQUEST_TIMEOUT_SECONDS,
    requestBodyLimitBytes: parsedEnv.REQUEST_BODY_LIMIT_BYTES,
    appVersion: APP_VERSION,
  };
}

export function loadConfig(): AppConfig {
  loadDotEnv();
  return parseConfig();
}
