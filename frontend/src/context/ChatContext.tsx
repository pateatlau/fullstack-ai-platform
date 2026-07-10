import { createContext, useContext, useReducer, type Dispatch, type ReactNode } from 'react'
import {
  chatReducer,
  initialChatState,
  type ChatAction,
  type ChatState,
} from '../state/chatReducer'

interface ChatContextValue {
  state: ChatState
  dispatch: Dispatch<ChatAction>
}

const ChatContext = createContext<ChatContextValue | undefined>(undefined)

export function ChatProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(chatReducer, initialChatState)

  return <ChatContext.Provider value={{ state, dispatch }}>{children}</ChatContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components -- context hook pairs with ChatProvider by design
export function useChatContext(): ChatContextValue {
  const context = useContext(ChatContext)
  if (!context) {
    throw new Error('useChatContext must be used within a ChatProvider')
  }
  return context
}
