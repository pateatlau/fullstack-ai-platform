"""Interrupt controller — barge-in / cancel semantics.

On barge-in, in-flight TTS synthesis and the upstream LLM/agent stream are
cancelled. The voice session stays open for the next utterance.

Partial assistant messages are **not** persisted on interrupt — the client should
discard the in-progress assistant bubble (``MessageStatus.interrupted`` intent).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from app.ai.voice.exceptions import VoiceSessionError
from app.ai.voice.stt import SttPipeline
from app.ai.voice.tts import TtsPipeline
from typing import Union

from app.schemas.voice import (
    AudioInMessage,
    HeartbeatMessage,
    InterruptedMessage,
    InterruptMessage,
    SessionEndMessage,
)

ClientMessage = Union[
    AudioInMessage,
    InterruptMessage,
    HeartbeatMessage,
    SessionEndMessage,
]

__all__ = ["InterruptController", "SessionInterruptState"]


@dataclass
class SessionInterruptState:
    """Per-session task and pipeline handles for interrupt handling."""

    turn_active: bool = False
    tts_task: asyncio.Task[object] | None = None
    llm_task: asyncio.Task[object] | None = None
    stt_task: asyncio.Task[object] | None = None
    tts_pipeline: TtsPipeline | None = None
    stt_pipeline: SttPipeline | None = None
    _owned_tasks: list[asyncio.Task[object]] = field(default_factory=list)


class InterruptController:
    """Cancel in-flight TTS and upstream LLM streams on user barge-in."""

    def __init__(self) -> None:
        self._states: dict[str, SessionInterruptState] = {}

    def _state(self, voice_session_id: str) -> SessionInterruptState:
        if voice_session_id not in self._states:
            self._states[voice_session_id] = SessionInterruptState()
        return self._states[voice_session_id]

    def set_turn_active(self, voice_session_id: str, active: bool) -> None:
        """Mark whether a chat/TTS turn is in progress for barge-in detection."""
        self._state(voice_session_id).turn_active = active

    def is_turn_active(self, voice_session_id: str) -> bool:
        """Return whether TTS or LLM processing is active for the session."""
        state = self._states.get(voice_session_id)
        return state.turn_active if state is not None else False

    def register_tts_pipeline(
        self, voice_session_id: str, pipeline: TtsPipeline
    ) -> None:
        """Bind a TTS pipeline whose synthesis loop honours ``request_cancel()``."""
        self._state(voice_session_id).tts_pipeline = pipeline

    def register_stt_pipeline(
        self, voice_session_id: str, pipeline: SttPipeline
    ) -> None:
        """Bind an STT pipeline whose processing loop honours ``request_cancel()``."""
        self._state(voice_session_id).stt_pipeline = pipeline

    def register_tts_task(
        self, voice_session_id: str, task: asyncio.Task[object]
    ) -> None:
        """Track the asyncio task driving TTS output for the current turn."""
        state = self._state(voice_session_id)
        state.tts_task = task
        state._owned_tasks.append(task)

    def register_llm_task(
        self, voice_session_id: str, task: asyncio.Task[object]
    ) -> None:
        """Track the asyncio task consuming the upstream chat/LLM stream."""
        state = self._state(voice_session_id)
        state.llm_task = task
        state._owned_tasks.append(task)

    def register_stt_task(
        self, voice_session_id: str, task: asyncio.Task[object]
    ) -> None:
        """Track the asyncio task driving STT for the current utterance."""
        state = self._state(voice_session_id)
        state.stt_task = task
        state._owned_tasks.append(task)

    def is_barge_in_trigger(self, message: ClientMessage, *, turn_active: bool) -> bool:
        """Return whether an inbound client message should trigger barge-in."""
        if isinstance(message, InterruptMessage):
            return True
        return isinstance(message, AudioInMessage) and turn_active

    async def cancel_all(self, voice_session_id: str) -> None:
        """Cancel TTS task, upstream LLM stream task, and pipeline loops.

        Idempotent: safe to call when no turn is active. The voice session
        remains open; only the current turn is torn down.
        """
        state = self._states.get(voice_session_id)
        if state is None:
            return

        if state.tts_pipeline is not None:
            state.tts_pipeline.request_cancel()
        if state.stt_pipeline is not None:
            state.stt_pipeline.request_cancel()

        tasks_to_cancel: list[asyncio.Task[object]] = []
        for task in (state.tts_task, state.llm_task, state.stt_task):
            if task is not None and not task.done():
                tasks_to_cancel.append(task)

        for task in tasks_to_cancel:
            task.cancel()

        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

        state.turn_active = False
        state.tts_task = None
        state.llm_task = None
        state.stt_task = None
        state.tts_pipeline = None
        state.stt_pipeline = None
        state._owned_tasks.clear()

    async def handle_barge_in(
        self,
        voice_session_id: str,
        message: ClientMessage,
    ) -> InterruptedMessage | None:
        """Cancel the active turn when *message* is a barge-in trigger.

        Returns:
            ``InterruptedMessage`` when a cancel was performed; ``None`` otherwise.
        """
        if not self.is_barge_in_trigger(
            message, turn_active=self.is_turn_active(voice_session_id)
        ):
            return None

        await self.cancel_all(voice_session_id)
        return InterruptedMessage()

    def clear_session(self, voice_session_id: str) -> None:
        """Drop interrupt state when a voice session is torn down."""
        self._states.pop(voice_session_id, None)

    def assert_no_leaked_tasks(self, voice_session_id: str) -> None:
        """Raise when registered tasks were not cleaned up after cancel.

        Intended for tests and diagnostics only.
        """
        state = self._states.get(voice_session_id)
        if state is None:
            return

        for task in state._owned_tasks:
            if not task.done():
                raise VoiceSessionError(
                    f"Leaked asyncio task after interrupt: {task!r}",
                    code="interrupt_task_leak",
                )
