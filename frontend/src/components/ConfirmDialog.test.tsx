/* @vitest-environment jsdom */

import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ConfirmDialog } from './ConfirmDialog'

describe('ConfirmDialog', () => {
  afterEach(() => {
    cleanup()
  })

  it('renders title and message when open', () => {
    render(
      <ConfirmDialog
        open
        title="Delete conversation"
        message="Delete this conversation? This cannot be undone."
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    )

    expect(screen.getByRole('dialog')).not.toBeNull()
    expect(screen.getByText('Delete conversation')).not.toBeNull()
    expect(screen.getByText('Delete this conversation? This cannot be undone.')).not.toBeNull()
  })

  it('calls onCancel when Cancel is clicked', async () => {
    const onCancel = vi.fn()
    render(
      <ConfirmDialog
        open
        title="Delete conversation"
        message="Delete this conversation? This cannot be undone."
        onConfirm={vi.fn()}
        onCancel={onCancel}
      />,
    )

    await userEvent.setup().click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('calls onConfirm when Delete is clicked', async () => {
    const onConfirm = vi.fn()
    render(
      <ConfirmDialog
        open
        title="Delete conversation"
        message="Delete this conversation? This cannot be undone."
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />,
    )

    await userEvent.setup().click(screen.getByRole('button', { name: 'Delete' }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('calls onCancel when Escape is pressed', async () => {
    const onCancel = vi.fn()
    render(
      <ConfirmDialog
        open
        title="Delete conversation"
        message="Delete this conversation? This cannot be undone."
        onConfirm={vi.fn()}
        onCancel={onCancel}
      />,
    )

    await userEvent.setup().keyboard('{Escape}')
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('does not render when closed', () => {
    render(
      <ConfirmDialog
        open={false}
        title="Delete conversation"
        message="Delete this conversation? This cannot be undone."
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    )

    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
