"""CLI entry point for the evaluation framework."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.ai.evaluation.datasets import EvalLevel, filter_cases, load_dataset
from app.ai.evaluation.report import (
    EvalRunEnvironment,
    EvalRunReport,
    print_console_summary,
    write_json_report,
)
from app.ai.evaluation.regression import (
    RegressionChecker,
    load_baseline_report,
    print_regression_summary,
    write_regression_result,
)
from app.ai.evaluation.runners import (
    AgentEvalRunner,
    EndToEndEvalRunner,
    HitlEvalRunner,
    PluginEvalRunner,
    PromptEvalRunner,
    RetrievalEvalRunner,
    WorkflowEvalRunner,
    pgvector_available,
    postgres_available,
)
from app.ai.prompts.manager import create_prompt_manager
from app.core.config import Settings, get_settings

DEFAULT_DATASET = Path("tests/data/evaluation/sample.yaml")
DEFAULT_OUTPUT = Path(".eval/eval-report.json")
DEFAULT_BASELINE = Path("tests/data/evaluation/baseline-report.json")
DEFAULT_REGRESSION_OUTPUT = Path(".eval/regression-result.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run prompt, retrieval, e2e, agent, workflow, and plugin eval."
    )
    parser.add_argument(
        "--level",
        choices=[
            "prompt",
            "retrieval",
            "e2e",
            "agent",
            "workflow",
            "plugin",
            "hitl",
            "all",
        ],
        default="all",
        help=(
            "Evaluation level to run (default: all). "
            "Use --level plugin for reference plugin smoke cases "
            "(skipped when PLUGINS_ENABLED=false). "
            "Use --level hitl for HITL reference scenarios "
            "(skipped when HITL_ENABLED=false)."
        ),
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help="Path to YAML/JSON eval dataset.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="JSON report output path.",
    )
    parser.add_argument(
        "--use-judge",
        action="store_true",
        help="Enable LLM-as-judge for e2e faithfulness/hallucination checks.",
    )
    parser.add_argument(
        "--check-regression",
        type=Path,
        default=None,
        metavar="BASELINE",
        help="Compare this run against a baseline report; non-zero exit on regression.",
    )
    parser.add_argument(
        "--regression-output",
        type=Path,
        default=DEFAULT_REGRESSION_OUTPUT,
        help="JSON regression findings output path (used with --check-regression).",
    )
    parser.add_argument(
        "--update-baseline",
        nargs="?",
        const=DEFAULT_BASELINE,
        default=None,
        type=Path,
        metavar="PATH",
        help=(
            "Write a git-tracked baseline report (requires --level all and "
            "enabled agent/workflow runtimes with Postgres/pgvector)."
        ),
    )
    return parser


def _settings_snapshot(settings: Settings) -> dict[str, object]:
    return {
        "llm_provider": settings.llm_provider,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "embedding_dimensions": settings.embedding_dimensions,
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "rag_top_k": settings.rag_top_k,
        "rag_context_max_chars": settings.rag_context_max_chars,
        "default_temperature": settings.default_temperature,
        "agent_runtime_enabled": settings.agent_runtime_enabled,
        "workflow_engine_enabled": settings.workflow_engine_enabled,
        "plugins_enabled": settings.plugins_enabled,
        "hitl_enabled": settings.hitl_enabled,
    }


def _levels_to_run(level: str) -> set[EvalLevel]:
    if level == "all":
        return {"prompt", "retrieval", "e2e", "agent", "workflow"}
    return {level}  # type: ignore[return-value]


def _all_level_prerequisite_error(
    *,
    settings: Settings,
    postgres_ok: bool,
    pgvector_ok: bool,
) -> str | None:
    if not settings.agent_runtime_enabled:
        return (
            "--level all requires AGENT_RUNTIME_ENABLED=true "
            "(target --level agent to skip when disabled)."
        )
    if not settings.workflow_engine_enabled:
        return (
            "--level all requires WORKFLOW_ENGINE_ENABLED=true "
            "(target --level workflow to skip when disabled)."
        )
    if not postgres_ok:
        return "--level all requires Postgres (run from backend-python with DB up)."
    if not pgvector_ok:
        return "--level all requires the pgvector extension."
    return None


async def _probe_postgres(
    settings: Settings,
) -> tuple[bool, bool, AsyncSession | None, AsyncEngine | None]:
    """Return postgres/pgvector availability and an open session when Postgres is up."""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    session: AsyncSession | None = None
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        session = factory()
        if not await postgres_available(session):
            await session.close()
            await engine.dispose()
            return False, False, None, None
        pgvector_ok = await pgvector_available(session)
        return True, pgvector_ok, session, engine
    except Exception:
        if session is not None:
            await session.close()
        await engine.dispose()
        return False, False, None, None


async def _dispose_db_resources(
    session: AsyncSession | None,
    engine: AsyncEngine | None,
    *,
    rollback: bool = False,
) -> None:
    if session is not None:
        if rollback:
            await session.rollback()
        await session.close()
    if engine is not None:
        await engine.dispose()


async def _run_with_session(
    *,
    settings: Settings,
    dataset_path: Path,
    levels: set[EvalLevel],
    use_judge: bool,
    level_arg: str,
) -> tuple[EvalRunReport, int | None]:
    dataset = load_dataset(dataset_path)
    prompt_manager = create_prompt_manager()
    report = EvalRunReport(
        dataset_path=str(dataset_path),
        settings_snapshot=_settings_snapshot(settings),
    )

    postgres_ok = False
    pgvector_ok = False
    session: AsyncSession | None = None
    engine: AsyncEngine | None = None

    db_levels = levels & {"retrieval", "e2e", "workflow"}
    if db_levels or "plugin" in levels or "hitl" in levels:
        postgres_ok, pgvector_ok, session, engine = await _probe_postgres(settings)

    report.run_environment = EvalRunEnvironment(
        agent_runtime_enabled=settings.agent_runtime_enabled,
        workflow_engine_enabled=settings.workflow_engine_enabled,
        plugins_enabled=settings.plugins_enabled,
        hitl_enabled=settings.hitl_enabled,
        postgres_available=postgres_ok,
        pgvector_available=pgvector_ok,
    )

    if level_arg == "all":
        prerequisite_error = _all_level_prerequisite_error(
            settings=settings,
            postgres_ok=postgres_ok,
            pgvector_ok=pgvector_ok,
        )
        if prerequisite_error is not None:
            print(prerequisite_error, file=sys.stderr)
            await _dispose_db_resources(session, engine)
            return report, 2

    if "prompt" in levels:
        runner = PromptEvalRunner(prompt_manager=prompt_manager)
        for case in filter_cases(dataset, "prompt"):
            report.results.append(runner.run_case(case))

    if "agent" in levels:
        agent_runner = AgentEvalRunner(
            settings=settings,
            prompt_manager=prompt_manager,
        )
        for case in filter_cases(dataset, "agent"):
            report.results.append(await agent_runner.run_case(case))

    if "plugin" in levels:
        plugin_runner = PluginEvalRunner(
            settings=settings,
            session=session,
        )
        for case in filter_cases(dataset, "plugin"):
            report.results.append(await plugin_runner.run_case(case))

    if "hitl" in levels:
        hitl_runner = HitlEvalRunner(
            settings=settings,
            prompt_manager=prompt_manager,
            session=session,
        )
        for case in filter_cases(dataset, "hitl"):
            report.results.append(await hitl_runner.run_case(case))

    if not db_levels:
        await _dispose_db_resources(session, engine, rollback=True)
        return report, None

    if not postgres_ok:
        for level in sorted(db_levels):
            for case in filter_cases(dataset, level):  # type: ignore[arg-type]
                report.results.append(
                    _skipped_result(
                        case_id=case.id,
                        level=level,  # type: ignore[arg-type]
                        reason="Postgres not available (run from backend-python with DB up)",
                    )
                )
        report.skipped_levels.append(
            f"{'/'.join(sorted(db_levels))} skipped — Postgres unavailable"
        )
        await _dispose_db_resources(session, engine, rollback=True)
        return report, None

    if session is None or engine is None:
        await _dispose_db_resources(session, engine, rollback=True)
        return report, None

    if levels & {"retrieval", "e2e"} and not pgvector_ok:
        for level in sorted(levels & {"retrieval", "e2e"}):
            for case in filter_cases(dataset, level):  # type: ignore[arg-type]
                report.results.append(
                    _skipped_result(
                        case_id=case.id,
                        level=level,  # type: ignore[arg-type]
                        reason="pgvector extension not available",
                    )
                )
        report.skipped_levels.append(
            "retrieval/e2e skipped — pgvector extension not installed"
        )
    else:
        if "retrieval" in levels:
            retrieval_runner = RetrievalEvalRunner(session=session, settings=settings)
            for case in filter_cases(dataset, "retrieval"):
                report.results.append(await retrieval_runner.run_case(case))

        if "e2e" in levels:
            e2e_runner = EndToEndEvalRunner(
                session=session,
                settings=settings,
                prompt_manager=prompt_manager,
                use_judge=use_judge,
            )
            for case in filter_cases(dataset, "e2e"):
                report.results.append(await e2e_runner.run_case(case))

    if "workflow" in levels:
        if not pgvector_ok:
            for case in filter_cases(dataset, "workflow"):
                report.results.append(
                    _skipped_result(
                        case_id=case.id,
                        level="workflow",
                        reason="pgvector extension not available",
                    )
                )
            report.skipped_levels.append(
                "workflow skipped — pgvector extension not installed"
            )
        else:
            workflow_runner = WorkflowEvalRunner(session=session, settings=settings)
            for case in filter_cases(dataset, "workflow"):
                report.results.append(await workflow_runner.run_case(case))

    await _dispose_db_resources(session, engine, rollback=True)
    return report, None


def _skipped_result(*, case_id: str, level: EvalLevel, reason: str):
    from app.ai.evaluation.report import EvalCaseResult

    return EvalCaseResult(
        case_id=case_id,
        level=level,
        passed=False,
        latency_ms=0,
        skipped=True,
        skip_reason=reason,
    )


def _skipped_agent_workflow_results(report: EvalRunReport) -> list[str]:
    reasons: list[str] = []
    for result in report.results:
        if result.level in {"agent", "workflow"} and result.skipped:
            skip_reason = result.skip_reason or "skipped"
            reasons.append(f"{result.case_id}: {skip_reason}")
    return reasons


def _baseline_eligible_environment(report: EvalRunReport) -> str | None:
    env = report.run_environment
    if env is None:
        return "run_environment was not captured"
    if not env.agent_runtime_enabled:
        return "AGENT_RUNTIME_ENABLED=false"
    if not env.workflow_engine_enabled:
        return "WORKFLOW_ENGINE_ENABLED=false"
    if not env.postgres_available:
        return "Postgres unavailable"
    if not env.pgvector_available:
        return "pgvector extension unavailable"
    return None


async def run_eval(args: argparse.Namespace) -> int:
    if args.update_baseline is not None and args.level != "all":
        print(
            "--update-baseline requires --level all.",
            file=sys.stderr,
        )
        return 2

    get_settings.cache_clear()
    settings = get_settings()
    levels = _levels_to_run(args.level)
    report, early_exit = await _run_with_session(
        settings=settings,
        dataset_path=args.dataset,
        levels=levels,
        use_judge=args.use_judge,
        level_arg=args.level,
    )
    if early_exit is not None:
        return early_exit

    print_console_summary(report)
    write_json_report(report, args.output)
    print(f"\nJSON report written to: {args.output}")

    if args.update_baseline is not None:
        return _handle_update_baseline(report, args.update_baseline)

    if args.check_regression is not None:
        return _handle_check_regression(
            report,
            settings,
            args.check_regression,
            args.regression_output,
        )

    return 0 if report.all_passed() else 1


def _handle_update_baseline(report: EvalRunReport, baseline_path: Path) -> int:
    env_error = _baseline_eligible_environment(report)
    if env_error is not None:
        print(
            f"Refusing to update baseline: {env_error}",
            file=sys.stderr,
        )
        return 2

    skipped = _skipped_agent_workflow_results(report)
    if skipped:
        print(
            "Refusing to update baseline: agent/workflow cases were skipped:",
            file=sys.stderr,
        )
        for reason in skipped:
            print(f"  - {reason}", file=sys.stderr)
        return 2

    if not report.all_passed():
        print(
            "Refusing to update baseline: current run has failing cases.",
            file=sys.stderr,
        )
        return 1

    write_json_report(report, baseline_path)
    print(f"\nBaseline updated: {baseline_path}")
    return 0


def _handle_check_regression(
    report: EvalRunReport,
    settings: Settings,
    baseline_path: Path,
    regression_output_path: Path,
) -> int:
    try:
        baseline = load_baseline_report(baseline_path)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    result = RegressionChecker.compare(
        report,
        baseline,
        pass_rate_tolerance_pct=settings.observability_regression_pass_rate_tolerance_pct,
        latency_tolerance_pct=settings.observability_regression_latency_tolerance_pct,
        latency_floor_ms=settings.observability_regression_latency_floor_ms,
    )
    print_regression_summary(result)
    write_regression_result(result, regression_output_path)
    print(f"\nRegression JSON written to: {regression_output_path}")
    if result.has_regression:
        return 1
    return 0 if report.all_passed() else 1


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    exit_code = asyncio.run(run_eval(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
