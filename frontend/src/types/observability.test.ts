import { describe, expect, it, vi, afterEach } from 'vitest'
import { defaultTrailingDateRange } from './observability'

describe('defaultTrailingDateRange', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('returns exactly N inclusive UTC calendar days ending today', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-10T15:30:00.000Z'))

    const { since, until } = defaultTrailingDateRange(30)

    expect(until).toBe('2026-08-10')
    expect(since).toBe('2026-07-12')
  })

  it('returns a single day when days is 1', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-10T00:00:00.000Z'))

    const { since, until } = defaultTrailingDateRange(1)

    expect(since).toBe('2026-08-10')
    expect(until).toBe('2026-08-10')
  })
})
