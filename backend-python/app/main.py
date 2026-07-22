import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from starlette.middleware.base import RequestResponseEndpoint
from starlette.types import Message

from app.ai.deps import get_tool_registry
from app.ai.tools.registration import register_production_tools
from app.core.config import get_settings
from app.core.errors import error_response, register_exception_handlers
from app.core.logging import bind_context, get_logger, setup_logging
from app.db.engine import get_engine
from app.middleware.correlation_id import (
    REQUEST_ID_HEADER,
    correlation_id_middleware,
)
from app.middleware.rate_limit import rate_limit_middleware
from app.routers import auth, chat, documents, health, rag

settings = get_settings()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging(settings)
    settings.log_development_warnings(logger)
    if settings.tools_enabled:
        register_production_tools(get_tool_registry(), settings)
    yield
    await get_engine().dispose()


app = FastAPI(title="Chatbot Backend", version="0.1.0", lifespan=lifespan)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(documents.router)
app.include_router(rag.router)

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
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
    expose_headers=["X-Guest-Token", "X-Guest-Quota-Remaining", REQUEST_ID_HEADER],
)


@app.get("/")
async def root():
    return {"message": "Welcome to the Chatbot Backend!"}
