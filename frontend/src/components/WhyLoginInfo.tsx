import { useState } from 'react'

/**
 * Guest-tier informational affordance (plan Section 5.2): UX copy only, no
 * backend dependency. Explains the benefit of logging in without gating chat.
 */
export function WhyLoginInfo() {
  const [isOpen, setIsOpen] = useState(false)

  return (
    <div className="relative">
      <button
        type="button"
        className="rounded-lg border border-shell-800/20 px-2 py-2 text-xs font-medium text-shell-700 transition hover:bg-shell-900/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
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
          className="absolute right-0 top-full z-30 mt-2 w-64 rounded-chat border border-shell-800/15 bg-white p-3 text-xs text-shell-800 shadow-chat-card"
        >
          Signing in with Google lets us save your chat history to your account in a future update.
          You can keep chatting as a guest at any time.
        </div>
      ) : null}
    </div>
  )
}
