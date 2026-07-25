import type { ProviderName } from '../constants/providerModels'
import type { Citation } from './citation'

export type { Citation }

export interface RetrievedChunkMeta {
  chunk_id: string | null
  document_id: string | null
  chunk_index: number | null
  score: number
}

export interface RAGAskRequest {
  question: string
  prompt_template?: string
  instructions?: string
  top_k?: number
  temperature?: number
}

export interface RAGAskResponse {
  answer: string
  retrieved_chunks: RetrievedChunkMeta[]
  truncated: boolean
  model: string
  provider: ProviderName
  retrieval_latency_ms?: number | null
  llm_latency_ms?: number | null
  /** Additive (advanced RAG). ``null`` / omitted on V1 / flag-off. */
  citations?: Citation[] | null
}
