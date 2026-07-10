import type { ErrorRequestHandler } from 'express';
import { ZodError } from 'zod';

export type AppErrorCode =
  | 'validation_error'
  | 'provider_timeout'
  | 'provider_rate_limited'
  | 'provider_error'
  | 'internal_error';

type ErrorEnvelope = {
  error: {
    code: AppErrorCode;
    message: string;
  };
};

export class AppError extends Error {
  public readonly statusCode: number;
  public readonly code: AppErrorCode;

  constructor(statusCode: number, code: AppErrorCode, message: string) {
    super(message);
    this.name = 'AppError';
    this.statusCode = statusCode;
    this.code = code;
  }
}

function toErrorEnvelope(error: AppError): ErrorEnvelope {
  return {
    error: {
      code: error.code,
      message: error.message,
    },
  };
}

function isBodyTooLargeError(error: unknown): error is { type: string } {
  return typeof error === 'object' && error !== null && 'type' in error;
}

function isJsonSyntaxError(
  error: unknown,
): error is SyntaxError & { status?: number } {
  return (
    error instanceof SyntaxError &&
    typeof (error as { status?: number }).status === 'number'
  );
}

export const errorMiddleware: ErrorRequestHandler = (
  error,
  _request,
  response,
  _next,
) => {
  if (error instanceof AppError) {
    response.status(error.statusCode).json(toErrorEnvelope(error));
    return;
  }

  if (error instanceof ZodError) {
    response
      .status(422)
      .json(
        toErrorEnvelope(
          new AppError(
            422,
            'validation_error',
            error.issues[0]?.message ?? 'Invalid request payload.',
          ),
        ),
      );
    return;
  }

  if (isBodyTooLargeError(error) && error.type === 'entity.too.large') {
    response
      .status(413)
      .json(
        toErrorEnvelope(
          new AppError(
            413,
            'validation_error',
            'Request body exceeds the configured size limit. Reduce message size and retry.',
          ),
        ),
      );
    return;
  }

  if (isJsonSyntaxError(error)) {
    response
      .status(400)
      .json(
        toErrorEnvelope(
          new AppError(
            400,
            'validation_error',
            'Request body must be valid JSON.',
          ),
        ),
      );
    return;
  }

  response
    .status(500)
    .json(
      toErrorEnvelope(
        new AppError(500, 'internal_error', 'Unexpected server error.'),
      ),
    );
};
