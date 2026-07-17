import { useCallback } from 'react'
import { useAuthContext } from '../context/AuthContext'
import { LoginButton } from './LoginButton'
import { WhyLoginInfo } from './WhyLoginInfo'

/**
 * Guest-tier: renders the Google Sign-In button + "Why login?" affordance,
 * and any login-error message (plan Section 5.5).
 * Authenticated-tier: renders a minimal user indicator + Logout action.
 */
export function AuthControls() {
  const { status, user, login, logout, loginError, clearLoginError } = useAuthContext()

  const handleCredential = useCallback(
    (idToken: string) => {
      void login(idToken)
    },
    [login],
  )

  if (status === 'authenticated' && user) {
    return (
      <div className="flex items-center gap-2">
        {user.picture_url ? (
          <img
            src={user.picture_url}
            alt=""
            referrerPolicy="no-referrer"
            className="h-7 w-7 rounded-full border border-shell-800/15"
          />
        ) : null}
        <span className="hidden text-sm font-medium text-shell-900 sm:inline">
          {user.display_name ?? user.email ?? 'Signed in'}
        </span>
        <button
          type="button"
          className="rounded-lg border border-shell-800/20 px-3 py-2 text-sm font-medium text-shell-900 transition hover:bg-shell-900/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
          onClick={logout}
        >
          Log out
        </button>
      </div>
    )
  }

  return (
    <div className="flex items-center gap-2">
      <LoginButton onCredential={handleCredential} />
      <WhyLoginInfo />
      {loginError ? (
        <div role="alert" className="flex items-center gap-2 text-xs text-danger-600">
          <span>{loginError.message}</span>
          <button type="button" className="font-semibold underline" onClick={clearLoginError}>
            Dismiss
          </button>
        </div>
      ) : null}
    </div>
  )
}
