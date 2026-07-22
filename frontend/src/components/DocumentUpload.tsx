import { useRef, useState, type FormEvent } from 'react'
import { DocumentsApiError, uploadDocument } from '../api/documentsClient'

interface DocumentUploadProps {
  onUploaded: () => void
  onInvalidAccessToken?: () => void
}

const ACCEPTED_TYPES = '.pdf,.docx,.md,.txt'

export function DocumentUpload({ onUploaded, onInvalidAccessToken }: DocumentUploadProps) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [isUploading, setIsUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (!selectedFile || isUploading) {
      return
    }

    setIsUploading(true)
    setError(null)

    try {
      await uploadDocument(selectedFile)
      setSelectedFile(null)
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }
      onUploaded()
    } catch (uploadError) {
      if (uploadError instanceof DocumentsApiError) {
        if (uploadError.code === 'invalid_access_token' || uploadError.status === 401) {
          onInvalidAccessToken?.()
          return
        }
        if (uploadError.status === 413) {
          setError('File exceeds the upload size limit. Choose a smaller file.')
        } else {
          setError(uploadError.message)
        }
      } else {
        setError('Upload failed. Check your connection and try again.')
      }
    } finally {
      setIsUploading(false)
    }
  }

  return (
    <section
      aria-labelledby="document-upload-heading"
      className="rounded-chat border border-shell-800/15 bg-white p-4 shadow-chat-card sm:p-5"
    >
      <h2 id="document-upload-heading" className="text-base font-semibold text-shell-950">
        Upload document
      </h2>
      <p className="mt-1 text-xs text-shell-700">
        Supported formats: PDF, DOCX, Markdown, and plain text.
      </p>

      <form className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-end" onSubmit={handleSubmit}>
        <div className="flex-1">
          <label htmlFor="document-file-input" className="sr-only">
            Choose a document file
          </label>
          <input
            ref={fileInputRef}
            id="document-file-input"
            type="file"
            accept={ACCEPTED_TYPES}
            disabled={isUploading}
            className="block w-full text-sm text-shell-800 file:mr-3 file:rounded-lg file:border file:border-shell-800/20 file:bg-shell-50 file:px-3 file:py-2 file:text-sm file:font-medium file:text-shell-900 hover:file:bg-shell-100"
            onChange={(event) => {
              setSelectedFile(event.target.files?.[0] ?? null)
              setError(null)
            }}
          />
        </div>
        <button
          type="submit"
          disabled={!selectedFile || isUploading}
          className="rounded-chat bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white shadow-chat-card transition hover:bg-brand-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isUploading ? 'Uploading…' : 'Upload'}
        </button>
      </form>

      {isUploading ? (
        <p className="mt-3 text-sm text-shell-700" role="status" aria-live="polite">
          Processing document on the server…
        </p>
      ) : null}

      {error ? (
        <div
          className="mt-3 rounded-lg border border-danger-600/30 bg-danger-100 px-3 py-2 text-sm text-danger-600"
          role="alert"
          aria-live="assertive"
        >
          {error}
        </div>
      ) : null}
    </section>
  )
}
