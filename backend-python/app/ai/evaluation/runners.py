"""Evaluation runners for prompt, retrieval, end-to-end, agent, and workflow levels."""

from __future__ import annotations

import datetime
import json
import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent.models.config import AgentConfig
from app.ai.agent.models.context import AgentContext
from app.ai.agent.executor.agent_executor import AgentExecutor
from app.ai.agent.executor.tool_runner import ToolRunner
from app.ai.agent.planner.react_planner import ReActPlanner
from app.ai.agent.scratchpad.store import ScratchpadStore
from app.ai.agent.streaming.publisher import NoOpStreamPublisher
from app.ai.evaluation.hitl_support import EvalHitlApprovalStore, EvalHitlChatStore
from app.ai.agent.models.messages import AgentMessage
from app.ai.agent.models.request import AgentRequest
from app.ai.agent.runtime.factory import create_default_agent
from app.ai.documents.pipeline import IngestionPipeline
from app.ai.evaluation.datasets import EvalCase, load_workflow_fixture
from app.ai.hitl.models import ApprovalStatus, ProposedToolCall
from app.ai.hitl.policy import ApprovalPolicy
from app.ai.hitl.service import AgentApprovalService
from app.ai.plugins.bootstrap import load_plugins as orchestrate_load_plugins
from app.ai.plugins.registry import PluginRegistry
from app.ai.plugins.workflow.plugin_node import PluginNodeExecutor
from app.ai.plugins.workflow.registry import WorkflowPluginRegistry
from app.ai.evaluation.metrics import (
    TARGET_RAG_RESPONSE_MS,
    TARGET_RETRIEVAL_MS,
    answer_matches,
    faithfulness_score,
    hallucination_detected,
    latency_within_target,
    precision,
    recall,
)
from app.ai.evaluation.report import EvalCaseResult
from app.ai.prompts.manager import PromptManager, create_prompt_manager
from app.ai.prompts.repository import PromptRepository
from app.ai.rag.context_builder import ContextBuilder
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.schemas import ToolCall, ToolExecutionContext
from app.ai.tools.stubs.echo import ECHO_TOOL_DEFINITION, EchoToolHandler
from app.ai.workflow.conditions.evaluator import ConditionEvaluator
from app.ai.workflow.manager import WorkflowManager
from app.ai.tools.stubs.send_notification import (
    SEND_NOTIFICATION_TOOL_DEFINITION,
    SEND_NOTIFICATION_TOOL_NAME,
    SendNotificationHandler,
)
from app.ai.workflow.models import (
    ApprovalDecision,
    DefinitionStatus,
    NodeType,
    RunStatus,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from app.ai.workflow.nodes.agent_node import AgentNodeExecutor
from app.ai.workflow.nodes.approval_node import ApprovalNodeExecutor
from app.ai.workflow.nodes.llm_node import LLMNodeExecutor
from app.ai.workflow.nodes.parallel_node import ForkNodeExecutor, JoinNodeExecutor
from app.ai.workflow.nodes.router_node import RouterNodeExecutor
from app.ai.workflow.nodes.task_node import TaskNodeExecutor
from app.ai.workflow.providers.postgres import PostgresWorkflowStore
from app.ai.rag.prompt_builder import PromptBuilder
from app.ai.rag.retriever import Retriever
from app.ai.rag.service import RAGService
from app.ai.vectorstores.pgvector import PgVectorStore
from app.core.caller import CallerContext
from app.core.config import Settings
from app.core.logging import get_logger
from app.db.documents import SqlDocumentStore
from app.db.identity import SqlUserStore
from app.db.models import User
from app.providers.base import (
    ChatMessageInput,
    LLMProvider,
    ProviderChunk,
    ProviderCompletion,
    ProviderToolCall,
    ProviderToolCompletion,
    ProviderUsage,
)
from app.providers.factory import ProviderFactory
from app.schemas.chat import ChatMessageSchema
from app.services.knowledge_service import KnowledgeService

DOCUMENT_FIXTURES_ROOT = (
    Path(__file__).resolve().parents[3] / "tests" / "data" / "documents"
)
REFERENCE_PLUGINS_ROOT = Path(__file__).resolve().parents[3] / "plugins"
_logger = get_logger(__name__)
EMBEDDING_DIMENSIONS = 1536
_AGENT_EVAL_SUPPORTED_TOOLS: frozenset[str] = frozenset({"echo"})


class _FakeEmbeddingProvider:
    dimensions = EMBEDDING_DIMENSIONS

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [
            [float(index % EMBEDDING_DIMENSIONS), 0.0]
            + [0.0] * (EMBEDDING_DIMENSIONS - 2)
            for index, _ in enumerate(texts)
        ]


class _EvalLLMProvider:
    """Deterministic LLM double for offline evaluation runs."""

    def __init__(self, *, default_response: str = "", judge_mode: bool = False) -> None:
        self._response = default_response
        self._judge_mode = judge_mode
        self.last_messages: list[ChatMessageSchema] = []

    async def stream_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
        *,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ProviderChunk]:
        del messages, model, temperature, max_tokens
        if False:
            yield ProviderChunk(content="", finish_reason=None)

    async def complete_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
        *,
        max_tokens: int | None = None,
    ) -> ProviderCompletion:
        del model, temperature, max_tokens
        self.last_messages = list(messages)
        content = self._response
        if self._judge_mode and messages:
            prompt = messages[-1].content
            if "Respond in JSON only" in prompt:
                content = (
                    '{"faithful": true, "hallucination": false, '
                    '"reason": "Answer aligns with context."}'
                )
        return ProviderCompletion(
            content=content,
            finish_reason="stop",
            usage=ProviderUsage(
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
            ),
        )

    async def complete_chat_with_tools(
        self,
        messages: list[ChatMessageInput],
        model: str,
        tools: list[dict[str, object]],
        temperature: float = 0.7,
        *,
        max_tokens: int | None = None,
    ) -> ProviderToolCompletion:
        del messages, model, tools, temperature, max_tokens
        return ProviderToolCompletion(
            content=self._response,
            tool_calls=[],
            finish_reason="stop",
            usage=ProviderUsage(
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
            ),
        )


class _AgentEvalProvider:
    """Deterministic agent eval double with scripted tool-call completions."""

    def __init__(
        self,
        *,
        tool_completions: list[ProviderToolCompletion],
        model: str,
        temperature: float,
    ) -> None:
        self._tool_completions = tool_completions
        self._tool_call_index = 0
        self.model = model
        self.temperature = temperature
        self.tools_invoked: list[str] = []

    async def stream_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
        *,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ProviderChunk]:
        del messages, model, temperature, max_tokens
        if False:
            yield ProviderChunk(content="", finish_reason=None)

    async def complete_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
        *,
        max_tokens: int | None = None,
    ) -> ProviderCompletion:
        del messages, model, temperature, max_tokens
        completion = self._next_tool_completion()
        return ProviderCompletion(
            content=completion.content or "",
            finish_reason=completion.finish_reason,
            usage=completion.usage,
        )

    async def complete_chat_with_tools(
        self,
        messages: list[ChatMessageInput],
        model: str,
        tools: list[dict[str, object]],
        temperature: float = 0.7,
        *,
        max_tokens: int | None = None,
    ) -> ProviderToolCompletion:
        del messages, model, tools, temperature, max_tokens
        completion = self._next_tool_completion()
        for tool_call in completion.tool_calls:
            self.tools_invoked.append(tool_call.name)
        return completion

    def _next_tool_completion(self) -> ProviderToolCompletion:
        if not self._tool_completions:
            return ProviderToolCompletion(
                content="",
                tool_calls=[],
                finish_reason="stop",
                usage=ProviderUsage(
                    prompt_tokens=1,
                    completion_tokens=1,
                    total_tokens=2,
                ),
            )
        index = min(self._tool_call_index, len(self._tool_completions) - 1)
        completion = self._tool_completions[index]
        self._tool_call_index += 1
        return completion


