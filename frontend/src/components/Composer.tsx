import { useState, type FormEvent, type KeyboardEvent } from 'react'

interface ComposerProps {
  onSend: (content: string) => void
  onStop: () => void
  isStreaming: boolean
}

export function Composer({ onSend, onStop, isStreaming }: ComposerProps) {
  const [value, setValue] = useState('')

  const submit = () => {
    const trimmed = value.trim()
    if (!trimmed || isStreaming) return
    onSend(trimmed)
    setValue('')
  }

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault()
    submit()
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  return (
    <form className="composer" onSubmit={handleSubmit}>
      <textarea
        className="composer__input"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Ask something…"
        disabled={isStreaming}
        rows={2}
      />
      {isStreaming ? (
        <button type="button" className="composer__stop" onClick={onStop}>
          Stop
        </button>
      ) : (
        <button type="submit" className="composer__send" disabled={!value.trim()}>
          Send
        </button>
      )}
    </form>
  )
}
