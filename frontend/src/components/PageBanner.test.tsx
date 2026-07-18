/* @vitest-environment jsdom */

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { PageBanner } from './PageBanner'

afterEach(() => {
  cleanup()
})

describe('PageBanner', () => {
  it('shows only the session-expired banner when multiple alerts are active', () => {
    render(
      <PageBanner
        sessionExpired
        onDismissSessionExpired={() => undefined}
        quotaBlocked
        error="Something failed"
      />,
    )

    expect(screen.getByText(/session expired/i)).not.toBeNull()
    expect(screen.queryByText(/guest message limit/i)).toBeNull()
    expect(screen.queryByText('Something failed')).toBeNull()
  })

  it('shows the quota banner when session expiry is not active', () => {
    render(
      <PageBanner
        sessionExpired={false}
        onDismissSessionExpired={() => undefined}
        quotaBlocked
        error="Something failed"
      />,
    )

    expect(screen.getByText(/guest message limit/i)).not.toBeNull()
    expect(screen.queryByText('Something failed')).toBeNull()
  })
})
