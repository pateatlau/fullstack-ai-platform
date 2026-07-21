/* @vitest-environment jsdom */

import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import { WhyLoginInfo } from './WhyLoginInfo'

afterEach(() => {
  cleanup()
})

describe('WhyLoginInfo', () => {
  it('hides the explanation until the affordance is opened', () => {
    render(<WhyLoginInfo />)

    expect(screen.queryByRole('note')).toBeNull()
  })

  it('shows the explanation after clicking "Why login?"', async () => {
    render(<WhyLoginInfo />)

    await userEvent.click(screen.getByRole('button', { name: 'Why login?' }))

    expect(screen.getByRole('note').textContent).toContain('provider and model selection')
    expect(screen.getByRole('note').textContent).toContain('multiple sessions')
    expect(screen.getByRole('note').textContent).toContain('Documents')
    expect(screen.getByRole('note').textContent).toContain('RAG')
  })
})
