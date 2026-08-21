import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from starlette.middleware.base import RequestResponseEndpoint
from starlette.types import Message

from app.ai.jobs.background import start_background_jobs, stop_background_jobs
from app.ai.deps import (
    get_mcp_server_registry,
    get_plugin_registry,
    get_prompt_repository,
    get_tool_registry,
    get_workflow_plugin_registry,
    reconcile_workflow_runs_at_startup,
)
from app.ai.observability.metrics.meter import MeterRegistry
from app.ai.observability.cost.calculator import CostRegistry
from app.ai.observability.metrics.instruments import MetricInstruments
from app.ai.observability.metrics.labels import set_model_registry
from app.ai.observability.tracing.provider import TracerRegistry
from app.ai.plugins import load_plugins
from app.ai.tools.registration import register_mcp_tools, register_production_tools
from app.core.config import get_settings
from app.core.cors import CORS_EXPOSE_HEADER_NAMES, DEV_ORIGIN_REGEX
from app.core.errors import error_response, register_exception_handlers
from app.core.logging import bind_context, get_logger, setup_logging
from app.db.engine import dispose_engine_cache, get_sessionmaker
from app.middleware.correlation_id import correlation_id_middleware
from app.middleware.rate_limit import rate_limit_middleware
from app.routers import (
    auth,
    approvals,
    chat,
    documents,
    health,
    jobs,
    memory,
    observability,
    plugins,
    rag,
    security,
    workflows,
)

settings = get_settings()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """App lifespan: register tools, MCP servers (if enabled), then cleanup."""
    setup_logging(settings)
    TracerRegistry.initialize(settings)
    MeterRegistry.initialize(settings)
    CostRegistry.initialize(settings)
    calculator = CostRegistry.get_calculator()
    if calculator is not None:
        set_model_registry(calculator.pricing_table.model_registry)
    MetricInstruments.initialize()
    settings.log_development_warnings(logger)

    if settings.security_governance_enabled:
        from app.ai.security.rbac.service import RbacService
        from app.ai.security.rbac.store import PostgresRoleStore

        async with get_sessionmaker()() as session:
            service = RbacService(
                PostgresRoleStore(session),
                cache_ttl_seconds=settings.security_rbac_cache_ttl_seconds,
            )
            try:
                await service.bootstrap_admins(settings.security_bootstrap_admin_emails)
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    if settings.plugins_enabled:
        load_plugins(
            settings,
            tool_registry=get_tool_registry(),
            prompt_repository=get_prompt_repository(),
            workflow_plugin_registry=get_workflow_plugin_registry(),
            plugin_registry=get_plugin_registry(),
        )

    # Register V1 production tools
    if settings.tools_enabled:
        register_production_tools(get_tool_registry(), settings)

    # Phase 9: Register MCP tools when flag on
    if settings.mcp_enabled:
        logger.info("MCP enabled; registering MCP tools")
        try:
            await register_mcp_tools(
                registry=get_tool_registry(),
                mcp_registry=get_mcp_server_registry(),
                settings=settings,
                extra_servers=get_plugin_registry().list_mcp_servers(),
            )
        except Exception as exc:
            logger.error(
                "Failed to register MCP tools; continuing with V1 tools only",
                error=str(exc),
                exc_info=True,
            )

    if settings.workflow_engine_enabled:
        try:
            reconciled = await reconcile_workflow_runs_at_startup(settings)
            if reconciled:
                logger.info(
                    "Workflow startup reconciliation complete",
                    reconciled_runs=reconciled,
                )
        except Exception as exc:
            logger.warning(
                "Workflow startup reconciliation failed",
                error=str(exc),
                exc_info=True,
            )

    jobs_runtime = await start_background_jobs(settings)

    yield

    await stop_background_jobs(jobs_runtime)

    # Phase 9: Disconnect all MCP servers on shutdown
    if settings.mcp_enabled:
        logger.info("Disconnecting all MCP servers")
        try:
            await get_mcp_server_registry().disconnect_all()
            logger.info("MCP server shutdown complete")
        except Exception as exc:
            logger.warning(
                "Error during MCP server shutdown",
                error=str(exc),
                exc_info=True,
            )

    await dispose_engine_cache()


app = FastAPI(title="Chatbot Backend", version="0.1.0", lifespan=lifespan)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(approvals.router)
app.include_router(documents.router)
app.include_router(rag.router)
app.include_router(memory.router)
app.include_router(workflows.router)
app.include_router(observability.router)
app.include_router(plugins.router)
app.include_router(jobs.router)
app.include_router(security.router)

if settings.voice_enabled:
    from app.routers.voice import create_voice_router

    app.include_router(create_voice_router(settings))

register_exception_handlers(app)


class RequestBodyTooLargeError(Exception):
    pass


DOCUMENT_UPLOAD_PATH = "/api/documents/upload"


@app.middleware("http")
async def enforce_request_size(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    is_document_upload = (
        request.method == "POST" and request.url.path == DOCUMENT_UPLOAD_PATH
    )
    if is_document_upload:
        body_limit = settings.document_upload_max_bytes
        limit_message = settings.document_upload_limit_message()
        limit_code = "document_too_large"
    else:
        body_limit = settings.request_body_limit_bytes
        limit_message = settings.request_body_limit_message()
        limit_code = "validation_error"

    if request.method in {"POST", "PUT", "PATCH"}:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > body_limit:
                    return error_response(
                        status_code=413,
                        code=limit_code,
                        message=limit_message,
                    )
            except ValueError:
                logger.warning(
                    "Ignoring invalid content-length header",
                    content_length=content_length,
                )

        received_bytes = 0

        async def receive_with_limit() -> Message:
            nonlocal received_bytes
            message = await request.receive()

            if message["type"] == "http.request":
                body = message.get("body", b"")
                if isinstance(body, bytes):
                    received_bytes += len(body)
                    if received_bytes > body_limit:
                        raise RequestBodyTooLargeError

            return message

        try:
            return await call_next(Request(request.scope, receive_with_limit))
        except RequestBodyTooLargeError:
            return error_response(
                status_code=413,
                code=limit_code,
                message=limit_message,
            )

    return await call_next(request)


@app.middleware("http")
async def log_requests(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    start = time.perf_counter()
    bind_context(route=request.url.path, method=request.method)
    try:
        response = await call_next(request)
    except Exception:
        latency_ms = int((time.perf_counter() - start) * 1000)
        logger.error(
            "Request failed",
            status_code=500,
            latency_ms=latency_ms,
            exc_info=True,
        )
        raise
    else:
        latency_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "Request completed",
            status_code=response.status_code,
            latency_ms=latency_ms,
        )
        return response


@app.middleware("http")
async def enforce_rate_limit(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    return await rate_limit_middleware(request, call_next)


@app.middleware("http")
async def assign_correlation_id(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    return await correlation_id_middleware(request, call_next)


# Outermost middleware so CORS headers are applied to early returns (429, 413, …)
# from inner HTTP middleware, not only successful route responses.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins_list,
    allow_origin_regex=DEV_ORIGIN_REGEX.pattern if settings.is_development else None,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=list(CORS_EXPOSE_HEADER_NAMES),
)


@app.get("/")
async def root():
    return {"message": "Welcome to the Chatbot Backend!"}
