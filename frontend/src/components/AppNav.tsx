import { Link } from 'react-router-dom'
import { useAuthContext } from '../context/AuthContext'
import { useChatStreamingEnabled } from '../hooks/useChatStreamingEnabled'

interface AppNavProps {
  current: 'chat' | 'documents' | 'memory' | 'workflows' | 'observability' | 'plugins' | 'approvals'
}

const NAV_LINK_CLASS =
  'rounded-lg border border-shell-800/20 px-3 py-2 text-sm font-medium text-shell-900 transition hover:bg-shell-900/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500'

/**
 * Authenticated-only cross-links between chat, documents, and memory routes.
 */
export function AppNav({ current }: AppNavProps) {
  const { status } = useAuthContext()
  const {
    memoryEnabled,
    workflowEngineEnabled,
    observabilityEnabled,
    pluginsEnabled,
    hitlEnabled,
    healthLoading,
  } = useChatStreamingEnabled()

  if (status !== 'authenticated') {
    return null
  }

  return (
    <nav className="flex flex-wrap items-center gap-2" aria-label="App sections">
      {current !== 'chat' ? (
        <Link to="/" className={NAV_LINK_CLASS}>
          Chat
        </Link>
      ) : null}
      {current !== 'documents' ? (
        <Link to="/documents" className={NAV_LINK_CLASS}>
          Documents
        </Link>
      ) : null}
      {!healthLoading && memoryEnabled && current !== 'memory' ? (
        <Link to="/settings/memory" className={NAV_LINK_CLASS}>
          Memory
        </Link>
      ) : null}
      {!healthLoading && workflowEngineEnabled && current !== 'workflows' ? (
        <Link to="/workflows" className={NAV_LINK_CLASS}>
          Workflows
        </Link>
      ) : null}
      {!healthLoading && observabilityEnabled && current !== 'observability' ? (
        <Link to="/observability" className={NAV_LINK_CLASS}>
          Observability
        </Link>
      ) : null}
      {!healthLoading && pluginsEnabled && current !== 'plugins' ? (
        <Link to="/plugins" className={NAV_LINK_CLASS}>
          Plugins
        </Link>
      ) : null}
      {!healthLoading && hitlEnabled && current !== 'approvals' ? (
        <Link to="/approvals" className={NAV_LINK_CLASS}>
          Approvals
        </Link>
      ) : null}
    </nav>
  )
}
