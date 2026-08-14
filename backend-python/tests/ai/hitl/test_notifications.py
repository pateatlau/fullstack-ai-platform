"""Outbound approval notification tests (Epic 09 recommendation #6)."""

from __future__ import annotations

import asyncio
import datetime
import time
import uuid

import httpx
import pytest

from app.ai.hitl.models import ApprovalKind, ProposedToolCall
from app.ai.hitl.notifications import (
    ApprovalNotificationEvent,
    ApprovalNotificationEventType,
    DiscordNotificationProvider,
    EmailNotificationProvider,
    InAppNotificationProvider,
    NotificationDispatcher,
    SlackNotificationProvider,
    TeamsNotificationProvider,
    WebhookNotificationProvider,
)
from app.ai.hitl.service import AgentApprovalService
from app.ai.agent.scratchpad import ScratchpadStore
from tests.ai.hitl.fakes import InMemoryApprovalStore
from tests.fakes import FakeChatStore


def _event(
    event_type: ApprovalNotificationEventType = ApprovalNotificationEventType.REQUESTED,
) -> ApprovalNotificationEvent:
    return ApprovalNotificationEvent(
        event_type=event_type,
        approval_id=uuid.uuid4(),
        approval_kind=ApprovalKind.AGENT_TOOL,
        occurred_at=datetime.datetime.now(datetime.UTC),
        summary="Approval requested for 1 tool call(s).",
        metadata={"execution_id": "exec-1"},
    )


async def _drain_background_notifications() -> None:
    await asyncio.sleep(0.01)


class _RecordingProvider:
    def __init__(self) -> None:
        self.events: list[ApprovalNotificationEvent] = []

    async def notify(self, event: ApprovalNotificationEvent) -> None:
        self.events.append(event)


class _RaisingProvider:
    async def notify(self, event: ApprovalNotificationEvent) -> None:
        del event
        raise RuntimeError("boom")


class _SlowProvider:
    def __init__(self, delay_seconds: float) -> None:
        self.delay_seconds = delay_seconds
        self.events: list[ApprovalNotificationEvent] = []

    async def notify(self, event: ApprovalNotificationEvent) -> None:
        await asyncio.sleep(self.delay_seconds)
        self.events.append(event)


class TestNotificationDispatcher:
    @pytest.mark.anyio
    async def test_fans_out_to_every_provider(self) -> None:
        first, second = _RecordingProvider(), _RecordingProvider()
        dispatcher = NotificationDispatcher([first, second])
        event = _event()

        await dispatcher.dispatch(event)
        await _drain_background_notifications()

        assert first.events == [event]
        assert second.events == [event]

    @pytest.mark.anyio
    async def test_one_provider_failure_does_not_block_others(self) -> None:
        recording = _RecordingProvider()
        dispatcher = NotificationDispatcher([_RaisingProvider(), recording])
        event = _event()

        await dispatcher.dispatch(event)
        await _drain_background_notifications()

        assert recording.events == [event]

    @pytest.mark.anyio
    async def test_dispatch_returns_without_waiting_for_slow_providers(self) -> None:
        slow_a = _SlowProvider(delay_seconds=0.1)
        slow_b = _SlowProvider(delay_seconds=0.1)
        dispatcher = NotificationDispatcher([slow_a, slow_b])
        event = _event()

        started = time.monotonic()
        await dispatcher.dispatch(event)
        elapsed = time.monotonic() - started

        assert elapsed < 0.05
        await asyncio.sleep(0.15)
        assert slow_a.events == [event]
        assert slow_b.events == [event]

    @pytest.mark.anyio
    async def test_no_providers_is_a_no_op(self) -> None:
        dispatcher = NotificationDispatcher()
        await dispatcher.dispatch(_event())


