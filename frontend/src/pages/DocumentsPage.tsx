import { useCallback, useEffect, useState } from 'react'
import { listDocuments } from '../api/documentsClient'
import { AuthControls } from '../components/AuthControls'
import { AppNav } from '../components/AppNav'
import { DocumentList } from '../components/DocumentList'
import { DocumentsLoginPrompt } from '../components/DocumentsLoginPrompt'
import { DocumentUpload } from '../components/DocumentUpload'
import { RagAskPanel } from '../components/RagAskPanel'
import { useAuthContext } from '../context/AuthContext'
import type { DocumentSummary } from '../types/documents'

function AuthenticatedDocumentsContent() {
  const [documents, setDocuments] = useState<DocumentSummary[]>([])
  const [isLoading, setIsLoading] = useState(false)

  const refreshDocuments = useCallback(async () => {
    setIsLoading(true)
    try {
      const result = await listDocuments()
      setDocuments(result.documents)
    } catch {
      setDocuments([])
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    let cancelled = false

    void (async () => {
      setIsLoading(true)
      try {
        const result = await listDocuments()
        if (!cancelled) {
          setDocuments(result.documents)
        }
      } catch {
        if (!cancelled) {
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
  }, [])

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-3 py-4 sm:px-4 sm:py-6">
      <DocumentUpload onUploaded={() => void refreshDocuments()} />
      <DocumentList
        documents={documents}
        isLoading={isLoading}
        onChanged={() => void refreshDocuments()}
      />
      <RagAskPanel />
    </div>
  )
}

export function DocumentsPage() {
  const { status } = useAuthContext()
  const isAuthenticated = status === 'authenticated'

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

      {isAuthenticated ? <AuthenticatedDocumentsContent /> : <DocumentsLoginPrompt />}
    </div>
  )
}
