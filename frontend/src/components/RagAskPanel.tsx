import { useState, type FormEvent } from 'react'
import {
  askRag,
  RagApiError,
  RAG_DISABLED_MESSAGE,
  RAG_FEATURE_DISABLED_CODE,
} from '../api/ragClient'
import type { RAGAskResponse } from '../types/rag'

export function RagAskPanel() {
  const [question, setQuestion] = useState('')
  const [isAsking, setIsAsking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [response, setResponse] = useState<RAGAskResponse | null>(null)
  const [ragDisabled, setRagDisabled] = useState(false)

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    const trimmed = question.trim()
    if (!trimmed || isAsking || ragDisabled) {
      return
    }

    setIsAsking(true)
    setError(null)

    try {
      const result = await askRag(trimmed)
      setResponse(result)
    } catch (askError) {
      if (askError instanceof RagApiError) {
        if (askError.status === 503 && askError.code === RAG_FEATURE_DISABLED_CODE) {
          setRagDisabled(true)
        } else {
          setError(askError.message)
        }
      } else {
        setError('Could not get an answer. Check your connection and try again.')
      }
    } finally {
      setIsAsking(false)
    }
  }

  return (
    <section
      aria-labelledby="rag-ask-heading"
      className="rounded-chat border border-shell-800/15 bg-white p-4 shadow-chat-card sm:p-5"
    >
      <h2 id="rag-ask-heading" className="text-base font-semibold text-shell-950">
        Ask your documents
      </h2>
      <p className="mt-1 text-xs text-shell-700">
        Ask a question and get an answer grounded in your uploaded documents.
      </p>

      {ragDisabled ? (
        <div
          className="mt-4 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900"
          role="status"
        >
          {RAG_DISABLED_MESSAGE}
        </div>
      ) : null}

      <form className="mt-4 space-y-3" onSubmit={handleSubmit}>
        <div>
          <label htmlFor="rag-question-input" className="sr-only">
            Question about your documents
          </label>
          <textarea
            id="rag-question-input"
            rows={3}
            value={question}
            disabled={isAsking || ragDisabled}
            placeholder="What would you like to know from your documents?"
            className="w-full resize-y rounded-chat border border-shell-800/20 bg-shell-50 px-3 py-2 text-sm text-shell-950 placeholder:text-shell-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:opacity-60"
            onChange={(event) => setQuestion(event.target.value)}
          />
        </div>
        <button
          type="submit"
          disabled={!question.trim() || isAsking || ragDisabled}
          className="rounded-chat bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white shadow-chat-card transition hover:bg-brand-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isAsking ? 'Asking…' : 'Ask'}
        </button>
      </form>

      {error ? (
        <div
          className="mt-3 rounded-lg border border-danger-600/30 bg-danger-100 px-3 py-2 text-sm text-danger-600"
          role="alert"
          aria-live="assertive"
        >
          {error}
        </div>
      ) : null}

      {response ? (
        <div className="mt-4 space-y-2" aria-live="polite">
          <h3 className="text-sm font-semibold text-shell-900">Answer</h3>
          <p className="whitespace-pre-wrap text-sm text-shell-800">{response.answer}</p>
          {response.retrieved_chunks.length === 0 ? (
            <p className="text-xs text-shell-700">
              No document chunks were retrieved for this question.
            </p>
          ) : (
            <p className="text-xs text-shell-700">
              Retrieved {response.retrieved_chunks.length} chunk
              {response.retrieved_chunks.length === 1 ? '' : 's'} from your documents.
            </p>
          )}
        </div>
      ) : null}
    </section>
  )
}
