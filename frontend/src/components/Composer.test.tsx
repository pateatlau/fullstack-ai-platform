/* @vitest-environment jsdom */

import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatPage } from '../pages/ChatPage'

function createStreamResponse(chunks: string[], chunkDelayMs = 0): Response {
  const encoder = new TextEncoder()

  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        const pushChunk = (index: number) => {
          if (index >= chunks.length) {
            controller.close()
            return
          }

          controller.enqueue(encoder.encode(chunks[index]))
          setTimeout(() => pushChunk(index + 1), chunkDelayMs)
        }

        pushChunk(0)
      },
    }),
    {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    },
  )
}

function createAbortableStreamResponse(
  chunks: string[],
  chunkDelayMs: number,
  signal?: AbortSignal,
): Response {
  const encoder = new TextEncoder()
  let aborted = signal?.aborted ?? false

  const abort = () => {
    aborted = true
  }

  signal?.addEventListener('abort', abort, { once: true })

  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        const maybePush = (index: number) => {
          if (aborted) {
            controller.close()
            return
          }

          if (index >= chunks.length) {
            controller.close()
            return
          }

          controller.enqueue(encoder.encode(chunks[index]))
          setTimeout(() => maybePush(index + 1), chunkDelayMs)
        }

        maybePush(0)
      },
      cancel() {
        aborted = true
        signal?.removeEventListener('abort', abort)
        return undefined
      },
    }),
    {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    },
  )
}

describe('Composer behavior', () => {
  beforeEach(() => {
    Object.defineProperty(globalThis.HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    })
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('streams assistant tokens into the chat page after send', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        createStreamResponse([
          'event: start\ndata: {"type":"start","id":"resp_1","timestamp":"t0"}\n\n',
          'event: delta\ndata: {"type":"delta","id":"resp_1","content":"Fast","timestamp":"t1"}\n\n',
          'event: delta\ndata: {"type":"delta","id":"resp_1","content":"API","timestamp":"t2"}\n\n',
          'event: end\ndata: {"type":"end","id":"resp_1","finish_reason":"stop","timestamp":"t3"}\n\n',
        ]),
      )
    vi.stubGlobal('fetch', fetchMock)

    render(<ChatPage />)

    const user = userEvent.setup()
    await user.type(screen.getByPlaceholderText('Ask something…'), 'Hello there')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1)
      expect(screen.getByText('Hello there')).not.toBeNull()
      expect(screen.getByText('FastAPI')).not.toBeNull()
    })
  })

  it('stops the stream and preserves partial content when Stop is pressed', async () => {
    const fetchMock = vi
      .fn()
      .mockImplementation(async (_input: RequestInfo | URL, init?: RequestInit) => {
        return createAbortableStreamResponse(
          [
            'event: start\ndata: {"type":"start","id":"resp_2","timestamp":"t0"}\n\n',
            'event: delta\ndata: {"type":"delta","id":"resp_2","content":"Partial","timestamp":"t1"}\n\n',
            'event: delta\ndata: {"type":"delta","id":"resp_2","content":" answer","timestamp":"t2"}\n\n',
            'event: end\ndata: {"type":"end","id":"resp_2","finish_reason":"stop","timestamp":"t3"}\n\n',
          ],
          40,
          init?.signal ?? undefined,
        )
      })
    vi.stubGlobal('fetch', fetchMock)

    render(<ChatPage />)

    const user = userEvent.setup()
    await user.type(screen.getByPlaceholderText('Ask something…'), 'Stop early')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Stop' })).not.toBeNull()
      expect(screen.getByText('Partial')).not.toBeNull()
    })

    await user.click(screen.getByRole('button', { name: 'Stop' }))

    await waitFor(() => {
      expect(screen.getByText('Stopped.')).not.toBeNull()
    })

    expect(screen.queryByText('Partial answer')).toBeNull()
  })
})
