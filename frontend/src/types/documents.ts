export interface DocumentSummary {
  id: string
  filename: string
  mime_type: string | null
  status: string
  created_at: string
  updated_at: string
}

export interface DocumentListResponse {
  documents: DocumentSummary[]
}

export type DocumentDetailResponse = DocumentSummary

export interface DocumentUploadResponse {
  document_id: string
  status: string
}
