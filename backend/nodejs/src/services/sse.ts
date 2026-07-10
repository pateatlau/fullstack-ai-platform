import type { Response } from 'express';

export type SseFrame<TData> = {
  event: string;
  data: TData;
};

export function formatSseFrame<TData>(frame: SseFrame<TData>): string {
  return `event: ${frame.event}\ndata: ${JSON.stringify(frame.data)}\n\n`;
}

export function initializeSse(response: Response): void {
  response.status(200);
  response.setHeader('Content-Type', 'text/event-stream');
  response.setHeader('Cache-Control', 'no-cache, no-transform');
  response.setHeader('Connection', 'keep-alive');
  response.flushHeaders();
}

export function writeSseFrame<TData>(
  response: Response,
  frame: SseFrame<TData>,
): void {
  response.write(formatSseFrame(frame));
}
