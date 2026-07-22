interface PageBannerProps {
  sessionExpired: boolean
  onDismissSessionExpired: () => void
  quotaBlocked: boolean
  error: string | null
}

/**
 * Shows at most one high-priority page banner so stacked alerts do not consume
 * mobile viewport height (session expiry beats quota, quota beats generic errors).
 */
export function PageBanner({
  sessionExpired,
  onDismissSessionExpired,
  quotaBlocked,
  error,
}: PageBannerProps) {
  if (sessionExpired) {
    return (
      <div
        className="mx-3 mt-3 flex flex-col items-start gap-3 rounded-chat border border-brand-500/25 bg-brand-500/10 px-4 py-3 text-sm text-shell-900 sm:mx-4 sm:flex-row sm:items-center sm:justify-between"
        role="status"
      >
        <span>Your session expired. Sign in again to continue as you were.</span>
        <button
          type="button"
          className="inline-flex min-h-11 shrink-0 cursor-pointer items-center justify-center rounded-lg border border-shell-800/20 px-3 text-xs font-semibold text-shell-900 transition hover:bg-shell-900/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
          onClick={onDismissSessionExpired}
        >
          Dismiss
        </button>
      </div>
    )
  }

  if (quotaBlocked) {
    return (
      <div
        className="mx-3 mt-3 rounded-chat border border-danger-600/25 bg-danger-100 px-4 py-3 text-sm text-danger-600 sm:mx-4"
        role="alert"
      >
        You&rsquo;ve reached today&rsquo;s guest message limit. Sign in above to keep chatting.
      </div>
    )
  }

  if (error) {
    return (
      <div
        className="mx-3 mt-3 rounded-chat border border-danger-600/25 bg-danger-100 px-4 py-3 text-sm text-danger-600 sm:mx-4"
        role="alert"
      >
        {error}
      </div>
    )
  }

  return null
}
