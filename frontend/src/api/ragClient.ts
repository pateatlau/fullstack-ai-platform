import type { RAGAskRequest, RAGAskResponse } from '../types/rag'
import { API_BASE_URL, buildAuthHeaders, captureRequestId, parseErrorEnvelope } from './request'

export const RAG_FEATURE_DISABLED_CODE = 'feature_disabled'
export const RAG_DISABLED_MESSAGE = 'RAG is not enabled on this server'

export class RagApiError extends Error {
  status: number
  code?: string

  constructor(message: string, status: number, code?: string) {
    super(message)
    this.name = 'RagApiError'
    this.status = status
    this.code = code
  }
}

async function toRagApiError(response: Response, fallbackMessage: string): Promise<RagApiError> {
  const parsed = await parseErrorEnvelope(response, fallbackMessage)
  const message = parsed.code === RAG_FEATURE_DISABLED_CODE ? RAG_DISABLED_MESSAGE : parsed.message
  return new RagApiError(message, parsed.status, parsed.code)
}

export async function askRag(
  question: string,
  options?: Omit<RAGAskRequest, 'question'>,
): Promise<RAGAskResponse> {
  const body: RAGAskRequest = { question, ...options }

  const response = await fetch(`${API_BASE_URL}/api/rag/ask`, {
    method: 'POST',
    headers: buildAuthHeaders(),
    body: JSON.stringify(body),
  })

  captureRequestId(response)

  if (!response.ok) {
    throw await toRagApiError(response, `RAG request failed: ${response.status}`)
  }

  return (await response.json()) as RAGAskResponse
}
