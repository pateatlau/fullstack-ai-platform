import { AuthControls } from './AuthControls'

/**
 * Guest gate for /documents — explains the feature and offers Google sign-in.
 */
export function DocumentsLoginPrompt() {
  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-6 px-4 py-12">
      <div className="space-y-2 text-center">
        <h1 className="text-2xl font-semibold text-shell-950">Documents &amp; Knowledge Base</h1>
        <p className="text-sm text-shell-800">
          Upload PDF, DOCX, Markdown, or text files and ask questions grounded in your documents.
          Sign in to upload, manage, and query your personal knowledge base.
        </p>
      </div>
      <div className="flex flex-col items-center gap-3 rounded-chat border border-shell-800/15 bg-white p-6 shadow-chat-card">
        <p className="text-sm font-medium text-shell-900">Sign in to continue</p>
        <AuthControls />
      </div>
    </div>
  )
}
