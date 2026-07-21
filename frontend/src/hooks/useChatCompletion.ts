import { useCallback, useRef, useState } from 'react'
import {
  sendChat,
  sendChatWithProgress,
  type ChatActivityPhase,
  type ChatResponse,
} from '../api/chatClient'
import type { ChatRequest } from '../types/chat'

export interface UseChatCompletionOptions {
  /** When true, request NDJSON activity frames for tool-chat pending labels. */
  useProgress?: boolean
  onComplete?: (response: ChatResponse) => void
  onError?: (error: Error) => void
}

/**
 * Sends chat turns via non-streaming ``POST /api/chat`` and exposes abort/stop
 * support for parity with ``useChatStream``.
 */
export function useChatCompletion(options: UseChatCompletionOptions = {}) {
  const [isPending, setIsPending] = useState(false)
  const [activityPhase, setActivityPhase] = useState<ChatActivityPhase>('thinking')
  const abortControllerRef = useRef<AbortController | null>(null)

  const start = useCallback(
    async (request: ChatRequest) => {
      const controller = new AbortController()
      abortControllerRef.current = controller
      setActivityPhase('thinking')
      setIsPending(true)

      try {
        const response = options.useProgress
          ? await sendChatWithProgress(request, {
              signal: controller.signal,
              onActivity: setActivityPhase,
            })
          : await sendChat(request, controller.signal)
        options.onComplete?.(response)
      } catch (error) {
        if ((error as Error).name !== 'AbortError') {
          options.onError?.(error as Error)
        }
      } finally {
        setIsPending(false)
        setActivityPhase('thinking')
        abortControllerRef.current = null
      }
    },
    [options],
  )

  const stop = useCallback(() => {
    abortControllerRef.current?.abort()
  }, [])

  return { start, stop, isPending, activityPhase }
}