@dataclass(frozen=True)
class PromptEvalRunner:
    """Render prompt templates and assert expected output."""

    prompt_manager: PromptManager

    def run_case(self, case: EvalCase) -> EvalCaseResult:
        start = time.perf_counter()
        try:
            rendered = self.prompt_manager.render(
                case.prompt_category or "",
                case.prompt_name or "",
                case.prompt_version or "",
                case.prompt_variables,
            )
            passed = True
            if case.expected_render_exact is not None:
                passed = rendered == case.expected_render_exact
            for substring in case.expected_render_contains:
                if substring not in rendered:
                    passed = False
                    break
            latency_ms = int((time.perf_counter() - start) * 1000)
            return EvalCaseResult(
                case_id=case.id,
                level="prompt",
                passed=passed,
                latency_ms=latency_ms,
                prompt_version=case.prompt_version,
            )
        except Exception as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            return EvalCaseResult(
                case_id=case.id,
                level="prompt",
                passed=False,
                latency_ms=latency_ms,
                prompt_version=case.prompt_version,
                error=str(exc),
            )


@dataclass(frozen=True)
class RetrievalEvalRunner:
    """Ingest fixture documents and evaluate retriever precision/recall."""

    session: AsyncSession
    settings: Settings
    fixtures_root: Path = DOCUMENT_FIXTURES_ROOT

    async def run_case(self, case: EvalCase) -> EvalCaseResult:
        start = time.perf_counter()
        try:
            user_id = await self._create_user()
            relevant_ids = set(case.relevant_chunk_ids)

            if case.document_fixture:
                document_id = await self._ingest_fixture(
                    user_id=user_id,
                    filename=case.document_fixture,
                )
                if not relevant_ids:
                    relevant_ids = await self._all_chunk_ids(document_id)

            retrieved = await self._retriever().retrieve(
                question=case.question or "",
                user_id=user_id,
            )
            retrieved_ids = {
                chunk.chunk_id for chunk in retrieved if chunk.chunk_id is not None
            }
            case_precision = precision(retrieved_ids, relevant_ids)
            case_recall = recall(retrieved_ids, relevant_ids)
            if relevant_ids:
                passed = case_recall > 0.0
            else:
                passed = not retrieved_ids

            latency_ms = int((time.perf_counter() - start) * 1000)
            warning = None
            if not latency_within_target("retrieval", latency_ms, TARGET_RETRIEVAL_MS):
                warning = (
                    f"retrieval latency {latency_ms}ms exceeds {TARGET_RETRIEVAL_MS}ms"
                )

            return EvalCaseResult(
                case_id=case.id,
                level="retrieval",
                passed=passed,
                latency_ms=latency_ms,
                precision=case_precision,
                recall=case_recall,
                retrieved_count=len(retrieved_ids),
                latency_warning=warning,
            )
        except Exception as exc:
            await self.session.rollback()
            latency_ms = int((time.perf_counter() - start) * 1000)
            return EvalCaseResult(
                case_id=case.id,
                level="retrieval",
                passed=False,
                latency_ms=latency_ms,
                error=str(exc),
            )

    async def _create_user(self) -> uuid.UUID:
        user = await SqlUserStore(self.session).create(
            sub=f"eval-{uuid.uuid4()}",
            email=None,
            name=None,
            picture=None,
        )
        return user.id

    async def _ingest_fixture(
        self,
        *,
        user_id: uuid.UUID,
        filename: str,
    ) -> uuid.UUID:
        fixture_path = self.fixtures_root / filename
        if not fixture_path.is_file():
            raise FileNotFoundError(f"Document fixture not found: {fixture_path}")
        knowledge = self._knowledge_service()
        return await knowledge.ingest_document(
            user_id=user_id,
            file_bytes=fixture_path.read_bytes(),
            filename=filename,
            mime_type=_guess_mime_type(filename),
        )

    async def _all_chunk_ids(self, document_id: uuid.UUID) -> set[uuid.UUID]:
        chunks = await SqlDocumentStore(self.session).list_chunks(document_id)
        return {chunk.id for chunk in chunks}

    def _knowledge_service(self) -> KnowledgeService:
        pipeline = IngestionPipeline(
            self.settings, embedding_provider=_FakeEmbeddingProvider()
        )
        vector_store = PgVectorStore(self.session, self.settings)
        return KnowledgeService(
            session=self.session,
            settings=self.settings,
            pipeline=pipeline,
            vector_store=vector_store,
        )

    def _retriever(self) -> Retriever:
        return Retriever(
            embedding_provider=_FakeEmbeddingProvider(),
            vector_store=PgVectorStore(self.session, self.settings),
            settings=self.settings,
        )


