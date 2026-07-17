import { describe, expect, it } from 'vitest'
import { getJwtExpiryMs, isJwtExpired } from './jwt'

function makeJwt(exp: number | undefined): string {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))
  const payload = btoa(JSON.stringify(exp === undefined ? {} : { exp }))
  return `${header}.${payload}.signature`
}

describe('jwt expiry helpers', () => {
  it('returns null for a malformed token', () => {
    expect(getJwtExpiryMs('not-a-jwt')).toBeNull()
    expect(isJwtExpired('not-a-jwt')).toBe(false)
  })

  it('returns null when the payload has no exp claim', () => {
    const token = makeJwt(undefined)
    expect(getJwtExpiryMs(token)).toBeNull()
    expect(isJwtExpired(token)).toBe(false)
  })

  it('reports a future exp as not expired', () => {
    const futureExpSeconds = Math.floor(Date.now() / 1000) + 3600
    const token = makeJwt(futureExpSeconds)

    expect(getJwtExpiryMs(token)).toBe(futureExpSeconds * 1000)
    expect(isJwtExpired(token)).toBe(false)
  })

  it('reports a past exp as expired', () => {
    const pastExpSeconds = Math.floor(Date.now() / 1000) - 3600
    const token = makeJwt(pastExpSeconds)

    expect(isJwtExpired(token)).toBe(true)
  })
})
