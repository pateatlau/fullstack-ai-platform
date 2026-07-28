# 413 Error Fix Proposal

## Summary

When the user sends a chat input that is too large, the backend correctly rejects the request with HTTP `413`. The frontend then gets stuck in a bad conversation state: every later chat request includes the rejected oversized user message in the conversation history, so the backend keeps returning `413` until the page is refreshed.

The primary fix should be in the frontend state flow, not in the backend status handling.

## Observed Behavior

1. The user submits a message that exceeds the backend request-size limit.
2. The backend returns:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request body exceeds the 16384 byte limit. Reduce message size and retry."
  }
}
```

with status `413`. 3. The frontend shows the error. 4. Subsequent chat attempts also fail until refresh.

## Root Cause

The problem is caused by the frontend conversation model.

### Controlling code path

In [frontend/src/pages/ChatPage.tsx](frontend/src/pages/ChatPage.tsx), `handleSend()` does this in order:

1. creates the new user message
2. immediately dispatches `ADD_USER_MESSAGE`
3. builds the outbound request from `state.messages` plus that new user message
4. starts the streaming request

That means the new user message is committed to local conversation state before the backend accepts it.

If the backend rejects the request before the stream starts, the failed user message still remains inside `state.messages`.

Later sends rebuild history from the full local message list, so the oversized user message is resent on every request.

### Why refresh fixes it

Refresh clears the in-memory chat state. Once the oversized message disappears from `state.messages`, later requests succeed again.

## Evidence

- In [frontend/src/pages/ChatPage.tsx](frontend/src/pages/ChatPage.tsx), `handleSend()` appends the user message before request success is known.
- In [frontend/src/pages/ChatPage.tsx](frontend/src/pages/ChatPage.tsx), pre-stream `ChatApiError` handling only sets a top-level error banner when there is no assistant message id yet; it does not roll back the failed user message.
- In [frontend/src/hooks/useChatStream.ts](frontend/src/hooks/useChatStream.ts), a non-OK HTTP response is converted into `ChatApiError`, so `413` is treated as a normal request failure rather than a streaming failure.
- In `backend-python/app/main.py`, the request-size middleware intentionally returns `413` before request handling when the payload exceeds the configured byte limit.

## Proposed Fix

## Fix Goal

Do not let a request that failed before streaming began permanently poison conversation history.

## Recommended approach

Add an explicit rollback path for the optimistically appended user message when the request fails before the backend emits a `start` event.

### Behavior change

When a send fails before `onStart` runs:

- remove the just-added user message from `state.messages`
- show the backend error message in the top-level banner
- keep the composer usable
- do not create an assistant placeholder message

This preserves the current optimistic UX for successful sends while ensuring rejected messages are not retained as part of history.

## Minimal implementation shape

### 1. Add a reducer action to remove a message by id

In [frontend/src/state/chatReducer.ts](frontend/src/state/chatReducer.ts):

- add something like `REMOVE_MESSAGE`
- filter the message array by id

### 2. Track the optimistic user message id for the active send

In [frontend/src/pages/ChatPage.tsx](frontend/src/pages/ChatPage.tsx):

- store the just-added user message id in a ref when `handleSend()` starts a request
- clear that ref once `onStart` fires, because after `start` the request is considered accepted for UI purposes

### 3. Roll back the user message on pre-start failure

In the `onError` path in [frontend/src/pages/ChatPage.tsx](frontend/src/pages/ChatPage.tsx):

- if the error happens before `onStart`
- and the current request has an optimistic user message id
- dispatch `REMOVE_MESSAGE` for that user message
- then surface the `ChatApiError` message normally

### 4. Preserve current streaming failure behavior

Do not roll back the user message if streaming already started.

After `start`, the user message is part of a valid accepted conversation turn. From that point onward, existing behaviors remain correct:

- partial assistant output may be interrupted
- assistant retries should still work
- user messages already accepted by the backend should remain in history

## Why this is the right fix

This addresses the actual bad state rather than just the visible symptom.

The backend is correct to reject oversized payloads. The bug is that the frontend treats a rejected send as if it were a committed conversation turn.

If we only change the backend, the frontend would still be vulnerable to the same class of bug for any other pre-start failure that rejects the request after the user message is optimistically appended, such as:

- `422` validation errors
- `429` rate limits
- `500` errors before streaming starts

So the rollback behavior should apply to any request that fails before `start`, not only `413`.

## Optional Hardening

### Client-side size guard

After the rollback fix, add a client-side guard to reduce avoidable round trips.

Suggested behavior:

- estimate the request payload size before sending
- if it is clearly above the backend limit, block the send locally
- show a user-friendly error before issuing `fetch`

This is useful UX, but it should be treated as secondary. The frontend still needs rollback logic because backend rejection can happen for reasons the client cannot predict exactly.

### Backend connection hardening

There is also a backend hardening improvement worth making later.

In `backend-python/app/main.py`, the middleware returns early when `Content-Length` already exceeds the limit. In HTTP keep-alive scenarios, early rejection without consuming the body can sometimes create connection reuse edge cases depending on the server stack.

That is not the primary cause of the current bug, because the frontend state flow already explains why refresh clears the issue. Still, the backend should eventually be reviewed to ensure the oversized request body is either:

- safely drained before reusing the connection, or
- rejected with explicit connection close semantics

That should be treated as protocol hardening, not the first fix.

## Acceptance Criteria

1. Sending an oversized message returns an error banner but does not leave the failed user message in conversation state.
2. The next normal-sized message succeeds without refreshing the page.
3. Existing streaming behavior is unchanged for successful sends.
4. Mid-stream failures still preserve the user message and partial assistant message.
5. Pre-start failures of other kinds, not just `413`, also do not poison future history.

## Suggested Tests

### Frontend unit tests

- reducer test for `REMOVE_MESSAGE`
- page-level or hook-integrated test that simulates:
  - optimistic user message append
  - `ChatApiError` before `onStart`
  - rollback of the user message
  - successful next send using clean history

### Manual verification

1. Send a payload larger than the backend limit.
2. Confirm the UI shows the validation error.
3. Confirm the failed user message is not left in the message list or reused in history.
4. Send a short message immediately afterward.
5. Confirm the response streams normally without refreshing.

## Recommended Order

1. Implement the frontend rollback fix.
2. Add regression tests for pre-start failures.
3. Optionally add a client-side request-size precheck.
4. Later, harden backend `413` connection behavior if needed.
