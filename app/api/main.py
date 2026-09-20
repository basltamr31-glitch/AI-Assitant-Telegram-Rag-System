"""The internal HTTP service that n8n calls.

Where this sits
---------------
    Telegram  ->  n8n Cloud  ->  [ this service ]  ->  n8n Cloud  ->  Telegram

n8n owns *orchestration*: who is allowed in, what to call, where to send the
answer. This service owns *thinking*: given a message, produce a reply. Keeping
that line sharp is what lets Phase 5 drop Claude in without touching a single
node in the workflow.

Two endpoints, on purpose:

* `GET /healthz` - unauthenticated, says nothing secret, and exists so you can
  answer "is it up?" without needing the key. It is also what you curl through
  the tunnel to prove the tunnel works before blaming the workflow.
* `POST /v1/chat` - the real one, behind the API key and the allowlist.

The `/v1` prefix is not ceremony. n8n calls a fixed URL; once Phase 9 changes
the response shape, `/v2/chat` lets the old workflow keep working while the new
one is built.
"""

from __future__ import annotations

import time
import uuid

import structlog
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.responder import respond
from app.api.schemas import ChatRequest, ChatResponse, HealthResponse
from app.api.security import require_allowed_user, require_api_key
from app.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.llm.client import AnthropicClient

API_VERSION = "0.5.0"

log = get_logger(__name__)


def create_app() -> FastAPI:
    """Build the application.

    A factory rather than a module-level `app = FastAPI()` so that tests can
    construct a fresh instance with their own settings, instead of inheriting
    whatever the developer happens to have in `.env`.
    """
    settings = get_settings()
    configure_logging(
        level=settings.log_level,
        json_output=settings.app_env == "production",
    )

    # Built once, at startup: the SDK holds a connection pool, and rebuilding
    # it per request would add a TLS handshake to every message.
    try:
        llm: AnthropicClient | None = AnthropicClient()
        log.info("llm.ready", model=settings.anthropic_model)
    except Exception as exc:  # noqa: BLE001
        # A missing key is not a security failure, so this degrades instead of
        # refusing to start: commands keep working and say what is wrong.
        llm = None
        log.error("llm.unavailable", reason=str(exc))

    app = FastAPI(
        title="Telegram AI Assistant - internal API",
        version=API_VERSION,
        # The interactive docs describe every endpoint and would hand a
        # stranger a map of the service. Harmless in development, not on a
        # public tunnel - so they follow the environment.
        docs_url="/docs" if settings.app_env == "development" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.app_env == "development" else None,
    )

    app.state.llm = llm

    @app.middleware("http")
    async def trace_and_log(request: Request, call_next):
        """Give every request an id, then log how it went.

        `bind_contextvars` attaches the id to *every* log line produced while
        handling this request, including ones written deep inside the
        responder. In Phase 13 this same id will follow a request across
        services; the habit starts here, while there is only one.
        """
        trace_id = uuid.uuid4().hex[:12]
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(trace_id=trace_id)

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Log the failure with its trace id, then let FastAPI's handler
            # turn it into a 500. We never swallow the exception.
            log.exception(
                "http.unhandled_error",
                method=request.method,
                path=request.url.path,
            )
            raise

        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        log.info(
            "http.request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
        )
        # Returned so a failure in Telegram can be traced back to a log line.
        response.headers["X-Trace-Id"] = trace_id
        return response

    @app.get("/healthz", response_model=HealthResponse, tags=["ops"])
    async def healthz() -> HealthResponse:
        return HealthResponse(
            status="ok",
            env=settings.app_env,
            version=API_VERSION,
            llm="ready" if app.state.llm is not None else "unavailable",
        )

    @app.post(
        "/v1/chat",
        response_model=ChatResponse,
        tags=["chat"],
        dependencies=[Depends(require_api_key)],
    )
    async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
        """Produce a reply for one Telegram message."""
        # Layer two of the allowlist. n8n filtered already; we do not take its
        # word for it, because this endpoint is reachable without n8n.
        require_allowed_user(payload.user_id)

        trace_id = structlog.contextvars.get_contextvars().get("trace_id", "")
        reply, handled_by = await respond(payload, request.app.state.llm)

        log.info(
            "chat.replied",
            user_id=payload.user_id,
            handled_by=handled_by,
            # The message text itself is NOT logged. Logs get shipped, shared
            # and kept; the conversation is the one thing here that is
            # genuinely private. Length is enough to debug with.
            chars_in=len(payload.text),
            chars_out=len(reply),
        )
        return ChatResponse(
            reply=reply,
            handled_by=handled_by,
            trace_id=trace_id,
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        """Never leak a stack trace to the caller."""
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal error"},
        )

    return app


app = create_app()
