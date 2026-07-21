import { Link } from 'react-router-dom'
import { useAuthContext } from '../context/AuthContext'

interface AppNavProps {
  current: 'chat' | 'documents'
}

/**
 * Authenticated-only cross-links between chat and documents routes.
 */
export function AppNav({ current }: AppNavProps) {
  const { status } = useAuthContext()

  if (status !== 'authenticated') {
    return null
  }

  if (current === 'chat') {
    return (
      <Link
        to="/documents"
        className="rounded-lg border border-shell-800/20 px-3 py-2 text-sm font-medium text-shell-900 transition hover:bg-shell-900/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
      >
        Documents
      </Link>
    )
  }

  return (
    <Link
      to="/"
      className="rounded-lg border border-shell-800/20 px-3 py-2 text-sm font-medium text-shell-900 transition hover:bg-shell-900/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
    >
      Chat
    </Link>
  )
}
