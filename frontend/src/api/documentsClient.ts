import type {
  DocumentDetailResponse,
  DocumentListResponse,
  DocumentUploadResponse,
} from '../types/documents'
import { API_BASE_URL, buildAuthHeaders, captureRequestId, parseErrorEnvelope } from './request'

export class DocumentsApiError extends Error {
  status: number
  code?: string

  constructor(message: string, status: number, code?: string) {
    super(message)
    this.name = 'DocumentsApiError'
    this.status = status
    this.code = code
  }
}

async function toDocumentsApiError(
  response: Response,
  fallbackMessage: string,
): Promise<DocumentsApiError> {
  const parsed = await parseErrorEnvelope(response, fallbackMessage)
  return new DocumentsApiError(parsed.message, parsed.status, parsed.code)
}

export async function uploadDocument(file: File): Promise<DocumentUploadResponse> {
  const formData = new FormData()
  formData.append('file', file)

  const response = await fetch(`${API_BASE_URL}/api/documents/upload`, {
    method: 'POST',
    headers: buildAuthHeaders({ json: false }),
    body: formData,
  })

  captureRequestId(response)

  if (!response.ok) {
    throw await toDocumentsApiError(response, `Document upload failed: ${response.status}`)
  }

  return (await response.json()) as DocumentUploadResponse
}

export async function listDocuments(): Promise<DocumentListResponse> {
  const response = await fetch(`${API_BASE_URL}/api/documents`, {
    method: 'GET',
    headers: buildAuthHeaders(),
  })

  captureRequestId(response)

  if (!response.ok) {
    throw await toDocumentsApiError(response, `Failed to list documents: ${response.status}`)
  }

  return (await response.json()) as DocumentListResponse
}

export async function getDocument(id: string): Promise<DocumentDetailResponse> {
  const response = await fetch(`${API_BASE_URL}/api/documents/${id}`, {
    method: 'GET',
    headers: buildAuthHeaders(),
  })

  captureRequestId(response)

  if (!response.ok) {
    throw await toDocumentsApiError(response, `Failed to load document: ${response.status}`)
  }

  return (await response.json()) as DocumentDetailResponse
}

export async function deleteDocument(id: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/documents/${id}`, {
    method: 'DELETE',
    headers: buildAuthHeaders(),
  })

  captureRequestId(response)

  if (response.status === 404) {
    return
  }

  if (!response.ok) {
    throw await toDocumentsApiError(response, `Failed to delete document: ${response.status}`)
  }
}
