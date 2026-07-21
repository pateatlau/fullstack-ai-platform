/* @vitest-environment jsdom */

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { StreamingIndicator } from './StreamingIndicator'

describe('StreamingIndicator', () => {
  it('defaults to the typing label', () => {
    render(<StreamingIndicator />)
    expect(screen.getByLabelText('Assistant is typing')).not.toBeNull()
    expect(screen.getByText('typing…')).not.toBeNull()
  })

  it('shows searching web when that variant is selected', () => {
    render(<StreamingIndicator variant="searching_web" />)
    expect(screen.getByLabelText('Assistant is searching the web')).not.toBeNull()
    expect(screen.getByText('searching web…')).not.toBeNull()
  })
})
