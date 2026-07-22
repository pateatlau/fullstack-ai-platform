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
          Signing in with Google unlocks provider and model selection (OpenAI, Gemini, Groq,
          Anthropic, and more), saves your chat history to your account, and lets you manage
          multiple sessions. You also get access to Documents — upload PDF, DOCX, Markdown, or text
          files and ask questions grounded in your content via RAG. You can keep chatting as a guest
          at any time.
        </div>
      ) : null}
    </div>
  )
}
