/* @vitest-environment jsdom */

import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthControls } from './AuthControls'
import { AuthProvider } from '../context/AuthContext'
import { AuthApiError } from '../api/authClient'
import * as authClient from '../api/authClient'
import { storeSession } from '../auth/tokenStorage'
import type { AuthenticatedUser } from '../types/auth'

// LoginButton depends on GIS/env details unrelated to AuthControls' own
// guest-vs-authenticated rendering logic; stub it with a clickable button
// that invokes `onCredential` so tests can drive login() deterministically.
vi.mock('./LoginButton', () => ({
  LoginButton: ({ onCredential }: { onCredential: (idToken: string) => void }) => (
    <button
      type="button"
      data-testid="login-button-stub"
      onClick={() => onCredential('fake-id-token')}
    >
      Login Button
    </button>
  ),
}))

const user: AuthenticatedUser = {
  id: 'user-1',
  email: 'person@example.com',
  display_name: 'Person',
  picture_url: null,
}

describe('AuthControls', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    window.localStorage.clear()
    cleanup()
  })

  it('shows the login affordance when guest', () => {
    render(
      <AuthProvider>
        <AuthControls />
      </AuthProvider>,
    )

    expect(screen.getByTestId('login-button-stub')).not.toBeNull()
  })

  it('shows the user indicator and Logout action when authenticated', async () => {
    storeSession('stored-jwt', user)

    render(
      <AuthProvider>
        <AuthControls />
      </AuthProvider>,
    )

    await waitFor(() => {
      expect(screen.getByText('Person')).not.toBeNull()
    })
    expect(screen.getByRole('button', { name: 'Log out' })).not.toBeNull()
  })

  it('logging out reverts the UI to the guest login affordance', async () => {
    storeSession('stored-jwt', user)

    render(
      <AuthProvider>
        <AuthControls />
      </AuthProvider>,
    )

    const logoutButton = await screen.findByRole('button', { name: 'Log out' })
    await userEvent.click(logoutButton)

    expect(screen.getByTestId('login-button-stub')).not.toBeNull()
  })

  it('shows the "Why login?" affordance when guest', () => {
    render(
      <AuthProvider>
        <AuthControls />
      </AuthProvider>,
    )

    expect(screen.getByRole('button', { name: 'Why login?' })).not.toBeNull()
  })

  it('shows a friendly message and Dismiss action when login fails', async () => {
    vi.spyOn(authClient, 'loginWithGoogle').mockRejectedValue(
      new AuthApiError('boom', 401, 'invalid_google_token'),
    )

    render(
      <AuthProvider>
        <AuthControls />
      </AuthProvider>,
    )

    await userEvent.click(screen.getByTestId('login-button-stub'))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('Google sign-in failed. Please try again.')

    await userEvent.click(screen.getByRole('button', { name: 'Dismiss' }))

    expect(screen.queryByRole('alert')).toBeNull()
  })
})
