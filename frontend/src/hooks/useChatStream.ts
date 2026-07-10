import { useCallback, useRef, useState } from 'react'
import { streamChat } from '../api/chatClient'
import { SseParser } from '../api/sseParser'
import type { ChatChunk, ChatRequest } from '../types/chat'

export interface UseChatStreamOptions {
  onStart?: (chunk: Extract<ChatChunk, { type: 'start' }>) => void
  onDelta?: (chunk: Extract<ChatChunk, { type: 'delta' }>) => void
  onEnd?: (chunk: Extract<ChatChunk, { type: 'end' }>) => void
  onError?: (error: Extract<ChatChunk, { type: 'error' }> | Error) => void
}

/**
 * Opens an SSE stream via `chatClient.streamChat`, parses frames with
 * `sseParser`, and invokes the matching callback per frame type.
 * Exposes `stop()` (aborts the in-flight fetch) and `isStreaming`.
 */
export function useChatStream(options: UseChatStreamOptions = {}) {
  const [isStreaming, setIsStreaming] = useState(false)
  const abortControllerRef = useRef<AbortController | null>(null)

  const start = useCallback(
    async (request: ChatRequest) => {
      const controller = new AbortController()
      abortControllerRef.current = controller
      setIsStreaming(true)

      try {
        const response = await streamChat(request, controller.signal)
        if (!response.ok || !response.body) {
          throw new Error(`Stream request failed: ${response.status}`)
        }

        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        const parser = new SseParser()

        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          const text = decoder.decode(value, { stream: true })
          for (const frame of parser.feed(text)) {
            const chunk = frame.data
            if (chunk.type === 'start') {
              options.onStart?.(chunk)
            } else if (chunk.type === 'delta') {
              options.onDelta?.(chunk)
            } else if (chunk.type === 'end') {
              options.onEnd?.(chunk)
            } else if (chunk.type === 'error') {
              options.onError?.(chunk)
            }
          }
        }
      } catch (error) {
        if ((error as Error).name !== 'AbortError') {
          options.onError?.(error as Error)
        }
      } finally {
        setIsStreaming(false)
        abortControllerRef.current = null
      }
    },
    [options],
  )

  const stop = useCallback(() => {
    abortControllerRef.current?.abort()
  }, [])

  return { start, stop, isStreaming }
}
