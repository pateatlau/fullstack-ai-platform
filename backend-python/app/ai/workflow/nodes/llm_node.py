"""LLM node executor: direct LLM calls via ``PromptManager`` + ``LLMProvider``.

Part I § LLM Node — reuses the existing provider/prompt platform for simple
transform/classify/summarize steps; no agent loop or tool execution here.
"""

from __future__ import annotations

from typing import cast

from app.ai.prompts.manager import PromptManager
from app.ai.workflow.models import WorkflowContext, WorkflowNode
from app.ai.workflow.nodes.base import NodeExecutionRequest, WorkflowNodeExecutionError
from app.ai.workflow.nodes.template_render import render_prompt_template
from app.core.config import Settings
from app.providers.base import LLMProvider
from app.providers.factory import ProviderFactory, UnsupportedProviderError
from app.schemas.chat import ChatMessageSchema, ProviderName


class LLMNodeExecutor:
    """Executes a single non-agentic LLM call for an ``llm`` node."""

    def __init__(
        self,
        *,
        prompt_manager: PromptManager,
        settings: Settings,
        provider: LLMProvider | None = None,
    ) -> None:
        self._prompt_manager = prompt_manager
        self._settings = settings
        self._provider = provider

    async def execute(
        self,
        node: WorkflowNode,
        context: WorkflowContext,
        request: NodeExecutionRequest,
    ) -> dict[str, object]:
        prompt_template = node.config.get("prompt_template")
        if not isinstance(prompt_template, str) or not prompt_template.strip():
            raise WorkflowNodeExecutionError(
                f"LLM node {node.id!r} requires config.prompt_template.",
                error_code="invalid_config",
            )

        prompt = render_prompt_template(
            prompt_template,
            context,
            prompt_manager=self._prompt_manager,
            node_id=node.id,
        )
        model = _resolve_model(node.config, self._settings)
        provider = self._provider or ProviderFactory.get_provider(
            self._settings.llm_provider, self._settings
        )

        messages = [ChatMessageSchema.model_construct(role="user", content=prompt)]
        try:
            completion = await provider.complete_chat(
                messages,
                model,
                temperature=0.7,
            )
        except UnsupportedProviderError as exc:
            raise WorkflowNodeExecutionError(
                str(exc), error_code="invalid_config"
            ) from exc
        except Exception as exc:
            raise WorkflowNodeExecutionError(
                f"LLM node {node.id!r} provider call failed.",
                error_code="provider_error",
            ) from exc

        return {
            "content": completion.content,
            "finish_reason": completion.finish_reason,
            "model": model,
            "execution_receipt_id": request.execution_receipt_id,
        }


def _resolve_model(config: dict[str, object], settings: Settings) -> str:
    model_override = config.get("model_override")
    if isinstance(model_override, str) and model_override.strip():
        return model_override.strip()

    provider_name_raw = settings.llm_provider
    allowed: tuple[ProviderName, ...] = ("openai", "gemini", "groq", "anthropic")
    if provider_name_raw not in allowed:
        raise WorkflowNodeExecutionError(
            f"Unsupported LLM_PROVIDER {provider_name_raw!r} for LLM node execution.",
            error_code="invalid_config",
        )
    provider_name = cast(ProviderName, provider_name_raw)
    defaults: dict[ProviderName, str] = {
        "openai": settings.openai_model,
        "gemini": settings.gemini_model,
        "groq": settings.groq_model,
        "anthropic": settings.anthropic_model,
    }
    return defaults[provider_name]