@dataclass(frozen=True)
class EndToEndEvalRunner:
    """Run the full RAG pipeline and evaluate answer quality."""

    session: AsyncSession
    settings: Settings
    prompt_manager: PromptManager
    use_judge: bool = False
    fixtures_root: Path = DOCUMENT_FIXTURES_ROOT

    async def run_case(self, case: EvalCase) -> EvalCaseResult:
        start = time.perf_counter()
        try:
            user = await SqlUserStore(self.session).create(
                sub=f"eval-e2e-{uuid.uuid4()}",
                email=None,
                name=None,
                picture=None,
            )
            user_id = user.id

            if case.document_fixture:
                retrieval_runner = RetrievalEvalRunner(
                    session=self.session,
                    settings=self.settings,
                    fixtures_root=self.fixtures_root,
                )
                await retrieval_runner._ingest_fixture(
                    user_id=user_id,
                    filename=case.document_fixture,
                )

            llm = _EvalLLMProvider(
                default_response=case.expected_answer or "",
                judge_mode=self.use_judge,
            )
            rag = self._rag_service()
            with patch.object(ProviderFactory, "get_provider", return_value=llm):
                response = await rag.ask(user_id=user_id, question=case.question or "")

            correctness = answer_matches(
                response.answer,
                case.expected_answer or "",
                case.expected_answer_match,
            )
            context = _extract_context(_messages_from_llm(llm))
            if self.use_judge:
                faithful_bool, hallucination = await self._run_judge(
                    context=context,
                    question=case.question or "",
                    answer=response.answer,
                    llm=llm,
                )
                faithful = 1.0 if faithful_bool else 0.0
            else:
                faithful = faithfulness_score(context, response.answer)
                hallucination = hallucination_detected(context, response.answer)

            passed = correctness and faithful >= 0.5 and not hallucination

            total_latency_ms = int((time.perf_counter() - start) * 1000)
            warning = None
            if not latency_within_target(
                "e2e",
                total_latency_ms,
                TARGET_RAG_RESPONSE_MS,
            ):
                warning = (
                    f"e2e latency {total_latency_ms}ms exceeds "
                    f"{TARGET_RAG_RESPONSE_MS}ms"
                )

            return EvalCaseResult(
                case_id=case.id,
                level="e2e",
                passed=passed,
                latency_ms=total_latency_ms,
                correctness=correctness,
                faithfulness=faithful,
                hallucination=hallucination,
                latency_warning=warning,
                model=self.settings.openai_model,
                temperature=self.settings.default_temperature,
            )
        except Exception as exc:
            await self.session.rollback()
            latency_ms = int((time.perf_counter() - start) * 1000)
            return EvalCaseResult(
                case_id=case.id,
                level="e2e",
                passed=False,
                latency_ms=latency_ms,
                model=self.settings.openai_model,
                temperature=self.settings.default_temperature,
                error=str(exc),
            )

    def _rag_service(self) -> RAGService:
        retriever = Retriever(
            embedding_provider=_FakeEmbeddingProvider(),
            vector_store=PgVectorStore(self.session, self.settings),
            settings=self.settings,
        )
        return RAGService(
            retriever=retriever,
            context_builder=ContextBuilder(self.settings),
            prompt_builder=PromptBuilder(
                prompt_manager=self.prompt_manager,
                settings=self.settings,
            ),
            settings=self.settings,
        )

    async def _run_judge(
        self,
        *,
        context: str,
        question: str,
        answer: str,
        llm: LLMProvider,
    ) -> tuple[bool, bool]:
        prompt = self.prompt_manager.render(
            "evaluation",
            "judge",
            "1",
            {"context": context, "question": question, "answer": answer},
        )
        completion = await llm.complete_chat(
            [ChatMessageSchema(role="user", content=prompt)],
            self.settings.openai_model,
            self.settings.default_temperature,
        )
        return _parse_judge_response(completion.content)


@dataclass(frozen=True)
class AgentEvalRunner:
    """Run agent-level cases through ``DefaultAgent`` with fake provider/tools."""

    settings: Settings
    prompt_manager: PromptManager

    async def run_case(self, case: EvalCase) -> EvalCaseResult:
        start = time.perf_counter()
        if not self.settings.agent_runtime_enabled:
            return EvalCaseResult(
                case_id=case.id,
                level="agent",
                passed=False,
                latency_ms=0,
                skipped=True,
                skip_reason="AGENT_RUNTIME_ENABLED=false",
            )

        model = case.model or self.settings.openai_model
        temperature = (
            case.temperature
            if case.temperature is not None
            else self.settings.default_temperature
        )
        try:
            provider = _build_agent_eval_provider(
                case,
                model=model,
                temperature=temperature,
            )
            registry = ToolRegistry()
            registry.register(ECHO_TOOL_DEFINITION, EchoToolHandler())
            tool_executor = ToolExecutor(registry=registry, settings=self.settings)
            agent = create_default_agent(
                settings=self.settings,
                tool_registry=registry,
                prompt_manager=self.prompt_manager,
                tool_executor=tool_executor,
                approval_policy=_approval_policy_for_settings(self.settings),
            )
            request = AgentRequest(
                messages=[
                    AgentMessage(
                        role="user",
                        content=_agent_user_content(case),
                    )
                ],
                model=model,
                temperature=temperature,
            )
            context = AgentContext(execution_id=f"eval-agent-{case.id}")

            with patch.object(
                ProviderFactory,
                "get_provider",
                staticmethod(lambda _name, _settings: provider),
            ):
                response = await agent.run(request, context)

            tool_calls_correct = None
            if case.expected_tool_calls:
                tool_calls_correct = provider.tools_invoked == list(
                    case.expected_tool_calls
                )

            outcome_correct = True
            if case.expected_outcome is not None:
                outcome_correct = answer_matches(
                    response.content,
                    case.expected_outcome,
                    case.expected_outcome_match,
                )

            passed = outcome_correct and (
                tool_calls_correct is None or tool_calls_correct
            )
            latency_ms = int((time.perf_counter() - start) * 1000)
            return EvalCaseResult(
                case_id=case.id,
                level="agent",
                passed=passed,
                latency_ms=latency_ms,
                tool_calls_correct=tool_calls_correct,
                model=model,
                temperature=temperature,
            )
        except Exception as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            return EvalCaseResult(
                case_id=case.id,
                level="agent",
                passed=False,
                latency_ms=latency_ms,
                model=model,
                temperature=temperature,
                error=str(exc),
            )


@dataclass(frozen=True)
class WorkflowEvalRunner:
    """Run workflow-level cases through ``WorkflowManager`` against Postgres."""

    session: AsyncSession
    settings: Settings

    async def run_case(self, case: EvalCase) -> EvalCaseResult:
        start = time.perf_counter()
        if not self.settings.workflow_engine_enabled:
            return EvalCaseResult(
                case_id=case.id,
                level="workflow",
                passed=False,
                latency_ms=0,
                skipped=True,
                skip_reason="WORKFLOW_ENGINE_ENABLED=false",
            )

        owner_id: uuid.UUID | None = None
        try:
            owner_id = await self._create_user()
            definition = _workflow_definition_from_case(case, owner_id=owner_id)
            manager = _build_eval_workflow_manager(
                session=self.session,
                settings=self.settings,
            )
            created = await manager.create_definition(definition)
            await self.session.commit()

            run = await manager.start_run(
                created.id,
                owner_id=owner_id,
                idempotency_key=f"eval-{case.id}-{uuid.uuid4()}",
                trigger_input=case.trigger_input,
                defer_schedule=True,
            )
            await self.session.commit()
            manager.flush_deferred_run_schedules()
            await _await_scheduled_run(manager)

            final_run = await manager.get_run(run.id, owner_id=owner_id)
            if final_run is None:
                raise RuntimeError(f"Workflow run {run.id} not found after execution.")

            terminal_status = final_run.status.value
            expected_status = case.expected_terminal_status or ""
            passed = terminal_status == expected_status
            model, prompt_version = _workflow_reproducibility_metadata(definition)

            latency_ms = int((time.perf_counter() - start) * 1000)
            return EvalCaseResult(
                case_id=case.id,
                level="workflow",
                passed=passed,
                latency_ms=latency_ms,
                terminal_status=terminal_status,
                model=model,
                prompt_version=prompt_version,
            )
        except Exception as exc:
            await self.session.rollback()
            latency_ms = int((time.perf_counter() - start) * 1000)
            return EvalCaseResult(
                case_id=case.id,
                level="workflow",
                passed=False,
                latency_ms=latency_ms,
                error=str(exc),
            )
        finally:
            if owner_id is not None:
                await _cleanup_eval_workflow_owner(self.session, owner_id)

    async def _create_user(self) -> uuid.UUID:
        user = await SqlUserStore(self.session).create(
            sub=f"eval-workflow-{uuid.uuid4()}",
            email=None,
            name=None,
            picture=None,
        )
        return user.id


