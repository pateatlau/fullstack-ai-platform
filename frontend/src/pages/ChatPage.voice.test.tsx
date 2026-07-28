/* @vitest-environment jsdom */

import { act, cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatPage } from './ChatPage'
import { storeSession } from '../auth/tokenStorage'
import { renderWithProviders } from '../test/renderWithProviders'
import { jsonHealthResponse } from '../test/chatFetchStubs'
import type { AuthenticatedUser } from '../types/auth'

const authenticatedUser: AuthenticatedUser = {
  id: 'user-1',
  email: 'person@example.com',
  display_name: 'Person',
  picture_url: null,
}

function makeJwt(expSecondsFromNow: number): string {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))
  const exp = Math.floor(Date.now() / 1000) + expSecondsFromNow
  const payload = btoa(JSON.stringify({ exp }))
  return `${header}.${payload}.signature`
}

const mockConnect = vi.fn().mockResolvedValue(undefined)
const mockDisconnect = vi.fn()
const mockStartRecording = vi.fn().mockResolvedValue(undefined)
const mockStopRecording = vi.fn()
const mockInterrupt = vi.fn()

let voiceCallbacks: {
  useWebSearch?: boolean
  useDocuments?: boolean
  onTranscriptPartial?: (text: string) => void
  onTranscriptFinal?: (text: string) => void
  onStart?: () => void
  onDelta?: (text: string) => void
  onEnd?: (metadata: {
    tools_used?: string[] | null
    retrieved_chunk_count?: number | null
  }) => void
  onInterrupted?: () => void
} = {}

vi.mock('../hooks/useVoiceSession', () => ({
  useVoiceSession: (options: typeof voiceCallbacks & Record<string, unknown>) => {
    voiceCallbacks = options
    return {
      connect: mockConnect,
      disconnect: mockDisconnect,
      startRecording: mockStartRecording,
      stopRecording: mockStopRecording,
      interrupt: mockInterrupt,
      isConnected: true,
      isRecording: false,
      isSpeaking: false,
      voiceSessionId: 'voice-1',
    }
  },
}))

function signInAsAuthenticatedUser(): void {
  storeSession(makeJwt(3600), authenticatedUser)
}

type FetchMock = (input: RequestInfo | URL, init?: RequestInit) => unknown

function withVoiceEnabledFetchStub(
  chatFetchMock: FetchMock,
  voiceEnabled: boolean,
  flags?: { toolsEnabled?: boolean; ragEnabled?: boolean },
): ReturnType<typeof vi.fn> {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    const method = init?.method ?? 'GET'

    if (url.endsWith('/api/health') && method === 'GET') {
      return jsonHealthResponse(
        true,
        flags?.toolsEnabled ?? false,
        flags?.ragEnabled ?? false,
        voiceEnabled,
      )
    }

    return chatFetchMock(input, init)
  })
}

function sessionsFetchMock(): ReturnType<typeof vi.fn> {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    const method = init?.method ?? 'GET'

    if (url.endsWith('/api/chat/sessions') && method === 'GET') {
      return new Response(
        JSON.stringify([
          {
            id: 'session-1',
            title: 'Voice chat',
            last_message_at: '2026-01-01T00:00:00Z',
            created_at: '2026-01-01T00:00:00Z',
          },
        ]),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )
    }

    if (url.endsWith('/api/chat/sessions/session-1') && method === 'GET') {
      return new Response(
        JSON.stringify({
          id: 'session-1',
          title: 'Voice chat',
          last_message_at: '2026-01-01T00:00:00Z',
          messages: [],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )
    }

    return new Response(JSON.stringify({ error: 'unexpected' }), { status: 404 })
  })
}

