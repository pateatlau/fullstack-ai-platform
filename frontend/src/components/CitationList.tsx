import type { Citation } from '../types/citation'

interface CitationListProps {
  citations: Citation[]
}

function citationLabel(citation: Citation): string {
  const name = citation.filename?.trim() || citation.source?.trim()
  if (name && citation.page != null && citation.page !== '') {
    return `${name} · p. ${citation.page}`
  }
  if (name) {
    return name
  }
  if (citation.page != null && citation.page !== '') {
    return `p. ${citation.page}`
  }
  return 'Source'
}

/** Minimal citation list — index, filename/source, optional page, short snippet. */
export function CitationList({ citations }: CitationListProps) {
  if (citations.length === 0) {
    return null
  }

  return (
    <ul className="mt-2 space-y-2" aria-label="Citations">
      {citations.map((citation) => (
        <li key={`${citation.index}-${citation.chunk_id}`} className="text-[11px] text-zinc-600">
          <div className="font-medium text-zinc-700">
            [{citation.index}] {citationLabel(citation)}
          </div>
          {citation.snippet.trim() ? (
            <p className="mt-0.5 line-clamp-3 whitespace-pre-wrap text-zinc-500">
              {citation.snippet}
            </p>
          ) : null}
        </li>
      ))}
    </ul>
  )
}