@dataclass(frozen=True)
class PluginEvalRunner:
    """Run plugin-level cases against git-tracked reference plugins."""

    settings: Settings
    session: AsyncSession | None = None
    plugins_root: Path = REFERENCE_PLUGINS_ROOT

    async def run_case(self, case: EvalCase) -> EvalCaseResult:
        start = time.perf_counter()
        if not self.settings.plugins_enabled:
            return EvalCaseResult(
                case_id=case.id,
                level="plugin",
                passed=False,
                latency_ms=0,
                skipped=True,
                skip_reason="PLUGINS_ENABLED=false",
            )

        plugin_kind = case.plugin_kind
        if plugin_kind is None:
            return _plugin_error_result(
                case_id=case.id,
                start=start,
                message="plugin_kind is required for plugin cases.",
            )

        try:
            if plugin_kind == "tool":
                return await self._run_tool_case(case, start=start)
            if plugin_kind == "prompt":
                return self._run_prompt_case(case, start=start)
            return await self._run_workflow_case(case, start=start)
        except Exception as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            return EvalCaseResult(
                case_id=case.id,
                level="plugin",
                passed=False,
                latency_ms=latency_ms,
                error=str(exc),
            )

    async def _run_tool_case(
        self,
        case: EvalCase,
        *,
        start: float,
    ) -> EvalCaseResult:
        tool_name = case.plugin_tool_name
        if tool_name is None:
            return _plugin_error_result(
                case_id=case.id,
                start=start,
                message="plugin_tool_name is required for tool plugin cases.",
            )

        tool_registry, _prompts, _plugin_registry, _workflow_registry = (
            _load_reference_plugin_registries(
                settings=self.settings,
                plugins_root=self.plugins_root,
            )
        )
        tool_executor = ToolExecutor(registry=tool_registry, settings=self.settings)
        result = await tool_executor.execute(
            ToolCall(name=tool_name, arguments=case.plugin_tool_arguments),
            ToolExecutionContext(caller=CallerContext.for_user(uuid.uuid4())),
        )
        passed = result.success
        if passed and case.expected_tool_data is not None:
            passed = isinstance(result.data, dict) and dict(result.data) == dict(
                case.expected_tool_data
            )

        latency_ms = int((time.perf_counter() - start) * 1000)
        return EvalCaseResult(
            case_id=case.id,
            level="plugin",
            passed=passed,
            latency_ms=latency_ms,
            correctness=passed,
        )

    def _run_prompt_case(self, case: EvalCase, *, start: float) -> EvalCaseResult:
        _tools, prompt_repository, _plugin_registry, _workflow_registry = (
            _load_reference_plugin_registries(
                settings=self.settings,
                plugins_root=self.plugins_root,
            )
        )
        prompt_manager = PromptManager(repository=prompt_repository)
        rendered = prompt_manager.render(
            case.prompt_category or "",
            case.prompt_name or "",
            case.prompt_version or "",
            case.prompt_variables,
        )
        passed = True
        if case.expected_render_exact is not None:
            passed = rendered == case.expected_render_exact
        for substring in case.expected_render_contains:
            if substring not in rendered:
                passed = False
                break

        latency_ms = int((time.perf_counter() - start) * 1000)
        return EvalCaseResult(
            case_id=case.id,
            level="plugin",
            passed=passed,
            latency_ms=latency_ms,
            prompt_version=case.prompt_version,
        )

    async def _run_workflow_case(
        self,
        case: EvalCase,
        *,
        start: float,
    ) -> EvalCaseResult:
        if not self.settings.workflow_engine_enabled:
            return EvalCaseResult(
                case_id=case.id,
                level="plugin",
                passed=False,
                latency_ms=0,
                skipped=True,
                skip_reason="WORKFLOW_ENGINE_ENABLED=false",
            )
        if self.session is None:
            return EvalCaseResult(
                case_id=case.id,
                level="plugin",
                passed=False,
                latency_ms=0,
                skipped=True,
                skip_reason="Postgres not available (run from backend-python with DB up)",
            )
        if not await pgvector_available(self.session):
            return EvalCaseResult(
                case_id=case.id,
                level="plugin",
                passed=False,
                latency_ms=0,
                skipped=True,
                skip_reason="pgvector extension not available",
            )

        owner_id: uuid.UUID | None = None
        try:
            owner_id = await self._create_user()
            tool_registry, prompt_repository, plugin_registry, workflow_registry = (
                _load_reference_plugin_registries(
                    settings=self.settings,
                    plugins_root=self.plugins_root,
                )
            )
            definition = _workflow_definition_from_case(case, owner_id=owner_id)
            manager = _build_plugin_eval_workflow_manager(
                session=self.session,
                settings=self.settings,
                tool_registry=tool_registry,
                prompt_repository=prompt_repository,
                plugin_registry=plugin_registry,
                workflow_plugin_registry=workflow_registry,
            )
            created = await manager.create_definition(definition)
            await self.session.commit()

            run = await manager.start_run(
                created.id,
                owner_id=owner_id,
                idempotency_key=f"eval-plugin-{case.id}-{uuid.uuid4()}",
                trigger_input=case.trigger_input,
                defer_schedule=True,
            )
            await self.session.commit()
            manager.flush_deferred_run_schedules()
            await _await_scheduled_run(manager)

            final_run = await manager.get_run(run.id, owner_id=owner_id)
            if final_run is None:
                raise RuntimeError(f"Workflow run {run.id} not found after execution.")

            terminal_status = final_run.status.value
            expected_status = case.expected_terminal_status or ""
            passed = terminal_status == expected_status
            model, prompt_version = _workflow_reproducibility_metadata(definition)
            latency_ms = int((time.perf_counter() - start) * 1000)
            return EvalCaseResult(
                case_id=case.id,
                level="plugin",
                passed=passed,
                latency_ms=latency_ms,
                terminal_status=terminal_status,
                model=model,
                prompt_version=prompt_version,
            )
        except Exception as exc:
            await self.session.rollback()
            latency_ms = int((time.perf_counter() - start) * 1000)
            return EvalCaseResult(
                case_id=case.id,
                level="plugin",
                passed=False,
                latency_ms=latency_ms,
                error=str(exc),
            )
        finally:
            if owner_id is not None and self.session is not None:
                await _cleanup_eval_workflow_owner(self.session, owner_id)

    async def _create_user(self) -> uuid.UUID:
        if self.session is None:
            raise RuntimeError(
                "Postgres session is required for plugin workflow cases."
            )
        user = await SqlUserStore(self.session).create(
            sub=f"eval-plugin-{uuid.uuid4()}",
            email=None,
            name=None,
            picture=None,
        )
        return user.id