class TestHttpWebhookProviders:
    @pytest.mark.anyio
    async def test_webhook_provider_posts_generic_payload(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        class _StubResponse:
            def raise_for_status(self) -> None:
                return None

        class _StubClient:
            async def __aenter__(self) -> "_StubClient":
                return self

            async def __aexit__(self, *exc_info: object) -> None:
                return None

            async def post(self, url: str, *, json: dict[str, object]) -> _StubResponse:
                captured["url"] = url
                captured["json"] = json
                return _StubResponse()

        monkeypatch.setattr(httpx, "AsyncClient", lambda timeout: _StubClient())
        provider = WebhookNotificationProvider(webhook_url="https://example.com/hook")

        await provider.notify(_event())

        assert captured["url"] == "https://example.com/hook"
        payload = captured["json"]
        assert isinstance(payload, dict)
        assert payload["event_type"] == "requested"

    @pytest.mark.anyio
    async def test_slack_provider_builds_text_payload(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        class _StubResponse:
            def raise_for_status(self) -> None:
                return None

        class _StubClient:
            async def __aenter__(self) -> "_StubClient":
                return self

            async def __aexit__(self, *exc_info: object) -> None:
                return None

            async def post(self, url: str, *, json: dict[str, object]) -> _StubResponse:
                captured["json"] = json
                return _StubResponse()

        monkeypatch.setattr(httpx, "AsyncClient", lambda timeout: _StubClient())
        provider = SlackNotificationProvider(webhook_url="https://hooks.slack.com/x")

        await provider.notify(_event())

        assert "text" in captured["json"]  # type: ignore[operator]

    @pytest.mark.anyio
    async def test_teams_and_discord_providers_never_raise_on_transport_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _FailingClient:
            async def __aenter__(self) -> "_FailingClient":
                return self

            async def __aexit__(self, *exc_info: object) -> None:
                return None

            async def post(self, url: str, *, json: dict[str, object]) -> None:
                del url, json
                raise httpx.ConnectError("unreachable")

        monkeypatch.setattr(httpx, "AsyncClient", lambda timeout: _FailingClient())

        await TeamsNotificationProvider(
            webhook_url="https://outlook.example/hook"
        ).notify(_event())
        await DiscordNotificationProvider(
            webhook_url="https://discord.example/hook"
        ).notify(_event())


class TestPlaceholderProviders:
    def test_hitl_email_provider_rejected_in_settings(self) -> None:
        from app.core.config import Settings

        with pytest.raises(
            ValueError, match="Unsupported HITL_NOTIFICATION_PROVIDERS entry 'email'"
        ):
            Settings(
                openai_api_key="test-key",
                hitl_notification_providers=["email"],
            ).validate_hitl_requirements()

    @pytest.mark.anyio
    async def test_email_provider_raises_until_delivery_implemented(self) -> None:
        with pytest.raises(
            NotImplementedError, match="email delivery is not implemented"
        ):
            await EmailNotificationProvider(recipient="ops@example.com").notify(
                _event()
            )

    @pytest.mark.anyio
    async def test_in_app_provider_never_raises(self) -> None:
        await InAppNotificationProvider().notify(_event())


class TestServiceDispatchesNotifications:
    @pytest.mark.anyio
    async def test_pause_dispatches_requested_event(self) -> None:
        from app.ai.agent.models.plan import PlannedStep, StepAction
        from app.ai.agent.models.state import AgentExecutionState, AgentExecutionStatus
        from app.ai.agent.scratchpad.scratchpad import Scratchpad
        from app.ai.agent.streaming import NoOpStreamPublisher
        from app.ai.tools.schemas import ToolCall

        recording = _RecordingProvider()
        store = InMemoryApprovalStore()
        chat_store = FakeChatStore()
        service = AgentApprovalService(
            approval_store=store,
            chat_store=chat_store,
            scratchpad_store=ScratchpadStore(),
            notification_dispatcher=NotificationDispatcher([recording]),
        )
        owner_id = uuid.uuid4()
        session = await chat_store.create_session(user_id=owner_id)
        step = PlannedStep(
            step_id="s1",
            action=StepAction.TOOL_CALL,
            tool_calls=[ToolCall(name="delete_file", arguments={"path": "/x"})],
        )

        await service.pause(
            step,
            scratchpad=Scratchpad(execution_id="exec-1"),
            state=AgentExecutionState(
                execution_id="exec-1", status=AgentExecutionStatus.EXECUTING
            ),
            session_id=session.id,
            owner_id=owner_id,
            execution_id="exec-1",
            stream_publisher=NoOpStreamPublisher(),
        )
        await _drain_background_notifications()

        assert len(recording.events) == 1
        assert recording.events[0].event_type == ApprovalNotificationEventType.REQUESTED

    @pytest.mark.anyio
    async def test_decide_dispatches_decided_event(self) -> None:
        recording = _RecordingProvider()
        store = InMemoryApprovalStore()
        chat_store = FakeChatStore()
        owner_id = uuid.uuid4()
        session = await chat_store.create_session(user_id=owner_id)
        approval = await store.create(
            session_id=session.id,
            owner_id=owner_id,
            execution_id="exec-notify",
            approval_correlation_id=uuid.uuid4(),
            proposed_calls=[
                ProposedToolCall(
                    name="delete_file", arguments={"path": "/x"}, call_id="c1"
                )
            ],
            paused_scratchpad=[],
            paused_state={"execution_id": "exec-notify", "status": "waiting_approval"},
        )
        service = AgentApprovalService(
            approval_store=store,
            chat_store=chat_store,
            notification_dispatcher=NotificationDispatcher([recording]),
        )

        await service.decide(approval.id, decider_id=owner_id, decision="rejected")
        await _drain_background_notifications()

        assert len(recording.events) == 1
        assert recording.events[0].event_type == ApprovalNotificationEventType.DECIDED

    @pytest.mark.anyio
    async def test_cancel_dispatches_cancelled_event(self) -> None:
        recording = _RecordingProvider()
        store = InMemoryApprovalStore()
        chat_store = FakeChatStore()
        owner_id = uuid.uuid4()
        session = await chat_store.create_session(user_id=owner_id)
        approval = await store.create(
            session_id=session.id,
            owner_id=owner_id,
            execution_id="exec-notify-cancel",
            approval_correlation_id=uuid.uuid4(),
            proposed_calls=[
                ProposedToolCall(
                    name="delete_file", arguments={"path": "/x"}, call_id="c1"
                )
            ],
            paused_scratchpad=[],
            paused_state={
                "execution_id": "exec-notify-cancel",
                "status": "waiting_approval",
            },
        )
        service = AgentApprovalService(
            approval_store=store,
            chat_store=chat_store,
            notification_dispatcher=NotificationDispatcher([recording]),
        )

        await service.cancel(approval.id, owner_id=owner_id)
        await _drain_background_notifications()

        assert len(recording.events) == 1
        assert recording.events[0].event_type == ApprovalNotificationEventType.CANCELLED

    @pytest.mark.anyio
    async def test_no_dispatcher_configured_is_a_no_op(self) -> None:
        store = InMemoryApprovalStore()
        chat_store = FakeChatStore()
        owner_id = uuid.uuid4()
        session = await chat_store.create_session(user_id=owner_id)
        approval = await store.create(
            session_id=session.id,
            owner_id=owner_id,
            execution_id="exec-no-dispatcher",
            approval_correlation_id=uuid.uuid4(),
            proposed_calls=[
                ProposedToolCall(
                    name="delete_file", arguments={"path": "/x"}, call_id="c1"
                )
            ],
            paused_scratchpad=[],
            paused_state={
                "execution_id": "exec-no-dispatcher",
                "status": "waiting_approval",
            },
        )
        service = AgentApprovalService(approval_store=store, chat_store=chat_store)

        result = await service.decide(
            approval.id, decider_id=owner_id, decision="rejected"
        )
        assert result.status.value == "rejected"
