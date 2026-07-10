import type { ChatChunk } from '../types/chat'

export interface SseFrame {
  event: string
  data: ChatChunk
}

/**
 * Incrementally parses an SSE byte/text stream into typed `ChatChunk` frames.
 *
 * Feed it arbitrarily-sized text chunks (as decoded from the network) via
 * `feed()`; it buffers partial frames until a full `\n\n`-terminated frame
 * is available, then parses the `data:` line as JSON.
 */
export class SseParser {
  private buffer = ''

  feed(chunk: string): SseFrame[] {
    this.buffer += chunk
    const frames: SseFrame[] = []

    let boundary = this.buffer.indexOf('\n\n')
    while (boundary !== -1) {
      const rawFrame = this.buffer.slice(0, boundary)
      this.buffer = this.buffer.slice(boundary + 2)

      const frame = parseFrame(rawFrame)
      if (frame) {
        frames.push(frame)
      }

      boundary = this.buffer.indexOf('\n\n')
    }

    return frames
  }
}

function parseFrame(raw: string): SseFrame | null {
  let event = 'message'
  let dataLine: string | null = null

  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) {
      event = line.slice('event:'.length).trim()
    } else if (line.startsWith('data:')) {
      dataLine = line.slice('data:'.length).trim()
    }
  }

  if (dataLine === null) {
    return null
  }

  const data = JSON.parse(dataLine) as ChatChunk
  return { event, data }
}