@dataclass(frozen=True)
class HitlEvalRunner:
    """Run HITL reference scenarios for agent and workflow surfaces."""

    settings: Settings
    prompt_manager: PromptManager
    session: AsyncSession | None = None

    async def run_case(self, case: EvalCase) -> EvalCaseResult:
        start = time.perf_counter()
        if not self.settings.hitl_enabled:
            return EvalCaseResult(
                case_id=case.id,
                level="hitl",
                passed=False,
                latency_ms=0,
                skipped=True,
                skip_reason="HITL_ENABLED=false",
            )

        surface = case.hitl_surface
        if surface == "agent":
            return await self._run_agent_case(case, start=start)
        if surface == "workflow":
            return await self._run_workflow_case(case, start=start)
        return _hitl_error_result(
            case_id=case.id,
            start=start,
            message="hitl_surface is required for hitl cases.",
        )

    async def _run_agent_case(self, case: EvalCase, *, start: float) -> EvalCaseResult:
        if not self.settings.agent_runtime_enabled:
            return EvalCaseResult(
                case_id=case.id,
                level="hitl",
                passed=False,
                latency_ms=0,
                skipped=True,
                skip_reason="AGENT_RUNTIME_ENABLED=false",
            )

        SendNotificationHandler.reset()
        owner_id = uuid.uuid4()
        approval_store = EvalHitlApprovalStore()
        chat_store = EvalHitlChatStore()
        registry = _hitl_eval_tool_registry()
        eval_settings = self.settings.model_copy(update={"hitl_enabled": True})
        tool_executor = ToolExecutor(registry=registry, settings=eval_settings)
        approval_service = AgentApprovalService(
            approval_store=approval_store,
            chat_store=chat_store,
            tool_registry=registry,
            tool_executor=tool_executor,
            scratchpad_store=ScratchpadStore(),
        )
        provider = _hitl_agent_provider(case)
        agent = create_default_agent(
            settings=eval_settings,
            tool_registry=registry,
            prompt_manager=self.prompt_manager,
            tool_executor=tool_executor,
            scratchpad_store=ScratchpadStore(),
            approval_policy=ApprovalPolicy(
                required_tool_names=frozenset(eval_settings.hitl_required_tool_names)
            ),
            approval_service=approval_service,
        )
        session = await chat_store.create_session(user_id=owner_id)
        caller = CallerContext.for_user(owner_id)
        context = AgentContext(
            execution_id=f"eval-hitl-{case.id}",
            caller=caller,
            session_id=session.id,
        )
        request = AgentRequest(
            messages=[AgentMessage(role="user", content=case.goal or "")],
            model=case.model or self.settings.openai_model,
            config=AgentConfig(max_iterations=3),
        )

        try:
            with patch.object(
                ProviderFactory,
                "get_provider",
                staticmethod(lambda _name, _settings: provider),
            ):
                paused = await agent.run(request, context)

            if paused.finish_reason != "waiting_approval":
                raise RuntimeError(
                    f"Expected waiting_approval, got {paused.finish_reason!r}."
                )
            pending = next(
                (
                    row
                    for row in approval_store.rows
                    if row.status is ApprovalStatus.PENDING
                ),
                None,
            )
            if pending is None:
                raise RuntimeError(
                    "No pending agent tool approval found after agent paused "
                    "with waiting_approval."
                )
            executor = _hitl_resume_executor(
                registry=registry,
                approval_service=approval_service,
                provider=provider,
                eval_settings=eval_settings,
            )
            passed = await self._apply_agent_decision(
                case=case,
                approval_service=approval_service,
                approval_id=pending.id,
                owner_id=owner_id,
                executor=executor,
                request=request,
                context=context,
                caller=caller,
            )
            latency_ms = int((time.perf_counter() - start) * 1000)
            return EvalCaseResult(
                case_id=case.id,
                level="hitl",
                passed=passed,
                latency_ms=latency_ms,
                model=request.model,
            )
        except Exception as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            return EvalCaseResult(
                case_id=case.id,
                level="hitl",
                passed=False,
                latency_ms=latency_ms,
                error=str(exc),
            )

    async def _apply_agent_decision(
        self,
        *,
        case: EvalCase,
        approval_service: AgentApprovalService,
        approval_id: uuid.UUID,
        owner_id: uuid.UUID,
        executor: AgentExecutor,
        request: AgentRequest,
        context: AgentContext,
        caller: CallerContext,
    ) -> bool:
        decision = case.hitl_decision or "approve"
        if decision == "reject":
            result = await approval_service.decide(
                approval_id,
                decider_id=owner_id,
                decision="rejected",
                reason="eval reject",
            )
            return result.status is ApprovalStatus.REJECTED

        edited_calls = _edited_calls_from_case(case)
        _, response = await approval_service.approve_and_resume(
            approval_id,
            decider_id=owner_id,
            executor=executor,
            request=request,
            context=context,
            tool_context=ToolExecutionContext(caller=caller),
            stream_publisher=NoOpStreamPublisher(),
            edited_calls=edited_calls,
        )
        if decision == "approve_with_edits":
            expected_message = None
            if edited_calls:
                message_value = edited_calls[0].arguments.get("message")
                if isinstance(message_value, str):
                    expected_message = message_value
            if expected_message is None:
                expected_message = "edited"
            if not SendNotificationHandler.sent_messages:
                return False
            last_sent = SendNotificationHandler.sent_messages[-1]
            if last_sent.get("message") != expected_message:
                return False
        elif not SendNotificationHandler.sent_messages:
            return False

        if case.expected_outcome is None:
            return response.finish_reason == "stop"
        return answer_matches(
            response.content,
            case.expected_outcome,
            case.expected_outcome_match,
        )

    async def _run_workflow_case(
        self, case: EvalCase, *, start: float
    ) -> EvalCaseResult:
        if not self.settings.workflow_engine_enabled:
            return EvalCaseResult(
                case_id=case.id,
                level="hitl",
                passed=False,
                latency_ms=0,
                skipped=True,
                skip_reason="WORKFLOW_ENGINE_ENABLED=false",
            )
        if self.session is None:
            return EvalCaseResult(
                case_id=case.id,
                level="hitl",
                passed=False,
                latency_ms=0,
                skipped=True,
                skip_reason="Postgres not available (run from backend-python with DB up)",
            )
        if not await pgvector_available(self.session):
            return EvalCaseResult(
                case_id=case.id,
                level="hitl",
                passed=False,
                latency_ms=0,
                skipped=True,
                skip_reason="pgvector extension not available",
            )

        owner_id: uuid.UUID | None = None
        try:
            owner_id = await self._create_user()
            manager = _build_hitl_eval_workflow_manager(
                session=self.session,
                settings=self.settings.model_copy(update={"hitl_enabled": True}),
            )
            definition = _workflow_definition_from_case(case, owner_id=owner_id)
            created = await manager.create_definition(definition)
            await self.session.commit()

            run = await manager.start_run(
                created.id,
                owner_id=owner_id,
                idempotency_key=f"eval-hitl-{case.id}-{uuid.uuid4()}",
                trigger_input=case.trigger_input,
                defer_schedule=True,
            )
            await self.session.commit()
            manager.flush_deferred_run_schedules()
            await _await_scheduled_run(manager)

            paused = await manager.get_run(run.id, owner_id=owner_id)
            if paused is None:
                raise RuntimeError(f"Workflow run {run.id} not found after scheduling.")

            if case.hitl_decision == "reject":
                if paused.status is not RunStatus.WAITING_APPROVAL:
                    raise RuntimeError(
                        f"Expected waiting_approval before reject, got {paused.status}."
                    )
                with_executions = await manager.get_run_with_executions(
                    run.id,
                    owner_id=owner_id,
                )
                assert with_executions is not None
                approval_execution = next(
                    (
                        execution
                        for execution in with_executions[1]
                        if execution.node_type is NodeType.APPROVAL
                    ),
                    None,
                )
                if approval_execution is None:
                    raise RuntimeError(
                        f"No approval node execution found for workflow run {run.id}."
                    )
                failed, _ = await manager.apply_decision(
                    run.id,
                    approval_execution.id,
                    owner_id=owner_id,
                    decision=ApprovalDecision.REJECTED,
                    reason="eval reject",
                )
                terminal_status = failed.status.value
            else:
                if paused.status is not RunStatus.WAITING_APPROVAL:
                    raise RuntimeError(
                        f"Expected waiting_approval before decision, got {paused.status}."
                    )
                with_executions = await manager.get_run_with_executions(
                    run.id,
                    owner_id=owner_id,
                )
                assert with_executions is not None
                approval_execution = next(
                    (
                        execution
                        for execution in with_executions[1]
                        if execution.node_type is NodeType.APPROVAL
                    ),
                    None,
                )
                if approval_execution is None:
                    raise RuntimeError(
                        f"No approval node execution found for workflow run {run.id}."
                    )
                edited_arguments = (
                    dict(case.hitl_edited_arguments)
                    if case.hitl_decision == "approve_with_edits"
                    else None
                )
                _, _ = await manager.apply_decision(
                    run.id,
                    approval_execution.id,
                    owner_id=owner_id,
                    decision=ApprovalDecision.APPROVED,
                    edited_arguments=edited_arguments,
                )
                manager.flush_deferred_run_schedules()
                await _await_scheduled_run(manager)
                final_run = await manager.get_run(run.id, owner_id=owner_id)
                if final_run is None:
                    raise RuntimeError(f"Workflow run {run.id} missing after decision.")
                terminal_status = final_run.status.value

            expected_status = case.expected_terminal_status or ""
            passed = terminal_status == expected_status
            latency_ms = int((time.perf_counter() - start) * 1000)
            return EvalCaseResult(
                case_id=case.id,
                level="hitl",
                passed=passed,
                latency_ms=latency_ms,
                terminal_status=terminal_status,
            )
        except Exception as exc:
            await self.session.rollback()
            latency_ms = int((time.perf_counter() - start) * 1000)
            return EvalCaseResult(
                case_id=case.id,
                level="hitl",
                passed=False,
                latency_ms=latency_ms,
                error=str(exc),
            )
        finally:
            if owner_id is not None:
                try:
                    await _cleanup_eval_workflow_owner(self.session, owner_id)
                except Exception:
                    _logger.warning(
                        "Eval HITL workflow owner cleanup failed",
                        owner_id=str(owner_id),
                        exc_info=True,
                    )

    async def _create_user(self) -> uuid.UUID:
        if self.session is None:
            raise RuntimeError("Postgres session is required for HITL workflow cases.")
        user = await SqlUserStore(self.session).create(
            sub=f"eval-hitl-{uuid.uuid4()}",
            email=None,
            name=None,
            picture=None,
        )
        return user.id


