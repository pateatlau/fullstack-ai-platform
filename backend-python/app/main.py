import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import RequestResponseEndpoint
from starlette.types import Message

from app.core.config import get_settings
from app.routers import chat, health
from app.schemas.chat import ErrorDetail, ErrorResponseSchema
from app.services.chat_service import ChatServiceError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
MAX_REQUEST_BODY_BYTES = 16 * 1024
REQUEST_BODY_LIMIT_MESSAGE = (
    "Request body exceeds the 16384 byte limit. Reduce message size and retry."
)

settings = get_settings()

app = FastAPI(title="Chatbot Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins_list,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(chat.router)


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    payload = ErrorResponseSchema(error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status_code, content=payload.model_dump())


def _format_validation_errors(exc: RequestValidationError) -> str:
    messages: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"] if part != "body")
        prefix = f"{location}: " if location else ""
        messages.append(f"{prefix}{error['msg']}")
    return "; ".join(messages) or "Request validation failed."


class RequestBodyTooLargeError(Exception):
    pass


@app.middleware("http")
async def enforce_request_size(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    if request.method in {"POST", "PUT", "PATCH"}:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_REQUEST_BODY_BYTES:
                    return _error_response(
                        status_code=413,
                        code="validation_error",
                        message=REQUEST_BODY_LIMIT_MESSAGE,
                    )
            except ValueError:
                logger.warning(
                    "Ignoring invalid content-length header: %s", content_length
                )

        received_bytes = 0

        async def receive_with_limit() -> Message:
            nonlocal received_bytes
            message = await request.receive()

            if message["type"] == "http.request":
                body = message.get("body", b"")
                if isinstance(body, bytes):
                    received_bytes += len(body)
                    if received_bytes > MAX_REQUEST_BODY_BYTES:
                        raise RequestBodyTooLargeError

            return message

        try:
            return await call_next(Request(request.scope, receive_with_limit))
        except RequestBodyTooLargeError:
            return _error_response(
                status_code=413,
                code="validation_error",
                message=REQUEST_BODY_LIMIT_MESSAGE,
            )

    return await call_next(request)


@app.exception_handler(ChatServiceError)
async def handle_chat_service_error(_: Request, exc: ChatServiceError) -> JSONResponse:
    return _error_response(exc.status_code, exc.code, exc.message)


@app.exception_handler(RequestValidationError)
async def handle_request_validation_error(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    return _error_response(422, "validation_error", _format_validation_errors(exc))


@app.exception_handler(Exception)
async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled server error", exc_info=exc)
    return _error_response(500, "internal_error", "Unexpected server error.")


# Root endpoint for basic health check or welcome message
@app.get("/")
async def root():
    return {"message": "Welcome to the Chatbot Backend!"}
