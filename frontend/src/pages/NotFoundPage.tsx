import { Link } from 'react-router-dom'
import { AppNav } from '../components/AppNav'
import { AuthControls } from '../components/AuthControls'

export function NotFoundPage() {
  return (
    <div className="min-h-dvh bg-linear-to-b from-shell-50 via-shell-100 to-[#ebeff6]">
      <header className="sticky top-0 z-20 border-b border-shell-800/15 bg-shell-50/90 px-3 py-2 backdrop-blur sm:px-4">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-2">
          <AppNav current="chat" />
          <AuthControls />
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-3xl flex-col items-start gap-4 px-3 py-12 sm:px-4 sm:py-16">
        <h1 className="text-2xl font-semibold text-shell-950">Page not found</h1>
        <p className="text-sm text-shell-700">
          We couldn&apos;t find that page. It may have been moved or the link is incorrect.
        </p>
        <div className="flex w-full flex-col gap-3 sm:w-auto sm:flex-row sm:flex-wrap">
          <Link
            to="/"
            className="inline-flex min-h-11 w-full items-center justify-center rounded-lg border border-shell-800/20 bg-white px-4 text-sm font-semibold text-shell-900 shadow-sm transition hover:bg-shell-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 sm:w-auto"
          >
            Back to Chat
          </Link>
          <Link
            to="/"
            className="inline-flex min-h-11 w-full items-center justify-center rounded-lg border border-shell-800/20 px-4 text-sm font-medium text-shell-900 transition hover:bg-shell-900/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 sm:w-auto"
          >
            Go Home
          </Link>
        </div>
      </main>
    </div>
  )
}
