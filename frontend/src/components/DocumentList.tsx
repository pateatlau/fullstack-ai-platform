import { useState } from 'react'
import { deleteDocument, DocumentsApiError } from '../api/documentsClient'
import { EmptyState } from './EmptyState'
import { LoadingIndicator } from './LoadingIndicator'
import type { DocumentSummary } from '../types/documents'

interface DocumentListProps {
  documents: DocumentSummary[]
  isLoading: boolean
  onChanged: () => void
  onInvalidAccessToken?: () => void
}

function statusBadgeClass(status: string): string {
  switch (status) {
    case 'ready':
      return 'bg-brand-500/15 text-brand-600'
    case 'processing':
      return 'bg-amber-100 text-amber-800'
    case 'failed':
      return 'bg-danger-100 text-danger-600'
    default:
      return 'bg-shell-100 text-shell-800'
  }
}

export function DocumentList({
  documents,
  isLoading,
  onChanged,
  onInvalidAccessToken,
}: DocumentListProps) {
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const handleDelete = async (id: string, filename: string) => {
    setDeletingId(id)
    setError(null)

    try {
      await deleteDocument(id)
      onChanged()
    } catch (deleteError) {
      if (deleteError instanceof DocumentsApiError) {
        if (deleteError.code === 'invalid_access_token' || deleteError.status === 401) {
          onInvalidAccessToken?.()
          return
        }
        setError(deleteError.message)
      } else {
        setError(`Could not delete "${filename}". Try again.`)
      }
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <section
      aria-labelledby="document-list-heading"
      className="rounded-chat border border-shell-800/15 bg-white p-4 shadow-chat-card sm:p-5"
    >
      <h2 id="document-list-heading" className="text-base font-semibold text-shell-950">
        Your documents
      </h2>

      {error ? (
        <div
          className="mt-3 rounded-lg border border-danger-600/30 bg-danger-100 px-3 py-2 text-sm text-danger-600"
          role="alert"
          aria-live="assertive"
        >
          {error}
        </div>
      ) : null}

      {isLoading ? (
        <LoadingIndicator variant="inline" label="Loading documents…" className="mt-4" />
      ) : documents.length === 0 ? (
        <EmptyState
          className="mt-4 border-shell-800/20 bg-shell-50/80 [&_h3]:text-shell-950 [&_p]:text-shell-700"
          title="No documents yet"
          description="Upload a file above to ground chat answers in your own content."
        />
      ) : (
        <ul className="mt-4 divide-y divide-shell-800/10" aria-label="Uploaded documents">
          {documents.map((document) => (
            <li
              key={document.id}
              className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-shell-950">{document.filename}</p>
                <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-shell-700">
                  <span
                    className={`rounded-chip px-2 py-0.5 font-medium ${statusBadgeClass(document.status)}`}
                  >
                    {document.status}
                  </span>
                  <time dateTime={document.created_at}>
                    {new Date(document.created_at).toLocaleString()}
                  </time>
                </div>
              </div>
              <button
                type="button"
                aria-label={`Delete ${document.filename}`}
                disabled={deletingId === document.id}
                className="inline-flex min-h-11 w-full shrink-0 items-center justify-center rounded-lg border border-shell-800/20 px-3 text-sm font-medium text-shell-900 transition hover:bg-shell-900/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:opacity-60 sm:w-auto"
                onClick={() => void handleDelete(document.id, document.filename)}
              >
                {deletingId === document.id ? 'Deleting…' : 'Delete'}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
