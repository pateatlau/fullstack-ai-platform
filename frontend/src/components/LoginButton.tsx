import { useEffect, useRef } from 'react'
import { getGoogleClientId } from '../auth/googleClientId'
import { loadGoogleIdentityScript } from '../auth/googleIdentityLoader'

interface LoginButtonProps {
  /** Called with the Google-issued ID token once the user completes sign-in. */
  onCredential: (idToken: string) => void
}

/**
 * Renders the Google Identity Services (GIS) Sign-In button (Decision D4).
 * Only the Google client ID (public) is used here; the ID token is handed to
 * the caller, never inspected or trusted client-side.
 */
export function LoginButton({ onCredential }: LoginButtonProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const clientId = getGoogleClientId()

  useEffect(() => {
    if (!clientId || !containerRef.current) {
      return
    }

    let cancelled = false
    const container = containerRef.current

    loadGoogleIdentityScript()
      .then(() => {
        if (cancelled || !window.google) {
          return
        }

        window.google.accounts.id.initialize({
          client_id: clientId,
          callback: (response) => onCredential(response.credential),
        })
        window.google.accounts.id.renderButton(container, {
          type: 'standard',
          theme: 'outline',
          size: 'medium',
          text: 'signin_with',
          shape: 'rectangular',
        })
      })
      .catch(() => {
        // Network/script-load failure UX is Phase 3 scope; fail silently here
        // so a transient GIS outage never breaks the chat page.
      })

    return () => {
      cancelled = true
    }
  }, [clientId, onCredential])

  if (!clientId) {
    return (
      <span className="text-xs text-shell-700" role="note">
        Login is temporarily unavailable.
      </span>
    )
  }

  return <div ref={containerRef} aria-label="Sign in with Google" />
}
