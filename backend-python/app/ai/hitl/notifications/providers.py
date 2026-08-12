"""Concrete outbound notification providers (Epic 09 recommendation #6).

Each provider is a thin adapter around a single webhook POST; they differ
only in payload shape. Providers never raise — transport failures are
logged and swallowed here so a slow/unreachable webhook can never block or
fail an approval decision (the caller is :class:`NotificationDispatcher`,
which also isolates failures per-provider, but each provider is defensive
on its own too so it stays safe to use standalone).
"""

from __future__ import annotations

import httpx

from app.ai.hitl.notifications.base import ApprovalNotificationEvent
from app.core.logging import get_logger

_logger = get_logger(__name__)


class _HttpWebhookProvider:
    """POST a JSON payload to a configured webhook URL."""

    def __init__(
        self,
        *,
        webhook_url: str,
        provider_name: str,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._webhook_url = webhook_url
        self._provider_name = provider_name
        self._timeout_seconds = timeout_seconds

    def _build_payload(self, event: ApprovalNotificationEvent) -> dict[str, object]:
        """Generic JSON payload; overridden by chat-style providers."""
        return {
            "event_type": event.event_type.value,
            "approval_id": str(event.approval_id),
            "approval_kind": event.approval_kind.value,
            "occurred_at": event.occurred_at.isoformat(),
            "summary": event.summary,
            "metadata": event.metadata,
        }

    async def notify(self, event: ApprovalNotificationEvent) -> None:
        payload = self._build_payload(event)
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(self._webhook_url, json=payload)
                response.raise_for_status()
        except Exception:
            _logger.warning(
                "HITL notification delivery failed",
                provider=self._provider_name,
                approval_id=str(event.approval_id),
                event_type=event.event_type.value,
                exc_info=True,
            )


class WebhookNotificationProvider(_HttpWebhookProvider):
    """Generic outbound webhook (arbitrary consumer, opaque JSON body)."""

    def __init__(self, *, webhook_url: str, timeout_seconds: float = 5.0) -> None:
        super().__init__(
            webhook_url=webhook_url,
            provider_name="webhook",
            timeout_seconds=timeout_seconds,
        )


class SlackNotificationProvider(_HttpWebhookProvider):
    """Slack incoming webhook (``{"text": ...}`` payload)."""

    def __init__(self, *, webhook_url: str, timeout_seconds: float = 5.0) -> None:
        super().__init__(
            webhook_url=webhook_url,
            provider_name="slack",
            timeout_seconds=timeout_seconds,
        )

    def _build_payload(self, event: ApprovalNotificationEvent) -> dict[str, object]:
        return {"text": f"[{event.event_type.value}] {event.summary}"}


class TeamsNotificationProvider(_HttpWebhookProvider):
    """Microsoft Teams incoming webhook (simple text card)."""

    def __init__(self, *, webhook_url: str, timeout_seconds: float = 5.0) -> None:
        super().__init__(
            webhook_url=webhook_url,
            provider_name="teams",
            timeout_seconds=timeout_seconds,
        )

    def _build_payload(self, event: ApprovalNotificationEvent) -> dict[str, object]:
        return {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "title": f"Approval {event.event_type.value}",
            "text": event.summary,
        }


class DiscordNotificationProvider(_HttpWebhookProvider):
    """Discord incoming webhook (``{"content": ...}`` payload)."""

    def __init__(self, *, webhook_url: str, timeout_seconds: float = 5.0) -> None:
        super().__init__(
            webhook_url=webhook_url,
            provider_name="discord",
            timeout_seconds=timeout_seconds,
        )

    def _build_payload(self, event: ApprovalNotificationEvent) -> dict[str, object]:
        return {"content": f"**[{event.event_type.value}]** {event.summary}"}


class EmailNotificationProvider:
    """Placeholder email adapter.

    No SMTP/transactional-email infrastructure exists in this project yet;
    this logs a structured "would send" record so the provider interface
    and dispatcher wiring are exercised end-to-end. Swap the body of
    :meth:`notify` for a real provider (SES, SendGrid, ...) when available.
    """

    def __init__(self, *, recipient: str | None = None) -> None:
        self._recipient = recipient

    async def notify(self, event: ApprovalNotificationEvent) -> None:
        _logger.info(
            "HITL email notification (not sent — no email provider configured)",
            recipient=self._recipient,
            approval_id=str(event.approval_id),
            event_type=event.event_type.value,
            summary=event.summary,
        )


class InAppNotificationProvider:
    """Structured-log adapter for the existing in-app (SSE + inbox) surface.

    The real-time UI signal already exists via
    ``AgentStreamEvent.approval_required`` and the ``GET /api/approvals``
    inbox; this provider exists only so ``in_app`` can be listed alongside
    the other channels in ``hitl_notification_providers`` for consistent
    dispatcher fan-out/observability without duplicating delivery.
    """

    async def notify(self, event: ApprovalNotificationEvent) -> None:
        _logger.info(
            "HITL in-app notification",
            approval_id=str(event.approval_id),
            approval_kind=event.approval_kind.value,
            event_type=event.event_type.value,
            summary=event.summary,
        )
