import { useCallback, useEffect, useState } from 'react'
import { fetchPluginInventory, PluginsApiError } from '../api/pluginsClient'
import { AppNav } from '../components/AppNav'
import { AuthControls } from '../components/AuthControls'
import { EmptyState } from '../components/EmptyState'
import { LoadingIndicator } from '../components/LoadingIndicator'
import { useAuthContext } from '../context/AuthContext'
import { useChatStreamingEnabled } from '../hooks/useChatStreamingEnabled'
import type { PluginInventoryItem } from '../types/plugins'
import {
  displayPluginId,
  displayPluginName,
  formatContributionKind,
  formatLoadDurationMs,
  formatPluginStatus,
} from '../types/plugins'

function isInvalidAccessTokenError(error: unknown): boolean {
  return (
    error instanceof PluginsApiError &&
    (error.code === 'invalid_access_token' || error.status === 401)
  )
}

interface StatusBannerProps {
  tone: 'success' | 'error'
  message: string
}

function StatusBanner({ tone, message }: StatusBannerProps) {
  const toneClass =
    tone === 'success'
      ? 'border-brand-500/30 bg-brand-500/10 text-brand-700'
      : 'border-danger-600/30 bg-danger-100 text-danger-600'

  return (
    <div
      className={`rounded-lg border px-3 py-2 text-sm ${toneClass}`}
      role={tone === 'error' ? 'alert' : 'status'}
      aria-live={tone === 'error' ? 'assertive' : 'polite'}
    >
      {message}
    </div>
  )
}

function PluginsUnavailableNotice() {
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-3 py-8 sm:px-4">
      <section className="rounded-chat border border-shell-800/15 bg-white p-5 shadow-chat-card">
        <h2 className="text-base font-semibold text-shell-950">Plugins are not available</h2>
        <p className="mt-2 text-sm text-shell-700">
          The Plugin Architecture is disabled on this server. Your chat experience is unchanged.
        </p>
        <a
          href="/"
          className="mt-4 inline-flex text-sm font-semibold text-brand-600 underline-offset-2 hover:underline"
        >
          Return to chat
        </a>
      </section>
    </div>
  )
}

function statusBadgeClass(status: PluginInventoryItem['status']): string {
  return status === 'loaded'
    ? 'border-brand-300 bg-brand-100 text-brand-800'
    : 'border-danger-300 bg-danger-100 text-danger-700'
}

function PluginFailureDetails({ item }: { item: PluginInventoryItem }) {
  if (!item.failure) {
    return null
  }

  const { failure } = item

  return (
    <div className="mt-1 text-xs text-danger-700">
      <p>
        <span className="font-medium">Failure:</span> {failure.code}
      </p>
      <p className="mt-0.5">{failure.message}</p>
      {failure.code === 'unsupported_api_version' ? (
        <p className="mt-0.5">
          Manifest API version: {failure.manifest_api_version ?? '—'}. Supported:{' '}
          {(failure.expected_api_versions ?? []).join(', ') || '—'}.
        </p>
      ) : null}
    </div>
  )
}

function PluginLinks({ item }: { item: PluginInventoryItem }) {
  const links = [
    item.homepage ? { label: 'Homepage', href: item.homepage } : null,
    item.repository ? { label: 'Repository', href: item.repository } : null,
    item.documentation ? { label: 'Docs', href: item.documentation } : null,
  ].filter((link): link is { label: string; href: string } => link !== null)

  if (links.length === 0) {
    return <span className="text-shell-600">—</span>
  }

  return (
    <div className="flex flex-wrap gap-x-2 gap-y-1">
      {links.map((link) => (
        <a
          key={link.label}
          href={link.href}
          target="_blank"
          rel="noopener noreferrer"
          className="font-medium text-brand-600 underline-offset-2 hover:underline"
        >
          {link.label}
        </a>
      ))}
    </div>
  )
}

