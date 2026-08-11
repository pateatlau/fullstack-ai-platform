/* @vitest-environment jsdom */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ApprovalsApiError,
  fetchApprovals,
  rejectApproval,
  streamApproveApproval,
} from './approvalsClient'
import { storeSession } from '../auth/tokenStorage'
import type { AuthenticatedUser } from '../types/auth'

const user: AuthenticatedUser = {
  id: 'user-1',
  email: 'person@example.com',
  display_name: 'Person',
  picture_url: null,
}

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

const sampleList = {
  approvals: [
    {
      id: 'approval-1',
      kind: 'agent_tool',
      approval_correlation_id: 'corr-1',
      status: 'pending',
      tool_calls: [{ name: 'send_notification', arguments: { message: 'hi' }, call_id: 'c1' }],
      workflow_run_id: null,
      workflow_node_id: null,
      session_id: 'session-1',
      requested_at: '2026-08-12T00:00:00Z',
      decided_at: null,
      decided_by: null,
      decision: null,
      reason: null,
      edited: false,
      revision_count: 0,
      decide_url: '/api/approvals/approval-1/decide',
    },
  ],
  limit: 50,
  offset: 0,
  total: 1,
}

describe('approvalsClient', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('fetchApprovals sends Bearer token', async () => {
    storeSession('approvals-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(sampleList))
    vi.stubGlobal('fetch', fetchMock)

    const result = await fetchApprovals({ status: 'pending' })

    expect(result.approvals).toHaveLength(1)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/approvals?status=pending',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer approvals-jwt' }),
      }),
    )
  })

  it('fetchApprovals throws ApprovalsApiError on feature_disabled', async () => {
    storeSession('approvals-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(
        {
          error: {
            code: 'feature_disabled',
            message: 'Human-in-the-loop approvals are not enabled on this server.',
          },
        },
        503,
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchApprovals()).rejects.toMatchObject({
      name: 'ApprovalsApiError',
      code: 'feature_disabled',
      status: 503,
    } satisfies Partial<ApprovalsApiError>)
  })

  it('rejectApproval posts JSON body', async () => {
    storeSession('approvals-jwt', user)
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        approval_id: 'approval-1',
        approval_kind: 'agent_tool',
        status: 'rejected',
        edited: false,
        final_payload: null,
        reason: 'no',
        approver: 'user-1',
        decided_at: '2026-08-12T00:01:00Z',
        approval_correlation_id: 'corr-1',
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await rejectApproval('approval-1', 'no')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/approvals/approval-1/decide',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ decision: 'rejected', reason: 'no' }),
      }),
    )
  })

  it('streamApproveApproval parses SSE delta frames', async () => {
    storeSession('approvals-jwt', user)
    const encoder = new TextEncoder()
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(
              encoder.encode(
                [
                  'event: delta',
                  'data: {"type":"delta","id":"msg-1","content":"Done.","timestamp":"t0"}',
                  '',
                  '',
                  'event: end',
                  'data: {"type":"end","id":"msg-1","finish_reason":"stop","timestamp":"t1"}',
                  '',
                  '',
                ].join('\n'),
              ),
            )
            controller.close()
          },
        }),
        { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    const onDelta = vi.fn()
    const onEnd = vi.fn()

    await streamApproveApproval(
      'approval-1',
      { edited_calls: [{ name: 'echo', arguments: {}, call_id: 'c1' }] },
      { onDelta, onEnd },
    )

    expect(onDelta).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'delta', content: 'Done.' }),
    )
    expect(onEnd).toHaveBeenCalled()
  })
})