def _approval_policy_for_settings(settings: Settings) -> ApprovalPolicy | None:
    """Wire HITL policy when the flag is on so eval agents match production."""
    if not settings.hitl_enabled:
        return None
    return ApprovalPolicy(
        required_tool_names=frozenset(settings.hitl_required_tool_names)
    )


def _hitl_error_result(*, case_id: str, start: float, message: str) -> EvalCaseResult:
    latency_ms = int((time.perf_counter() - start) * 1000)
    return EvalCaseResult(
        case_id=case_id,
        level="hitl",
        passed=False,
        latency_ms=latency_ms,
        error=message,
    )


def _hitl_eval_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(SEND_NOTIFICATION_TOOL_DEFINITION, SendNotificationHandler())
    registry.register(ECHO_TOOL_DEFINITION, EchoToolHandler())
    return registry


def _hitl_agent_provider(case: EvalCase) -> _AgentEvalProvider:
    tool_completions = [
        ProviderToolCompletion(
            content="Sending notification.",
            tool_calls=[
                ProviderToolCall(
                    id="call-hitl-1",
                    name=SEND_NOTIFICATION_TOOL_NAME,
                    arguments={"message": "hello", "channel": "email"},
                )
            ],
        ),
        ProviderToolCompletion(
            content=case.expected_outcome or "Notification sent.",
            tool_calls=[],
            finish_reason="stop",
            usage=ProviderUsage(
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
            ),
        ),
    ]
    return _AgentEvalProvider(
        tool_completions=tool_completions,
        model=case.model or "gpt-4o-mini",
        temperature=case.temperature or 0.7,
    )


def _edited_calls_from_case(case: EvalCase) -> list[ProposedToolCall] | None:
    if case.hitl_decision != "approve_with_edits":
        return None
    if not case.hitl_edited_calls:
        return [
            ProposedToolCall(
                name=SEND_NOTIFICATION_TOOL_NAME,
                arguments={"message": "edited", "channel": "email"},
                call_id="call-hitl-1",
            )
        ]
    parsed: list[ProposedToolCall] = []
    for index, raw in enumerate(case.hitl_edited_calls):
        name = raw.get("name")
        arguments = raw.get("arguments")
        call_id = raw.get("call_id")
        if not isinstance(name, str) or not isinstance(arguments, dict):
            raise ValueError(f"Invalid hitl_edited_calls entry at index {index}.")
        parsed.append(
            ProposedToolCall(
                name=name,
                arguments=arguments,
                call_id=str(call_id or f"call-hitl-{index + 1}"),
            )
        )
    return parsed


def _hitl_resume_executor(
    *,
    registry: ToolRegistry,
    approval_service: AgentApprovalService,
    provider: _AgentEvalProvider,
    eval_settings: Settings,
) -> AgentExecutor:
    tool_executor = ToolExecutor(registry=registry, settings=eval_settings)
    scratchpad_store = ScratchpadStore()
    runner = ToolRunner(
        tool_executor=tool_executor,
        tool_registry=registry,
        stream_publisher=NoOpStreamPublisher(),
        hitl_enabled=True,
        approval_policy=ApprovalPolicy(
            required_tool_names=frozenset(eval_settings.hitl_required_tool_names)
        ),
        approval_service=approval_service,
    )
    return AgentExecutor(
        planner=ReActPlanner(
            provider=provider,
            tool_registry=registry,
            prompt_manager=create_prompt_manager(),
            scratchpad_store=scratchpad_store,
        ),
        provider=provider,
        tool_runner=runner,
        stream_publisher=NoOpStreamPublisher(),
        scratchpad_store=scratchpad_store,
        prompt_manager=create_prompt_manager(),
    )


