/* @vitest-environment jsdom */

import { describe, expect, it } from 'vitest'
import { friendlyErrorMessage } from './friendlyErrors'

describe('friendlyErrorMessage', () => {
  it('maps provider_rate_limited to retry copy without raw backend text', () => {
    const message = friendlyErrorMessage(
      'provider_rate_limited',
      'Upstream rate limit hit, please retry shortly.',
    )

    expect(message).toMatch(/wait a moment and retry/i)
    expect(message).not.toMatch(/upstream/i)
    expect(message).not.toMatch(/rate limit hit/i)
  })

  it('maps provider_timeout to timeout retry copy', () => {
    const message = friendlyErrorMessage('provider_timeout', 'Upstream provider timed out.')

    expect(message).toMatch(/took too long/i)
    expect(message).toMatch(/try again/i)
    expect(message).not.toMatch(/upstream/i)
  })

  it('maps provider_error to generic AI service copy', () => {
    const message = friendlyErrorMessage('provider_error', 'Upstream provider failed.')

    expect(message).toMatch(/something went wrong with the ai service/i)
    expect(message).not.toMatch(/upstream/i)
  })

  it('maps empty_provider_response to empty-response retry copy', () => {
    const message = friendlyErrorMessage('empty_provider_response')

    expect(message).toMatch(/empty response/i)
    expect(message).toMatch(/try again/i)
  })

  it('falls back to provided fallback for unknown codes', () => {
    expect(friendlyErrorMessage('validation_error', 'Invalid request payload.')).toBe(
      'Invalid request payload.',
    )
  })

  it('uses generic default when code is unknown and no fallback is provided', () => {
    expect(friendlyErrorMessage('unknown_code')).toBe('Something went wrong. Please try again.')
  })
})
