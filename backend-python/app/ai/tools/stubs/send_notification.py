"""Reference sensitive tool for HITL eval and integration tests (no side effects)."""

from __future__ import annotations

from app.ai.tools.schemas import (
    ToolDefinition,
    ToolExecutionContext,
    ToolResult,
)

SEND_NOTIFICATION_TOOL_NAME = "send_notification"

SEND_NOTIFICATION_TOOL_DEFINITION = ToolDefinition(
    name=SEND_NOTIFICATION_TOOL_NAME,
    description="Send a notification (reference stub — records in memory only)",
    parameters={
        "type": "object",
        "properties": {
            "message": {"type": "string"},
            "channel": {"type": "string"},
        },
        "required": ["message"],
    },
    requires_approval=True,
)


class SendNotificationHandler:
    """Record sent notifications for test assertions."""

    sent_messages: list[dict[str, object]] = []

    @classmethod
    def reset(cls) -> None:
        cls.sent_messages = []

    async def execute(
        self,
        args: dict[str, object],
        context: ToolExecutionContext,
    ) -> ToolResult:
        del context
        message = str(args["message"])
        channel = str(args.get("channel", "in_app"))
        payload: dict[str, object] = {"message": message, "channel": channel}
        SendNotificationHandler.sent_messages.append(payload)
        return ToolResult(success=True, data={"sent": True, **payload})
