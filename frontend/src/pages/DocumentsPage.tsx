import { useCallback, useEffect, useState } from 'react'
import { DocumentsApiError, listDocuments } from '../api/documentsClient'
import { AuthControls } from '../components/AuthControls'
import { AppNav } from '../components/AppNav'
import { DocumentList } from '../components/DocumentList'
import { DocumentUpload } from '../components/DocumentUpload'
import { useAuthContext } from '../context/AuthContext'
import type { DocumentSummary } from '../types/documents'

function isInvalidAccessTokenError(error: unknown): boolean {
  return (
    error instanceof DocumentsApiError &&
    (error.code === 'invalid_access_token' || error.status === 401)
  )
}

function AuthenticatedDocumentsContent() {
  const { handleInvalidAccessToken } = useAuthContext()
  const [documents, setDocuments] = useState<DocumentSummary[]>([])
  const [isLoading, setIsLoading] = useState(false)

  const handleDocumentsApiError = useCallback(
    (error: unknown): boolean => {
      if (isInvalidAccessTokenError(error)) {
        handleInvalidAccessToken()
        return true
      }
      return false
    },
    [handleInvalidAccessToken],
  )

  const refreshDocuments = useCallback(async () => {
    setIsLoading(true)
    try {
      const result = await listDocuments()
      setDocuments(result.documents)
    } catch (error) {
      if (!handleDocumentsApiError(error)) {
        setDocuments([])
      }
    } finally {
      setIsLoading(false)
    }
  }, [handleDocumentsApiError])

  useEffect(() => {
    let cancelled = false

    void (async () => {
      setIsLoading(true)
      try {
        const result = await listDocuments()
        if (!cancelled) {
          setDocuments(result.documents)
        }
      } catch (error) {
        if (!cancelled && !handleDocumentsApiError(error)) {
          setDocuments([])
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false)
        }
      }
    })()

    return () => {
      cancelled = true
    }
  }, [handleDocumentsApiError])

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-3 py-4 sm:px-4 sm:py-6">
      <DocumentUpload
        onUploaded={() => void refreshDocuments()}
        onInvalidAccessToken={handleInvalidAccessToken}
      />
      <DocumentList
        documents={documents}
        isLoading={isLoading}
        onChanged={() => void refreshDocuments()}
        onInvalidAccessToken={handleInvalidAccessToken}
      />
      <div className="rounded-chat border border-dashed border-zinc-300 bg-zinc-100/80 p-4">
        <p className="text-sm font-medium text-zinc-900">Ask about your documents in chat</p>
        <p className="mt-1 text-xs text-zinc-600">
          Upload and manage files here, then enable <strong>My documents</strong> on the chat page
          for grounded answers.
        </p>
        <a
          href="/"
          className="mt-3 inline-flex text-xs font-semibold text-brand-600 underline-offset-2 hover:underline"
        >
          Go to chat
        </a>
      </div>
    </div>
  )
}

export function DocumentsPage() {
  return (
    <div className="min-h-dvh bg-linear-to-b from-shell-50 via-shell-100 to-[#ebeff6]">
      <header className="sticky top-0 z-20 border-b border-shell-800/15 bg-shell-50/90 px-3 py-2 backdrop-blur sm:px-4">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <AppNav current="documents" />
            <h1 className="min-w-0 truncate text-sm font-semibold tracking-wide text-shell-900 sm:text-base">
              Documents
            </h1>
          </div>
          <div className="shrink-0">
            <AuthControls />
          </div>
        </div>
      </header>

      <AuthenticatedDocumentsContent />
    </div>
  )
}
