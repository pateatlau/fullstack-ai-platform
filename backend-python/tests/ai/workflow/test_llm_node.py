"""Tests for ``LLMNodeExecutor`` (Epic 06 Phase 6)."""

from __future__ import annotations

import uuid

import pytest

from app.ai.prompts.manager import create_prompt_manager
from app.ai.workflow.models import NodeType, WorkflowContext, WorkflowNode
from app.ai.workflow.nodes.base import NodeExecutionRequest, WorkflowNodeExecutionError
from app.ai.workflow.nodes.llm_node import LLMNodeExecutor
from app.core.config import Settings
from tests.fakes import FakeProvider


def _request(owner_id: uuid.UUID | None = None) -> NodeExecutionRequest:
    return NodeExecutionRequest(
        owner_id=owner_id or uuid.uuid4(),
        execution_receipt_id="run-1:llm:1",
    )


def _executor(
    provider: FakeProvider,
    *,
    settings: Settings | None = None,
) -> LLMNodeExecutor:
    return LLMNodeExecutor(
        prompt_manager=create_prompt_manager(),
        settings=settings or Settings(openai_api_key="test-key"),
        provider=provider,
    )


@pytest.mark.anyio
async def test_executes_llm_call_through_provider() -> None:
    provider = FakeProvider(response="Summarized output.")
    node = WorkflowNode(
        id="summarize",
        type=NodeType.LLM,
        config={"prompt_template": "Summarize: {{ variables.text }}"},
    )
    context = WorkflowContext(variables={"text": "long article"})
    executor = _executor(provider)

    output = await executor.execute(node, context, _request())

    assert output["content"] == "Summarized output."
    assert output["model"] == Settings(openai_api_key="test-key").openai_model
    assert output["execution_receipt_id"] == "run-1:llm:1"


@pytest.mark.anyio
async def test_renders_inline_prompt_against_variables() -> None:
    captured: list[str] = []

    class _RecordingProvider(FakeProvider):
        async def complete_chat(
            self, messages, model, temperature=0.7, *, max_tokens=None
        ):
            captured.append(messages[0].content)
            return await super().complete_chat(
                messages, model, temperature, max_tokens=max_tokens
            )

    node = WorkflowNode(
        id="classify",
        type=NodeType.LLM,
        config={
            "prompt_template": "Topic={{ trigger_input.topic }} prior={{ variables.first.content }}"
        },
    )
    context = WorkflowContext(
        trigger_input={"topic": "news"},
        variables={"first": {"content": "hello"}},
    )
    executor = _executor(_RecordingProvider())

    await executor.execute(node, context, _request())

    assert captured[0] == "Topic=news prior=hello"


@pytest.mark.anyio
async def test_renders_file_backed_prompt_template() -> None:
    captured: list[str] = []

    class _RecordingProvider(FakeProvider):
        async def complete_chat(
            self, messages, model, temperature=0.7, *, max_tokens=None
        ):
            captured.append(messages[0].content)
            return await super().complete_chat(
                messages, model, temperature, max_tokens=max_tokens
            )

    node = WorkflowNode(
        id="transform",
        type=NodeType.LLM,
        config={"prompt_template": "@workflow/transform/1"},
    )
    context = WorkflowContext(
        variables={
            "instruction": "Summarize briefly.",
            "input": "Alpha beta gamma.",
        }
    )
    executor = _executor(_RecordingProvider())

    await executor.execute(node, context, _request())

    assert "Summarize briefly." in captured[0]
    assert "Alpha beta gamma." in captured[0]


@pytest.mark.anyio
async def test_model_override_is_used() -> None:
    provider = FakeProvider(response="ok")
    node = WorkflowNode(
        id="llm",
        type=NodeType.LLM,
        config={
            "prompt_template": "Hello",
            "model_override": "gpt-4o",
        },
    )
    executor = _executor(provider)

    output = await executor.execute(node, WorkflowContext(), _request())

    assert output["model"] == "gpt-4o"


@pytest.mark.anyio
async def test_provider_error_becomes_node_failure() -> None:
    class _FailingProvider(FakeProvider):
        async def complete_chat(
            self, messages, model, temperature=0.7, *, max_tokens=None
        ):
            del messages, model, temperature, max_tokens
            raise RuntimeError("provider unavailable")

    node = WorkflowNode(
        id="llm",
        type=NodeType.LLM,
        config={"prompt_template": "Hello"},
    )
    executor = _executor(_FailingProvider())

    with pytest.raises(WorkflowNodeExecutionError, match="provider call failed"):
        await executor.execute(node, WorkflowContext(), _request())


@pytest.mark.anyio
async def test_invalid_file_template_reference_raises_node_execution_error() -> None:
    node = WorkflowNode(
        id="llm",
        type=NodeType.LLM,
        config={"prompt_template": "@workflow/only-two"},
    )
    executor = _executor(FakeProvider())

    with pytest.raises(
        WorkflowNodeExecutionError, match="@category/name/version"
    ) as exc:
        await executor.execute(node, WorkflowContext(), _request())

    assert exc.value.error_code == "invalid_config"


@pytest.mark.anyio
async def test_unsupported_provider_becomes_node_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers.factory import ProviderFactory, UnsupportedProviderError

    node = WorkflowNode(
        id="llm",
        type=NodeType.LLM,
        config={"prompt_template": "Hello"},
    )
    executor = LLMNodeExecutor(
        prompt_manager=create_prompt_manager(),
        settings=Settings(openai_api_key="test-key"),
    )

    def _raise_unsupported(_name: object, _settings: object) -> object:
        raise UnsupportedProviderError("Unsupported provider: 'unknown'")

    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        staticmethod(_raise_unsupported),
    )

    with pytest.raises(WorkflowNodeExecutionError, match="Unsupported provider") as exc:
        await executor.execute(node, WorkflowContext(), _request())

    assert exc.value.error_code == "invalid_config"


@pytest.mark.anyio
async def test_missing_prompt_template_raises_node_execution_error() -> None:
    node = WorkflowNode(id="llm", type=NodeType.LLM, config={})
    executor = _executor(FakeProvider())

    with pytest.raises(WorkflowNodeExecutionError, match="prompt_template"):
        await executor.execute(node, WorkflowContext(), _request())


@pytest.mark.anyio
async def test_unresolved_template_variable_raises_node_execution_error() -> None:
    node = WorkflowNode(
        id="llm",
        type=NodeType.LLM,
        config={"prompt_template": "Value={{ variables.missing.path }}"},
    )
    executor = _executor(FakeProvider())

    with pytest.raises(WorkflowNodeExecutionError, match="Failed to render"):
        await executor.execute(node, WorkflowContext(), _request())