def _build_hitl_eval_workflow_manager(
    *,
    session: AsyncSession,
    settings: Settings,
) -> WorkflowManager:
    registry = _hitl_eval_tool_registry()
    prompt_manager = create_prompt_manager()
    tool_executor = ToolExecutor(registry=registry, settings=settings)
    agent_runtime = create_default_agent(
        settings=settings,
        tool_registry=registry,
        prompt_manager=prompt_manager,
        tool_executor=tool_executor,
        approval_policy=_approval_policy_for_settings(settings),
    )
    store = PostgresWorkflowStore(session=session, settings=settings)

    def background_store_factory(
        bg_session: AsyncSession,
    ) -> PostgresWorkflowStore:
        return PostgresWorkflowStore(session=bg_session, settings=settings)

    return WorkflowManager(
        store=store,
        settings=settings,
        node_executors={
            NodeType.TASK: TaskNodeExecutor(tool_executor),
            NodeType.LLM: LLMNodeExecutor(
                prompt_manager=prompt_manager,
                settings=settings,
            ),
            NodeType.AGENT: AgentNodeExecutor(agent_runtime, settings=settings),
            NodeType.ROUTER: RouterNodeExecutor(ConditionEvaluator()),
            NodeType.FORK: ForkNodeExecutor(
                max_parallel_branches=settings.workflow_max_parallel_branches
            ),
            NodeType.JOIN: JoinNodeExecutor(),
            NodeType.APPROVAL: ApprovalNodeExecutor(),
        },
        background_store_factory=background_store_factory,
        tool_registry=registry,
    )


def _plugin_error_result(*, case_id: str, start: float, message: str) -> EvalCaseResult:
    latency_ms = int((time.perf_counter() - start) * 1000)
    return EvalCaseResult(
        case_id=case_id,
        level="plugin",
        passed=False,
        latency_ms=latency_ms,
        error=message,
    )


def _load_reference_plugin_registries(
    *,
    settings: Settings,
    plugins_root: Path,
) -> tuple[ToolRegistry, PromptRepository, PluginRegistry, WorkflowPluginRegistry]:
    tool_registry = ToolRegistry()
    prompt_repository = PromptRepository()
    plugin_registry = PluginRegistry()
    workflow_plugin_registry = WorkflowPluginRegistry()
    plugin_settings = settings.model_copy(
        update={
            "plugins_enabled": True,
            "plugin_directories": [str(plugins_root)],
        }
    )
    orchestrate_load_plugins(
        plugin_settings,
        tool_registry=tool_registry,
        prompt_repository=prompt_repository,
        plugin_registry=plugin_registry,
        workflow_plugin_registry=workflow_plugin_registry,
    )
    return tool_registry, prompt_repository, plugin_registry, workflow_plugin_registry


def _build_plugin_eval_workflow_manager(
    *,
    session: AsyncSession,
    settings: Settings,
    tool_registry: ToolRegistry,
    prompt_repository: PromptRepository,
    plugin_registry: PluginRegistry,
    workflow_plugin_registry: WorkflowPluginRegistry,
) -> WorkflowManager:
    prompt_manager = PromptManager(repository=prompt_repository)
    tool_executor = ToolExecutor(registry=tool_registry, settings=settings)
    agent_runtime = create_default_agent(
        settings=settings,
        tool_registry=tool_registry,
        prompt_manager=prompt_manager,
        tool_executor=tool_executor,
        approval_policy=_approval_policy_for_settings(settings),
    )
    store = PostgresWorkflowStore(session=session, settings=settings)

    def background_store_factory(
        bg_session: AsyncSession,
    ) -> PostgresWorkflowStore:
        return PostgresWorkflowStore(session=bg_session, settings=settings)

    node_executors = {
        NodeType.TASK: TaskNodeExecutor(tool_executor),
        NodeType.LLM: LLMNodeExecutor(
            prompt_manager=prompt_manager,
            settings=settings,
        ),
        NodeType.AGENT: AgentNodeExecutor(agent_runtime, settings=settings),
        NodeType.ROUTER: RouterNodeExecutor(ConditionEvaluator()),
        NodeType.FORK: ForkNodeExecutor(
            max_parallel_branches=settings.workflow_max_parallel_branches
        ),
        NodeType.JOIN: JoinNodeExecutor(),
        NodeType.APPROVAL: ApprovalNodeExecutor(),
        NodeType.PLUGIN: PluginNodeExecutor(
            workflow_plugin_registry=workflow_plugin_registry,
            settings=settings,
        ),
    }

    return WorkflowManager(
        store=store,
        settings=settings,
        node_executors=node_executors,
        background_store_factory=background_store_factory,
        tool_registry=tool_registry,
        plugin_registry=plugin_registry,
        workflow_plugin_registry=workflow_plugin_registry,
    )


