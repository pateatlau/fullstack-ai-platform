import { render, type RenderOptions } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { AuthProvider } from '../context/AuthContext'
import { ChatProvider } from '../context/ChatContext'

interface WrapperOptions {
  initialRoute?: string
  withChatProvider?: boolean
}

export function renderWithProviders(ui: ReactElement, options: WrapperOptions = {}) {
  const { initialRoute = '/', withChatProvider = false } = options

  function Wrapper({ children }: { children: React.ReactNode }) {
    const content = withChatProvider ? <ChatProvider>{children}</ChatProvider> : children
    return (
      <AuthProvider>
        <MemoryRouter initialEntries={[initialRoute]}>{content}</MemoryRouter>
      </AuthProvider>
    )
  }

  return render(ui, { wrapper: Wrapper } as RenderOptions)
}
