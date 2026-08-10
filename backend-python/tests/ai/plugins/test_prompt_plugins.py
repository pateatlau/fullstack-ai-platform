"""Prompt plugin integration tests (Epic 08 Phase 3)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.ai.observability.tracing.provider import TracerRegistry
from app.ai.plugins import PluginContributionKind, PluginStatus
from app.ai.plugins.exceptions import PluginRegistrationError
from app.ai.plugins.registrar import PluginRegistrar
from app.ai.prompts.manager import PromptManager
from app.ai.prompts.repository import PromptRepository
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.schemas import ToolDefinition, ToolExecutionContext, ToolResult
from app.core.config import Settings
from tests.ai.plugins.conftest import load_plugins, plugin_settings

PLUGIN_ID = "com.test.prompt"
GREETING_CATEGORY = f"plugin/{PLUGIN_ID}"


@pytest.fixture
def prompt_repository() -> PromptRepository:
    return PromptRepository()


@pytest.fixture
def prompt_manager(prompt_repository: PromptRepository) -> PromptManager:
    return PromptManager(repository=prompt_repository)


class TestPromptRepositoryLookup:
    def test_filesystem_precedence_over_plugin_overlay(self, tmp_path: Path) -> None:
        prompts_root = tmp_path / "prompts"
        category_dir = prompts_root / "plugin" / "com.test.precedence"
        category_dir.mkdir(parents=True)
        (category_dir / "greeting.v1.j2").write_text(
            "Filesystem {{ user_name }}",
            encoding="utf-8",
        )
        repository = PromptRepository(prompts_root=prompts_root)
        repository._plugin_overlay[("plugin/com.test.precedence", "greeting", "1")] = (
            "Overlay {{ user_name }}"
        )

        template = repository.get_template(
            "plugin/com.test.precedence", "greeting", "1"
        )
        rendered = template.render({"user_name": "Win"})

        assert rendered == "Filesystem Win"


class TestPromptPluginLoad:
    def test_prompt_registered_in_repository(
        self,
        prompt_repository: PromptRepository,
    ) -> None:
        _, registry, _, prompts = load_plugins(
            plugin_settings(allowlist=[PLUGIN_ID]),
            prompt_repository=prompt_repository,
        )
        record = registry.get(PLUGIN_ID)
        assert record is not None
        assert record.status == PluginStatus.LOADED
        assert PluginContributionKind.PROMPT in record.contributions
        assert prompts.list_plugin_templates() == sorted(
            [
                (GREETING_CATEGORY, "greeting", "1"),
                (GREETING_CATEGORY, "farewell", "1"),
            ]
        )

    def test_collision_with_existing_prompt_fails_plugin(
        self,
        tmp_path: Path,
    ) -> None:
        prompts_root = tmp_path / "prompts"
        collision_dir = prompts_root / "plugin" / "com.test.prompt.collision"
        collision_dir.mkdir(parents=True)
        (collision_dir / "greeting.v1.j2").write_text(
            "Existing built-in filesystem prompt.",
            encoding="utf-8",
        )
        repo = PromptRepository(prompts_root=prompts_root)
        _, registry, _, prompts = load_plugins(
            plugin_settings(allowlist=["com.test.prompt.collision"]),
            prompt_repository=repo,
        )
        record = registry.get("com.test.prompt.collision")
        assert record is not None
        assert record.status == PluginStatus.FAILED
        assert record.failure is not None
        assert record.failure.code == "registration_error"
        assert "already registered" in record.failure.message
        assert prompts.list_plugin_templates() == []

    def test_path_traversal_rejected(
        self,
        prompt_repository: PromptRepository,
    ) -> None:
        _, registry, _, _ = load_plugins(
            plugin_settings(allowlist=["com.test.prompt.badpath"]),
            prompt_repository=prompt_repository,
        )
        record = registry.get("com.test.prompt.badpath")
        assert record is not None
        assert record.status == PluginStatus.FAILED
        assert record.failure is not None
        assert record.failure.code == "registration_error"
        assert ".." in record.failure.message

    def test_flag_off_prompt_not_registered(
        self,
        prompt_repository: PromptRepository,
    ) -> None:
        load_plugins(
            plugin_settings(
                enabled=False,
                allowlist=[PLUGIN_ID],
            ),
            prompt_repository=prompt_repository,
        )
        assert prompt_repository.list_plugin_templates() == []


class TestPromptPluginRender:
    def test_inline_template_renders_via_prompt_manager(
        self,
        prompt_manager: PromptManager,
        prompt_repository: PromptRepository,
    ) -> None:
        load_plugins(
            plugin_settings(allowlist=[PLUGIN_ID]),
            prompt_repository=prompt_repository,
        )
        rendered = prompt_manager.render(
            GREETING_CATEGORY,
            "greeting",
            "1",
            {"user_name": "Ada"},
        )
        assert rendered == "Hello Ada!"

    def test_file_template_renders_via_prompt_manager(
        self,
        prompt_manager: PromptManager,
        prompt_repository: PromptRepository,
    ) -> None:
        load_plugins(
            plugin_settings(allowlist=[PLUGIN_ID]),
            prompt_repository=prompt_repository,
        )
        rendered = prompt_manager.render(
            GREETING_CATEGORY,
            "farewell",
            "1",
            {"user_name": "Grace"},
        )
        assert rendered == "Goodbye Grace!"


class TestRegistrarPromptValidation:
    def test_path_traversal_rejected_at_commit(self, tmp_path: Path) -> None:
        registrar = PluginRegistrar(
            plugin_id="com.test.path",
            plugin_dir=tmp_path,
            prompt_repository=PromptRepository(),
        )
        registrar.register_prompt_template(
            name="bad",
            version="1",
            path="../escape.v1.j2",
        )
        with pytest.raises(PluginRegistrationError, match="\\.\\."):
            registrar.commit()

    def test_commit_rolls_back_partial_registrations_on_unexpected_error(
        self,
        tmp_path: Path,
    ) -> None:
        tool_registry = ToolRegistry()
        prompt_repository = PromptRepository()
        registrar = PluginRegistrar(
            plugin_id="com.test.rollback",
            plugin_dir=tmp_path,
            tool_registry=tool_registry,
            prompt_repository=prompt_repository,
        )

        class _Handler:
            async def execute(
                self,
                args: dict[str, object],
                context: ToolExecutionContext,
            ) -> ToolResult:
                del args, context
                return ToolResult(success=True)

        tool_name = "com.test.rollback.echo"
        registrar.register_tool(
            ToolDefinition(name=tool_name, description="echo", parameters={}),
            _Handler(),
        )
        registrar.register_prompt_template(
            name="first",
            version="1",
            source="First {{ name }}",
        )
        registrar.register_prompt_template(
            name="second",
            version="1",
            source="Second {{ name }}",
        )

        original_register = prompt_repository.register_plugin_template

        def _register_with_failure(**kwargs: object) -> None:
            if kwargs.get("name") == "second":
                raise OSError("Simulated template read failure")
            original_register(**kwargs)  # type: ignore[arg-type]

        with (
            patch.object(
                prompt_repository,
                "register_plugin_template",
                side_effect=_register_with_failure,
            ),
            pytest.raises(PluginRegistrationError, match="Simulated template read"),
        ):
            registrar.commit()

        assert tool_registry.get(tool_name) is None
        assert prompt_repository.list_plugin_templates() == []


class TestPromptPluginObservability:
    @pytest.fixture(autouse=True)
    def _reset_tracer_registry(self) -> Iterator[None]:
        TracerRegistry.reset_for_tests()
        yield
        TracerRegistry.reset_for_tests()

    @pytest.fixture
    def memory_exporter(self) -> InMemorySpanExporter:
        exporter = InMemorySpanExporter()
        settings = Settings(openai_api_key="test-key", observability_enabled=True)
        TracerRegistry.initialize(
            settings,
            extra_span_processors=[SimpleSpanProcessor(exporter)],
        )
        return exporter

    def test_prompt_span_wraps_plugin_render(
        self,
        memory_exporter: InMemorySpanExporter,
        prompt_manager: PromptManager,
        prompt_repository: PromptRepository,
    ) -> None:
        load_plugins(
            plugin_settings(allowlist=[PLUGIN_ID]),
            prompt_repository=prompt_repository,
        )
        rendered = prompt_manager.render(
            GREETING_CATEGORY,
            "greeting",
            "1",
            {"user_name": "Span"},
        )
        assert rendered == "Hello Span!"
        spans = memory_exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "prompt.render"
        attributes = dict(spans[0].attributes or {})
        assert attributes["category"] == GREETING_CATEGORY
        assert attributes["name"] == "greeting"
        assert attributes["version"] == "1"

    def test_render_without_observability_emits_no_spans(
        self,
        prompt_manager: PromptManager,
        prompt_repository: PromptRepository,
    ) -> None:
        TracerRegistry.initialize(
            Settings(openai_api_key="test-key", observability_enabled=False),
        )
        assert TracerRegistry.is_enabled() is False

        with patch("app.ai.observability.tracing.spans.get_tracer") as get_tracer_mock:
            load_plugins(
                plugin_settings(allowlist=[PLUGIN_ID]),
                prompt_repository=prompt_repository,
            )
            rendered = prompt_manager.render(
                GREETING_CATEGORY,
                "greeting",
                "1",
                {"user_name": "Off"},
            )

        assert rendered == "Hello Off!"
        get_tracer_mock.assert_not_called()
