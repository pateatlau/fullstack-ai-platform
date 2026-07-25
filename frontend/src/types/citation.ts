/** Structured citation for an included context block (matches backend CitationSchema). */
export interface Citation {
  index: number
  chunk_id: string
  document_id: string
  snippet: string
  score: number
  filename?: string | null
  source?: string | null
  page?: number | string | null
}
