/* @vitest-environment jsdom */

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LoginButton } from './LoginButton'
import * as googleClientIdModule from '../auth/googleClientId'
import * as googleIdentityLoaderModule from '../auth/googleIdentityLoader'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('LoginButton', () => {
  it('shows a disabled affordance when no Google client ID is configured', () => {
    vi.spyOn(googleClientIdModule, 'getGoogleClientId').mockReturnValue(undefined)

    render(<LoginButton onCredential={vi.fn()} />)

    expect(screen.getByRole('note').textContent).toBe('Login is temporarily unavailable.')
  })

  it('renders the GIS container and initializes the Sign-In button when configured', async () => {
    vi.spyOn(googleClientIdModule, 'getGoogleClientId').mockReturnValue('test-client-id')
    vi.spyOn(googleIdentityLoaderModule, 'loadGoogleIdentityScript').mockResolvedValue(undefined)

    const initialize = vi.fn()
    const renderButton = vi.fn()
    vi.stubGlobal('google', {
      accounts: { id: { initialize, renderButton, disableAutoSelect: vi.fn() } },
    })

    const onCredential = vi.fn()
    render(<LoginButton onCredential={onCredential} />)

    expect(screen.getByLabelText('Sign in with Google')).not.toBeNull()

    await vi.waitFor(() => {
      expect(initialize).toHaveBeenCalledWith(
        expect.objectContaining({ client_id: 'test-client-id' }),
      )
      expect(renderButton).toHaveBeenCalled()
    })

    const callback = initialize.mock.calls[0][0].callback as (response: {
      credential: string
    }) => void
    callback({ credential: 'google-id-token' })

    expect(onCredential).toHaveBeenCalledWith('google-id-token')
  })
})
