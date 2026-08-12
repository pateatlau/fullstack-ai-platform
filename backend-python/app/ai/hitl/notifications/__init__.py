"""Outbound approval notification providers (Epic 09 recommendation #6)."""

from __future__ import annotations

from app.ai.hitl.notifications.base import (
    ApprovalNotificationEvent,
    ApprovalNotificationEventType,
    NotificationProvider,
)
from app.ai.hitl.notifications.dispatcher import NotificationDispatcher
from app.ai.hitl.notifications.providers import (
    DiscordNotificationProvider,
    EmailNotificationProvider,
    InAppNotificationProvider,
    SlackNotificationProvider,
    TeamsNotificationProvider,
    WebhookNotificationProvider,
)

__all__ = [
    "ApprovalNotificationEvent",
    "ApprovalNotificationEventType",
    "DiscordNotificationProvider",
    "EmailNotificationProvider",
    "InAppNotificationProvider",
    "NotificationDispatcher",
    "NotificationProvider",
    "SlackNotificationProvider",
    "TeamsNotificationProvider",
    "WebhookNotificationProvider",
]