function PluginsContent() {
  const { handleInvalidAccessToken } = useAuthContext()
  const [plugins, setPlugins] = useState<PluginInventoryItem[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const handleApiError = useCallback(
    (apiError: unknown): boolean => {
      if (isInvalidAccessTokenError(apiError)) {
        handleInvalidAccessToken()
        return true
      }
      if (apiError instanceof PluginsApiError) {
        if (apiError.code === 'feature_disabled') {
          setError('Plugins are not enabled on this server.')
          return true
        }
        setError(apiError.message)
        return true
      }
      setError('Something went wrong. Please try again.')
      return true
    },
    [handleInvalidAccessToken],
  )

  useEffect(() => {
    let cancelled = false

    void (async () => {
      setIsLoading(true)
      setError(null)
      try {
        const response = await fetchPluginInventory()
        if (!cancelled) {
          setPlugins(response.plugins)
        }
      } catch (apiError) {
        if (!cancelled) {
          handleApiError(apiError)
          setPlugins([])
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false)
        }
      }
    })()

    return () => {
      cancelled = true
    }
  }, [handleApiError])

  const loadedCount = plugins.filter((plugin) => plugin.status === 'loaded').length
  const failedCount = plugins.filter((plugin) => plugin.status === 'failed').length

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-6 px-3 py-4 sm:px-4 sm:py-6">
      {error ? <StatusBanner tone="error" message={error} /> : null}

      <section
        aria-labelledby="plugins-status-heading"
        className="rounded-chat border border-shell-800/15 bg-white p-4 shadow-chat-card sm:p-5"
      >
        <h2 id="plugins-status-heading" className="text-base font-semibold text-shell-950">
          Plugin inventory
        </h2>
        <p className="mt-2 text-sm text-shell-700">
          Read-only snapshot of plugins loaded at server startup. Restart the server after plugin
          changes.
        </p>
        {!isLoading && plugins.length > 0 ? (
          <p className="mt-2 text-sm text-shell-700">
            {loadedCount} loaded, {failedCount} failed.
          </p>
        ) : null}
      </section>

      <section
        aria-labelledby="plugins-list-heading"
        className="rounded-chat border border-shell-800/15 bg-white p-4 shadow-chat-card sm:p-5"
      >
        <h2 id="plugins-list-heading" className="text-base font-semibold text-shell-950">
          Loaded plugins
        </h2>

        {isLoading ? (
          <LoadingIndicator variant="inline" label="Loading plugin inventory…" className="mt-4" />
        ) : plugins.length === 0 ? (
          <EmptyState
            className="mt-4 border-shell-800/20 bg-shell-50/80 [&_h3]:text-shell-950 [&_p]:text-shell-700"
            title="No plugins loaded"
            description="No plugins were discovered or loaded on this server."
          />
        ) : (
          <div className="mt-4 overflow-x-auto">
            <table className="min-w-full text-left text-sm text-shell-900">
              <caption className="sr-only">Plugin inventory loaded at server startup</caption>
              <thead className="border-b border-shell-800/15 text-xs uppercase tracking-wide text-shell-700">
                <tr>
                  <th scope="col" className="px-2 py-2">
                    Name
                  </th>
                  <th scope="col" className="px-2 py-2">
                    Plugin ID
                  </th>
                  <th scope="col" className="px-2 py-2">
                    Version
                  </th>
                  <th scope="col" className="px-2 py-2">
                    API version
                  </th>
                  <th scope="col" className="px-2 py-2">
                    Contributions
                  </th>
                  <th scope="col" className="px-2 py-2">
                    Status
                  </th>
                  <th scope="col" className="px-2 py-2">
                    Load time
                  </th>
                  <th scope="col" className="px-2 py-2">
                    Author
                  </th>
                  <th scope="col" className="px-2 py-2">
                    Links
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-shell-800/10">
                {plugins.map((plugin, index) => (
                  <tr key={`${plugin.plugin_id ?? 'unknown'}-${index}`}>
                    <td className="px-2 py-2 align-top font-medium">
                      <div>{displayPluginName(plugin)}</div>
                      <PluginFailureDetails item={plugin} />
                    </td>
                    <td className="px-2 py-2 align-top font-mono text-xs">
                      {displayPluginId(plugin.plugin_id)}
                    </td>
                    <td className="px-2 py-2 align-top">{plugin.version ?? '—'}</td>
                    <td className="px-2 py-2 align-top">{plugin.api_version ?? '—'}</td>
                    <td className="px-2 py-2 align-top">
                      {plugin.contributions.length > 0
                        ? plugin.contributions.map(formatContributionKind).join(', ')
                        : '—'}
                    </td>
                    <td className="px-2 py-2 align-top">
                      <span
                        className={`inline-flex rounded-full border px-2 py-0.5 text-xs font-medium ${statusBadgeClass(plugin.status)}`}
                      >
                        {formatPluginStatus(plugin.status)}
                      </span>
                    </td>
                    <td className="px-2 py-2 align-top">
                      {formatLoadDurationMs(plugin.load_duration_ms)}
                    </td>
                    <td className="px-2 py-2 align-top">{plugin.author ?? '—'}</td>
                    <td className="px-2 py-2 align-top">
                      <PluginLinks item={plugin} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}

export function PluginsPage() {
  const { pluginsEnabled, healthLoading } = useChatStreamingEnabled()

  return (
    <div className="min-h-dvh bg-linear-to-b from-shell-50 via-shell-100 to-[#ebeff6]">
      <header className="sticky top-0 z-20 border-b border-shell-800/15 bg-shell-50/90 px-3 py-2 backdrop-blur sm:px-4">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <AppNav current="plugins" />
            <h1 className="min-w-0 truncate text-sm font-semibold tracking-wide text-shell-900 sm:text-base">
              Plugins
            </h1>
          </div>
          <div className="shrink-0">
            <AuthControls />
          </div>
        </div>
      </header>

      {healthLoading ? (
        <div className="mx-auto flex w-full max-w-5xl justify-center px-3 py-12 sm:px-4">
          <LoadingIndicator variant="inline" label="Loading plugins…" />
        </div>
      ) : pluginsEnabled ? (
        <PluginsContent />
      ) : (
        <PluginsUnavailableNotice />
      )}
    </div>
  )
}
