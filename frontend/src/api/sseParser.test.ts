import { describe, expect, it } from 'vitest'
import { SseParser } from './sseParser'

describe('SseParser', () => {
  it('parses a single frame delivered in one chunk', () => {
    const parser = new SseParser()

    const frames = parser.feed(
      'event: start\ndata: {"type":"start","id":"resp_1","timestamp":"t0"}\n\n',
    )

    expect(frames).toEqual([
      {
        event: 'start',
        data: { type: 'start', id: 'resp_1', timestamp: 't0' },
      },
    ])
  })

  it('buffers a partial frame until the terminator arrives', () => {
    const parser = new SseParser()

    const first = parser.feed('event: delta\ndata: {"type":"delta"')
    expect(first).toEqual([])

    const second = parser.feed(',"id":"resp_1","content":"x","timestamp":"t"}\n\n')
    expect(second).toEqual([
      {
        event: 'delta',
        data: { type: 'delta', id: 'resp_1', content: 'x', timestamp: 't' },
      },
    ])
  })

  it('parses multiple frames split across arbitrary chunk boundaries', () => {
    const parser = new SseParser()

    const full =
      'event: start\ndata: {"type":"start","id":"resp_1","timestamp":"t0"}\n\n' +
      'event: delta\ndata: {"type":"delta","id":"resp_1","content":"Fast","timestamp":"t1"}\n\n' +
      'event: end\ndata: {"type":"end","id":"resp_1","finish_reason":"stop","timestamp":"t2"}\n\n'

    // Split at byte offsets that land in the middle of frames/JSON payloads,
    // not on frame boundaries, to exercise the buffering logic.
    const splitPoints = [10, 45, 90, 130]
    const chunks: string[] = []
    let start = 0
    for (const point of splitPoints) {
      chunks.push(full.slice(start, point))
      start = point
    }
    chunks.push(full.slice(start))

    const frames = chunks.flatMap((chunk) => parser.feed(chunk))

    expect(frames).toEqual([
      {
        event: 'start',
        data: { type: 'start', id: 'resp_1', timestamp: 't0' },
      },
      {
        event: 'delta',
        data: { type: 'delta', id: 'resp_1', content: 'Fast', timestamp: 't1' },
      },
      {
        event: 'end',
        data: {
          type: 'end',
          id: 'resp_1',
          finish_reason: 'stop',
          timestamp: 't2',
        },
      },
    ])
  })

  it('parses tool lifecycle frames', () => {
    const parser = new SseParser()

    const frames = parser.feed(
      'event: tool_start\ndata: {"type":"tool_start","id":"resp_1","tool_name":"web_search","call_id":"call-1","timestamp":"t0"}\n\n' +
        'event: tool_end\ndata: {"type":"tool_end","id":"resp_1","tool_name":"web_search","call_id":"call-1","success":true,"timestamp":"t1"}\n\n',
    )

    expect(frames).toEqual([
      {
        event: 'tool_start',
        data: {
          type: 'tool_start',
          id: 'resp_1',
          tool_name: 'web_search',
          call_id: 'call-1',
          timestamp: 't0',
        },
      },
      {
        event: 'tool_end',
        data: {
          type: 'tool_end',
          id: 'resp_1',
          tool_name: 'web_search',
          call_id: 'call-1',
          success: true,
          timestamp: 't1',
        },
      },
    ])
  })

  it('ignores frames with no data line', () => {
    const parser = new SseParser()

    const frames = parser.feed('event: ping\n\n')

    expect(frames).toEqual([])
  })
})