describe('ChatPage voice integration', () => {
  beforeEach(() => {
    window.localStorage.clear()
    signInAsAuthenticatedUser()
    mockConnect.mockClear()
    mockDisconnect.mockClear()
    mockStartRecording.mockClear()
    mockStopRecording.mockClear()
    mockInterrupt.mockClear()
    voiceCallbacks = {}
    Object.defineProperty(globalThis.HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    })
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    window.localStorage.clear()
  })

  it('hides voice mode toggle when voice_enabled is false', async () => {
    vi.stubGlobal('fetch', withVoiceEnabledFetchStub(sessionsFetchMock() as FetchMock, false))

    renderWithProviders(<ChatPage />)

    await waitFor(() => {
      expect(screen.queryByRole('checkbox', { name: 'Voice mode' })).toBeNull()
    })
  })

  it('shows voice mode toggle when voice_enabled is true for authenticated users', async () => {
    vi.stubGlobal('fetch', withVoiceEnabledFetchStub(sessionsFetchMock() as FetchMock, true))

    renderWithProviders(<ChatPage />)

    await waitFor(() => {
      expect(screen.getByRole('checkbox', { name: 'Voice mode' })).not.toBeNull()
    })
  })

  it('connects voice session when voice mode is enabled', async () => {
    vi.stubGlobal('fetch', withVoiceEnabledFetchStub(sessionsFetchMock() as FetchMock, true))

    renderWithProviders(<ChatPage />)

    await waitFor(() => {
      expect(screen.getByRole('checkbox', { name: 'Voice mode' })).not.toBeNull()
      expect(screen.getByText('Voice chat')).not.toBeNull()
    })

    const user = userEvent.setup()
    await user.click(screen.getByRole('checkbox', { name: 'Voice mode' }))

    await waitFor(() => {
      expect(mockConnect).toHaveBeenCalledTimes(1)
    })
  })

  it('maps voice transcript and assistant events into the message list', async () => {
    vi.stubGlobal('fetch', withVoiceEnabledFetchStub(sessionsFetchMock() as FetchMock, true))

    renderWithProviders(<ChatPage />)

    await waitFor(() => {
      expect(screen.getByRole('checkbox', { name: 'Voice mode' })).not.toBeNull()
      expect(screen.getByText('Voice chat')).not.toBeNull()
    })

    const user = userEvent.setup()
    await user.click(screen.getByRole('checkbox', { name: 'Voice mode' }))

    await waitFor(() => {
      expect(mockConnect).toHaveBeenCalled()
    })

    act(() => {
      voiceCallbacks.onTranscriptPartial?.('Hel')
    })
    await waitFor(() => {
      expect(screen.getByText('Hel')).not.toBeNull()
    })

    act(() => {
      voiceCallbacks.onTranscriptFinal?.('Hello voice')
    })
    await waitFor(() => {
      expect(screen.getByText('Hello voice')).not.toBeNull()
    })

    act(() => {
      voiceCallbacks.onStart?.()
      voiceCallbacks.onDelta?.('Hi ')
      voiceCallbacks.onDelta?.('there')
      voiceCallbacks.onEnd?.({ tools_used: null, retrieved_chunk_count: null })
    })

    await waitFor(() => {
      expect(screen.getByText('Hi there')).not.toBeNull()
    })
  })

  it('disables text send while voice mode is active', async () => {
    vi.stubGlobal('fetch', withVoiceEnabledFetchStub(sessionsFetchMock() as FetchMock, true))

    renderWithProviders(<ChatPage />)

    await waitFor(() => {
      expect(screen.getByText('Voice chat')).not.toBeNull()
    })

    const user = userEvent.setup()
    await user.click(screen.getByRole('checkbox', { name: 'Voice mode' }))

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Send' })).toBeNull()
      expect(
        (screen.getByPlaceholderText('Voice mode — hold mic to speak') as HTMLTextAreaElement)
          .disabled,
      ).toBe(true)
    })
  })

  it('renders assistant markdown once a voice turn completes', async () => {
    vi.stubGlobal('fetch', withVoiceEnabledFetchStub(sessionsFetchMock() as FetchMock, true))

    renderWithProviders(<ChatPage />)

    await waitFor(() => {
      expect(screen.getByRole('checkbox', { name: 'Voice mode' })).not.toBeNull()
      expect(screen.getByText('Voice chat')).not.toBeNull()
    })

    const user = userEvent.setup()
    await user.click(screen.getByRole('checkbox', { name: 'Voice mode' }))

    act(() => {
      voiceCallbacks.onStart?.()
      voiceCallbacks.onDelta?.('Try **bold** and [docs](https://example.com).')
      voiceCallbacks.onEnd?.({ tools_used: null, retrieved_chunk_count: null })
    })

    await waitFor(() => {
      expect(screen.getByText('bold').tagName).toBe('STRONG')
      expect(screen.getByRole('link', { name: 'docs' }).getAttribute('href')).toBe(
        'https://example.com',
      )
    })
  })

  it('keeps tool toggles editable in voice mode and reconnects with the new options', async () => {
    vi.stubGlobal(
      'fetch',
      withVoiceEnabledFetchStub(sessionsFetchMock() as FetchMock, true, {
        toolsEnabled: true,
        ragEnabled: true,
      }),
    )

    renderWithProviders(<ChatPage />)

    await waitFor(() => {
      expect(screen.getByRole('checkbox', { name: 'Voice mode' })).not.toBeNull()
      expect(screen.getByText('Voice chat')).not.toBeNull()
    })

    const user = userEvent.setup()
    await user.click(screen.getByRole('checkbox', { name: 'Voice mode' }))

    await waitFor(() => {
      expect(mockConnect).toHaveBeenCalledTimes(1)
    })

    const webSearch = screen.getByRole('checkbox', { name: 'Web search' }) as HTMLInputElement
    const documents = screen.getByRole('checkbox', { name: 'My documents' }) as HTMLInputElement
    expect(webSearch.disabled).toBe(false)
    expect(documents.disabled).toBe(false)

    await user.click(webSearch)

    // Turn options are fixed at the WS handshake, so a change must reconnect.
    await waitFor(() => {
      expect(voiceCallbacks.useWebSearch).toBe(true)
      expect(mockConnect).toHaveBeenCalledTimes(2)
    })
  })

  it('marks assistant message stopped after voice interrupt callback', async () => {
    vi.stubGlobal('fetch', withVoiceEnabledFetchStub(sessionsFetchMock() as FetchMock, true))

    renderWithProviders(<ChatPage />)

    await waitFor(() => {
      expect(screen.getByRole('checkbox', { name: 'Voice mode' })).not.toBeNull()
      expect(screen.getByText('Voice chat')).not.toBeNull()
    })

    const user = userEvent.setup()
    await user.click(screen.getByRole('checkbox', { name: 'Voice mode' }))

    act(() => {
      voiceCallbacks.onStart?.()
      voiceCallbacks.onDelta?.('Speaking')
    })

    await waitFor(() => {
      expect(screen.getByText('Speaking')).not.toBeNull()
    })

    act(() => {
      voiceCallbacks.onInterrupted?.()
    })

    await waitFor(() => {
      expect(screen.getByText('Stopped.')).not.toBeNull()
    })
  })
})
