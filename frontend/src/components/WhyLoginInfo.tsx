import { useState } from 'react'

/**
 * Guest-tier informational affordance (plan Section 5.2): UX copy only, no
 * backend dependency. Explains the benefit of logging in without gating chat.
 */
export function WhyLoginInfo() {
  const [isOpen, setIsOpen] = useState(false)

  return (
    <div className="relative sm:inline-block">
      <button
        type="button"
        className="inline-flex min-h-11 cursor-pointer items-center justify-center rounded-lg border border-shell-800/20 px-3 text-xs font-medium text-shell-700 transition hover:bg-shell-900/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
        aria-expanded={isOpen}
        aria-controls="why-login-popover"
        onClick={() => setIsOpen((value) => !value)}
      >
        Why login?
      </button>
      {isOpen ? (
        <div
          id="why-login-popover"
          role="note"
          className="mt-2 w-full rounded-chat border border-shell-800/15 bg-white p-3 text-xs text-shell-800 shadow-chat-card sm:absolute sm:right-0 sm:top-full sm:mt-2 sm:w-64"
        >
          <p>
            <strong>Sign in with Google to unlock additional capabilities:</strong>
          </p>

          <ul>
            <li>
              Choose from multiple AI providers and models, including OpenAI, Gemini, Groq,
              Anthropic, and more.
            </li>
            <li>Save your conversations to your account with persistent chat history.</li>
            <li>Create and manage multiple chat sessions.</li>
            <li>
              Upload PDF, DOCX, Markdown, and text files, then ask questions grounded in your
              documents using RAG (Retrieval-Augmented Generation).
            </li>
            <li>Use Voice Mode for natural voice conversations.</li>
          </ul>

          <p>
            You can continue chatting as a guest anytime—signing in simply unlocks these additional
            capabilities.
          </p>
        </div>
      ) : null}
    </div>
  )
}
