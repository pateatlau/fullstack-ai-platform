/* @vitest-environment jsdom */

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { Citation, Message } from '../types/chat'
import { MessageBubble } from './MessageBubble'

function assistantMessage(
  content: string,
  status: Message['status'] = 'complete',
  extras: Partial<Message> = {},
): Message {
  return {
    id: 'a1',
    role: 'assistant',
    content,
    status,
    createdAt: '2026-07-24T00:00:00.000Z',
    ...extras,
  }
}

const sampleCitation: Citation = {
  index: 1,
  chunk_id: '11111111-1111-1111-1111-111111111111',
  document_id: '22222222-2222-2222-2222-222222222222',
  snippet: 'Remote work is allowed three days per week.',
  score: 0.91,
  filename: 'policy.pdf',
  source: null,
  page: 3,
}

function userMessage(content: string): Message {
  return {
    id: 'u1',
    role: 'user',
    content,
    status: 'complete',
    createdAt: '2026-07-24T00:00:00.000Z',
  }
}

describe('MessageBubble markdown', () => {
  afterEach(() => {
    cleanup()
  })

  it('renders assistant markdown links and bold without raw asterisks', () => {
    render(
      <MessageBubble
        message={assistantMessage(
          '**[South China Morning Post](https://www.scmp.com/topics/narendra-modi)**: summary',
        )}
      />,
    )

    const link = screen.getByRole('link', { name: 'South China Morning Post' })
    expect(link.getAttribute('href')).toBe('https://www.scmp.com/topics/narendra-modi')
    expect(screen.queryByText(/\*\*/)).toBeNull()
  })

  it('keeps user messages as plain text', () => {
    render(
      <MessageBubble
        message={userMessage(
          '**[South China Morning Post](https://www.scmp.com/topics/narendra-modi)**',
        )}
      />,
    )

    expect(
      screen.getByText('**[South China Morning Post](https://www.scmp.com/topics/narendra-modi)**'),
    ).toBeTruthy()
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('keeps streaming assistant messages as plain text', () => {
    render(
      <MessageBubble
        message={assistantMessage(
          '**[South China Morning Post](https://www.scmp.com/topics/narendra-modi)**: summary',
          'streaming',
        )}
      />,
    )

    expect(
      screen.getByText(
        '**[South China Morning Post](https://www.scmp.com/topics/narendra-modi)**: summary',
      ),
    ).toBeTruthy()
    expect(screen.queryByRole('link')).toBeNull()
  })
})

describe('MessageBubble citations', () => {
  afterEach(() => {
    cleanup()
  })

  it('keeps grounded summary and renders citation list when present', () => {
    render(
      <MessageBubble
        message={assistantMessage('Answer grounded in docs.', 'complete', {
          retrievedChunkCount: 2,
          citations: [sampleCitation],
        })}
      />,
    )

    expect(screen.getByText('Grounded in 2 document chunks.')).toBeTruthy()
    expect(screen.getByLabelText('Citations')).toBeTruthy()
    expect(screen.getByText('[1] policy.pdf · p. 3')).toBeTruthy()
    expect(screen.getByText('Remote work is allowed three days per week.')).toBeTruthy()
  })

  it('does not crash or render a citation list when citations are null or empty', () => {
    const { rerender } = render(
      <MessageBubble
        message={assistantMessage('No citations field.', 'complete', {
          retrievedChunkCount: 1,
          citations: null,
        })}
      />,
    )

    expect(screen.getByText('Grounded in 1 document chunk.')).toBeTruthy()
    expect(screen.queryByLabelText('Citations')).toBeNull()

    rerender(
      <MessageBubble
        message={assistantMessage('Empty citations.', 'complete', {
          retrievedChunkCount: 1,
          citations: [],
        })}
      />,
    )

    expect(screen.getByText('Grounded in 1 document chunk.')).toBeTruthy()
    expect(screen.queryByLabelText('Citations')).toBeNull()
  })
})
