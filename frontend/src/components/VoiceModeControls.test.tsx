/* @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { VoiceModeControls } from './VoiceModeControls'

const defaultProps = {
  voiceModeEnabled: false,
  onVoiceModeChange: vi.fn(),
  isRecording: false,
  isSpeaking: false,
  isVoiceReady: true,
  onMicPressStart: vi.fn(),
  onMicPressEnd: vi.fn(),
  onInterrupt: vi.fn(),
  hasActiveSession: true,
}

describe('VoiceModeControls', () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('renders voice mode toggle and mic when voice mode is enabled', () => {
    render(<VoiceModeControls {...defaultProps} voiceModeEnabled />)

    expect(screen.getByRole('checkbox', { name: 'Voice mode' })).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Hold to speak' })).not.toBeNull()
  })

  it('hides mic when voice mode is off', () => {
    render(<VoiceModeControls {...defaultProps} voiceModeEnabled={false} />)

    expect(screen.queryByRole('button', { name: 'Hold to speak' })).toBeNull()
  })

  it('calls onVoiceModeChange when toggled', async () => {
    const onVoiceModeChange = vi.fn()
    render(<VoiceModeControls {...defaultProps} onVoiceModeChange={onVoiceModeChange} />)

    const user = userEvent.setup()
    await user.click(screen.getByRole('checkbox', { name: 'Voice mode' }))

    expect(onVoiceModeChange).toHaveBeenCalledWith(true)
  })

  it('shows session required message when voice mode is on without an active session', () => {
    render(<VoiceModeControls {...defaultProps} voiceModeEnabled hasActiveSession={false} />)

    expect(screen.getByText(/select or start a chat before using voice mode/i)).not.toBeNull()
  })

  it('fires mic press start and end on pointer interaction', () => {
    const onMicPressStart = vi.fn()
    const onMicPressEnd = vi.fn()
    render(
      <VoiceModeControls
        {...defaultProps}
        voiceModeEnabled
        onMicPressStart={onMicPressStart}
        onMicPressEnd={onMicPressEnd}
      />,
    )

    const micButton = screen.getByRole('button', { name: 'Hold to speak' })
    fireEvent.pointerDown(micButton)
    expect(onMicPressStart).toHaveBeenCalledTimes(1)

    fireEvent.pointerUp(micButton)
    expect(onMicPressEnd).toHaveBeenCalledTimes(1)
  })

  it('shows interrupt control while assistant is speaking', async () => {
    const onInterrupt = vi.fn()
    render(
      <VoiceModeControls {...defaultProps} voiceModeEnabled isSpeaking onInterrupt={onInterrupt} />,
    )

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Interrupt assistant' }))

    expect(onInterrupt).toHaveBeenCalledTimes(1)
  })

  it('shows live transcript partial caption', () => {
    render(<VoiceModeControls {...defaultProps} voiceModeEnabled transcriptPartial="Hello wor" />)

    expect(screen.getByText('Hello wor')).not.toBeNull()
  })

  it('shows mic error message', () => {
    render(
      <VoiceModeControls {...defaultProps} voiceModeEnabled micError="Microphone access denied." />,
    )

    expect(screen.getByRole('alert').textContent).toContain('Microphone access denied.')
  })
})
