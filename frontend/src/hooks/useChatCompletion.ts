import { useCallback, useRef, useState } from 'react'
import { sendChat, type ChatResponse } from '../api/chatClient'
import type { ChatRequest } from '../types/chat'

export interface UseChatCompletionOptions {
  onComplete?: (response: ChatResponse) => void
  onError?: (error: Error) => void
}

/**
 * Sends chat turns via non-streaming ``POST /api/chat`` and exposes abort/stop
 * support for parity with ``useChatStream``.
 */
export function useChatCompletion(options: UseChatCompletionOptions = {}) {
  const [isPending, setIsPending] = useState(false)
  const abortControllerRef = useRef<AbortController | null>(null)

  const start = useCallback(
    async (request: ChatRequest) => {
      const controller = new AbortController()
      abortControllerRef.current = controller
      setIsPending(true)

      try {
        const response = await sendChat(request, controller.signal)
        options.onComplete?.(response)
      } catch (error) {
        if ((error as Error).name !== 'AbortError') {
          options.onError?.(error as Error)
        }
      } finally {
        setIsPending(false)
        abortControllerRef.current = null
      }
    },
    [options],
  )

  const stop = useCallback(() => {
    abortControllerRef.current?.abort()
  }, [])

  return { start, stop, isPending }
}
