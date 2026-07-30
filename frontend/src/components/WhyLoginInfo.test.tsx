/* @vitest-environment jsdom */

import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import { WhyLoginInfo } from './WhyLoginInfo'

afterEach(() => {
  cleanup()
})

describe('WhyLoginInfo', () => {
  it('hides the explanation until the affordance is opened', () => {
    render(<WhyLoginInfo />)

    expect(screen.queryByRole('note')).toBeNull()
  })

  it('shows the explanation after clicking "Why login?"', async () => {
    render(<WhyLoginInfo />)

    await userEvent.click(screen.getByRole('button', { name: 'Why login?' }))

    expect(screen.getByRole('note').textContent).toContain(
      'Sign in with Google to unlock additional capabilities:Choose from multiple AI providers and models, including OpenAI, Gemini, Groq, Anthropic, and more.Save your conversations to your account with persistent chat history.Create and manage multiple chat sessions.Upload PDF, DOCX, Markdown, and text files, then ask questions grounded in your documents using RAG (Retrieval-Augmented Generation).Use Voice Mode for natural voice conversations.You can continue chatting as a guest anytime—signing in simply unlocks these additional capabilities.',
    )
  })
})