async def _cleanup_eval_workflow_owner(
    session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    """Delete eval-scoped workflow artifacts committed during ``run_case``."""
    try:
        await session.execute(delete(User).where(User.id == owner_id))
        await session.commit()
    except Exception:
        await session.rollback()


async def postgres_available(session: AsyncSession) -> bool:
    """Return True when Postgres accepts a simple connectivity probe."""
    try:
        await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def pgvector_available(session: AsyncSession) -> bool:
    """Return True when the pgvector extension is installed."""
    try:
        result = await session.scalar(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        )
        return result == 1
    except Exception:
        return False


def _messages_from_llm(llm: LLMProvider) -> list[ChatMessageSchema]:
    if isinstance(llm, _EvalLLMProvider):
        return llm.last_messages
    return []


def _extract_context(messages: list[ChatMessageSchema]) -> str:
    for message in messages:
        if message.role != "user":
            continue
        match = re.search(r"Context:\s*(.+?)\n\nQuestion:", message.content, re.S)
        if match:
            return match.group(1).strip()
        return message.content
    return ""


def _parse_judge_response(content: str) -> tuple[bool, bool]:
    try:
        payload = json.loads(content.strip())
    except json.JSONDecodeError as exc:
        raise ValueError(f"Judge response is not valid JSON: {content}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Judge response must be a JSON object.")
    faithful = payload.get("faithful")
    hallucination = payload.get("hallucination")
    if not isinstance(faithful, bool) or not isinstance(hallucination, bool):
        raise ValueError("Judge response must include boolean faithful/hallucination.")
    return faithful, hallucination


def _guess_mime_type(filename: str) -> str | None:
    lowered = filename.lower()
    if lowered.endswith(".txt"):
        return "text/plain"
    if lowered.endswith(".md"):
        return "text/markdown"
    if lowered.endswith(".pdf"):
        return "application/pdf"
    if lowered.endswith(".docx"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return None


def _agent_user_content(case: EvalCase) -> str:
    parts = [case.goal or ""]
    if case.instructions:
        parts.append(case.instructions)
    return "\n\n".join(part for part in parts if part.strip())


def _build_agent_eval_provider(
    case: EvalCase,
    *,
    model: str,
    temperature: float,
) -> _AgentEvalProvider:
    expected_tools = _agent_eval_expected_tool_calls(case)
    tool_completions: list[ProviderToolCompletion] = []
    for index, _tool_name in enumerate(expected_tools):
        tool_completions.append(
            ProviderToolCompletion(
                content="Calling echo.",
                tool_calls=[
                    ProviderToolCall(
                        id=f"call-echo-{index}",
                        name="echo",
                        arguments={"message": case.goal or "hello"},
                    )
                ],
            )
        )
    tool_completions.append(
        ProviderToolCompletion(
            content=case.expected_outcome or "Done.",
            tool_calls=[],
            finish_reason="stop",
            usage=ProviderUsage(
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
            ),
        ),
    )
    return _AgentEvalProvider(
        tool_completions=tool_completions,
        model=model,
        temperature=temperature,
    )


def _agent_eval_expected_tool_calls(case: EvalCase) -> list[str]:
    """Return scripted tool-call sequence for an agent eval case."""
    expected_tools = (
        list(case.expected_tool_calls) if case.expected_tool_calls else ["echo"]
    )
    unsupported = [
        tool_name
        for tool_name in expected_tools
        if tool_name not in _AGENT_EVAL_SUPPORTED_TOOLS
    ]
    if unsupported:
        unsupported_text = ", ".join(sorted(set(unsupported)))
        supported_text = ", ".join(sorted(_AGENT_EVAL_SUPPORTED_TOOLS))
        raise ValueError(
            f"Agent eval case '{case.id}' lists unsupported expected_tool_calls "
            f"({unsupported_text}); harness only supports: {supported_text}."
        )
    return expected_tools


def _workflow_definition_from_case(
    case: EvalCase,
    *,
    owner_id: uuid.UUID,
) -> WorkflowDefinition:
    if case.workflow_definition is not None:
        spec = case.workflow_definition
    elif case.workflow_fixture is not None:
        spec = load_workflow_fixture(case.workflow_fixture)
    else:
        raise ValueError("Workflow case missing definition spec.")

    now = datetime.datetime.now(datetime.UTC)
    nodes_raw = spec.get("nodes")
    nodes = (
        [
            WorkflowNode.model_validate(node)
            for node in nodes_raw
            if isinstance(node, dict)
        ]
        if isinstance(nodes_raw, list)
        else []
    )
    edges_raw = spec.get("edges")
    edges = (
        [
            WorkflowEdge.model_validate(edge)
            for edge in edges_raw
            if isinstance(edge, dict)
        ]
        if isinstance(edges_raw, list)
        else []
    )
    status_raw = spec.get("status", DefinitionStatus.ACTIVE.value)
    status = (
        DefinitionStatus(status_raw)
        if isinstance(status_raw, str)
        else DefinitionStatus.ACTIVE
    )
    version_raw = spec.get("version", 1)
    version = version_raw if isinstance(version_raw, int) else 1
    metadata_raw = spec.get("metadata", {})
    metadata: dict[str, object] = (
        dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
    )
    return WorkflowDefinition(
        id=uuid.uuid4(),
        owner_id=owner_id,
        name=str(spec.get("name", f"Eval Workflow {case.id}")),
        description=(
            str(spec["description"])
            if isinstance(spec.get("description"), str)
            else None
        ),
        version=version,
        status=status,
        entry_node_id=str(spec["entry_node_id"]),
        nodes=nodes,
        edges=edges,
        metadata=metadata,
        created_at=now,
        updated_at=now,
    )


def _build_eval_workflow_manager(
    *,
    session: AsyncSession,
    settings: Settings,
) -> WorkflowManager:
    registry = ToolRegistry()
    registry.register(ECHO_TOOL_DEFINITION, EchoToolHandler())
    prompt_manager = create_prompt_manager()
    tool_executor = ToolExecutor(registry=registry, settings=settings)
    agent_runtime = create_default_agent(
        settings=settings,
        tool_registry=registry,
        prompt_manager=prompt_manager,
        tool_executor=tool_executor,
        approval_policy=_approval_policy_for_settings(settings),
    )
    store = PostgresWorkflowStore(session=session, settings=settings)

    def background_store_factory(
        bg_session: AsyncSession,
    ) -> PostgresWorkflowStore:
        return PostgresWorkflowStore(session=bg_session, settings=settings)

    return WorkflowManager(
        store=store,
        settings=settings,
        node_executors={
            NodeType.TASK: TaskNodeExecutor(tool_executor),
            NodeType.LLM: LLMNodeExecutor(
                prompt_manager=prompt_manager,
                settings=settings,
            ),
            NodeType.AGENT: AgentNodeExecutor(agent_runtime, settings=settings),
            NodeType.ROUTER: RouterNodeExecutor(ConditionEvaluator()),
            NodeType.FORK: ForkNodeExecutor(
                max_parallel_branches=settings.workflow_max_parallel_branches
            ),
            NodeType.JOIN: JoinNodeExecutor(),
            NodeType.APPROVAL: ApprovalNodeExecutor(),
        },
        background_store_factory=background_store_factory,
        tool_registry=registry,
    )


async def _await_scheduled_run(manager: WorkflowManager) -> None:
    task = manager._last_scheduled_run_task
    if task is not None:
        await task


def _workflow_reproducibility_metadata(
    definition: WorkflowDefinition,
) -> tuple[str | None, str | None]:
    model: str | None = None
    prompt_version: str | None = None
    for node in definition.nodes:
        if node.type is NodeType.LLM:
            config = node.config
            model_value = config.get("model")
            if isinstance(model_value, str):
                model = model_value
            prompt_version_value = config.get("prompt_version")
            if isinstance(prompt_version_value, str):
                prompt_version = prompt_version_value
        if node.type is NodeType.AGENT:
            config = node.config
            model_value = config.get("model")
            if isinstance(model_value, str):
                model = model_value
    return model, prompt_version


@dataclass(frozen=True)
class JobsEvalRunner:
    """Run Background Jobs reference scenarios for each first-class handler."""

    settings: Settings
    session: AsyncSession | None = None

    async def run_case(self, case: EvalCase) -> EvalCaseResult:
        from app.ai.evaluation.jobs_scenarios import run_jobs_reference_scenario

        start = time.perf_counter()
        if not self.settings.background_jobs_enabled:
            return EvalCaseResult(
                case_id=case.id,
                level="jobs",
                passed=False,
                latency_ms=0,
                skipped=True,
                skip_reason="BACKGROUND_JOBS_ENABLED=false",
            )

        if self.session is None:
            return EvalCaseResult(
                case_id=case.id,
                level="jobs",
                passed=False,
                latency_ms=0,
                skipped=True,
                skip_reason="Postgres not available (run from backend-python with DB up)",
            )

        try:
            outcome = await run_jobs_reference_scenario(
                case,
                session=self.session,
                settings=self.settings,
            )
            latency_ms = int((time.perf_counter() - start) * 1000)
            return EvalCaseResult(
                case_id=case.id,
                level="jobs",
                passed=outcome.passed,
                latency_ms=latency_ms,
                error=outcome.error,
            )
        except Exception as exc:
            await self.session.rollback()
            latency_ms = int((time.perf_counter() - start) * 1000)
            return EvalCaseResult(
                case_id=case.id,
                level="jobs",
                passed=False,
                latency_ms=latency_ms,
                error=str(exc),
            )
