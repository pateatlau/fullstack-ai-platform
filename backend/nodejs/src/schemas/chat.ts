import { z } from 'zod';

export const chatRoleSchema = z.enum(['system', 'user', 'assistant']);

export function createChatRequestSchema(maxMessageLength: number) {
  return z.object({
    messages: z
      .array(
        z.object({
          role: chatRoleSchema,
          content: z.string().trim().min(1).max(maxMessageLength),
        }),
      )
      .min(1, 'messages must not be empty'),
    model: z.string().trim().min(1).optional(),
    provider: z.enum(['openai', 'gemini']).optional(),
    temperature: z.number().min(0).max(2).optional(),
  });
}

export const chatResponseSchema = z.object({
  id: z.string().trim().min(1),
  role: z.literal('assistant'),
  content: z.string(),
  model: z.string().trim().min(1),
  provider: z.enum(['openai', 'gemini']),
  created_at: z.string().datetime(),
});

export const streamStartFrameSchema = z.object({
  type: z.literal('start'),
  id: z.string().trim().min(1),
  timestamp: z.string().datetime(),
});

export const streamDeltaFrameSchema = z.object({
  type: z.literal('delta'),
  id: z.string().trim().min(1),
  content: z.string(),
  timestamp: z.string().datetime(),
});

export const streamEndFrameSchema = z.object({
  type: z.literal('end'),
  id: z.string().trim().min(1),
  finish_reason: z.string().trim().min(1),
  timestamp: z.string().datetime(),
});

export const streamErrorFrameSchema = z.object({
  type: z.literal('error'),
  id: z.string().trim().min(1),
  code: z.enum([
    'validation_error',
    'provider_timeout',
    'provider_rate_limited',
    'provider_error',
    'internal_error',
  ]),
  message: z.string().trim().min(1),
  timestamp: z.string().datetime(),
});

export const errorEnvelopeSchema = z.object({
  error: z.object({
    code: z.enum([
      'validation_error',
      'provider_timeout',
      'provider_rate_limited',
      'provider_error',
      'internal_error',
    ]),
    message: z.string().trim().min(1),
  }),
});

export type ChatRequest = z.infer<ReturnType<typeof createChatRequestSchema>>;
export type ChatResponse = z.infer<typeof chatResponseSchema>;
export type ErrorEnvelope = z.infer<typeof errorEnvelopeSchema>;
export type StreamStartFrame = z.infer<typeof streamStartFrameSchema>;
export type StreamDeltaFrame = z.infer<typeof streamDeltaFrameSchema>;
export type StreamEndFrame = z.infer<typeof streamEndFrameSchema>;
export type StreamErrorFrame = z.infer<typeof streamErrorFrameSchema>;
export type StreamFrame =
  StreamStartFrame | StreamDeltaFrame | StreamEndFrame | StreamErrorFrame;
