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

import threading
import time
import uuid

import httpx
import structlog
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.ratelimit import SlidingWindowLimiter
from app.api.responder import RATE_LIMITED, respond
from app.api.schemas import ChatRequest, ChatResponse, HealthResponse
from app.api.security import require_allowed_user, require_api_key
from app.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.llm.client import create_llm_client
from app.memory.store import ConversationStore
from app.rag.retrieval import Retriever

API_VERSION = "0.13.0"

log = get_logger(__name__)


def _warm(retriever: Retriever) -> None:
    try:
        retriever.warm()
    except Exception as exc:  # noqa: BLE001
        # Not fatal: the first question loads it instead, slowly.
        log.error("retrieval.warm_failed", reason=str(exc))


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
        llm = create_llm_client()
        log.info(
            "llm.ready",
            provider=settings.llm_provider,
            model={
                "ollama": settings.ollama_model,
                "openrouter": settings.openrouter_model,
                "anthropic": settings.anthropic_model,
            }[settings.llm_provider],
        )
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

    # Built here rather than per request: the embedder holds a model that
    # takes seconds to load. Construction is cheap - the weights load lazily -
    # so a Qdrant that is down or a model that was never downloaded surfaces
    # on the first question rather than at startup. That is the honest place
    # for it: the service is still useful for commands either way.
    retriever = None
    if settings.retrieval_enabled:
        try:
            retriever = Retriever()
            log.info("retrieval.ready", collection=settings.qdrant_collection)
            # Phase 13: load the embedding model now, in the background, so
            # the first question after a restart does not spend 43 s on it.
            # Daemon, so a slow load never holds up shutdown.
            if settings.retrieval_warm_on_start:
                threading.Thread(target=_warm, args=(retriever,), daemon=True).start()
        except Exception as exc:  # noqa: BLE001
            log.error("retrieval.unavailable", reason=str(exc))
    else:
        log.warning("retrieval.disabled", reason="RETRIEVAL_ENABLED is false")

    # Unlike the retriever, this one is checked at startup: creating the
    # table is the check, and it is one quick query. A Postgres that is down
    # leaves the service answering every message as a fresh question.
    memory = None
    if settings.memory_enabled:
        try:
            memory = ConversationStore(settings.postgres_dsn)
            memory.ensure_schema()
            log.info("memory.ready", messages=settings.memory_messages)
        except Exception as exc:  # noqa: BLE001
            memory = None
            log.error("memory.unavailable", reason=str(exc))
    else:
        log.warning("memory.disabled", reason="MEMORY_ENABLED is false")

    app.state.llm = llm
    app.state.retriever = retriever
    app.state.grounded = settings.retrieval_enabled
    app.state.memory = memory
    app.state.limiter = SlidingWindowLimiter(
        settings.rate_limit_messages, settings.rate_limit_window_s
    )

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
        """Up, and whether each dependency answers *now*.

        Before Phase 13, "ready" meant "was constructed at startup": with
        Qdrant stopped, /healthz still said `retrieval: ready`. Each check is
        a real round trip with a one-second budget, so a stopped container
        shows as `unreachable` within a second of being stopped.
        """
        retrieval = "disabled" if not app.state.grounded else "unavailable"
        if app.state.retriever is not None:
            retrieval = "ready" if await _reachable_qdrant() else "unreachable"
        memory = "disabled" if not settings.memory_enabled else "unavailable"
        if app.state.memory is not None:
            memory = "ready" if await app.state.memory.ping() else "unreachable"
        return HealthResponse(
            status="ok",
            env=settings.app_env,
            version=API_VERSION,
            llm="ready" if app.state.llm is not None else "unavailable",
            retrieval=retrieval,
            memory=memory,
        )

    async def _reachable_qdrant() -> bool:
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                return (await client.get(f"{settings.qdrant_url}/readyz")).is_success
        except httpx.HTTPError:
            return False

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
        if not request.app.state.limiter.allow(payload.user_id):
            # A 200 with a reply, not a 429: n8n sends whatever comes back,
            # and the user should learn why the bot went quiet rather than
            # hear nothing at all.
            wait = round(request.app.state.limiter.retry_after(payload.user_id) / 60) or 1
            log.warning("chat.rate_limited", user_id=payload.user_id)
            return ChatResponse(
                reply=RATE_LIMITED.format(minutes=wait),
                handled_by="rate_limited",
                trace_id=trace_id,
            )

        reply, handled_by, sources = await respond(
            payload,
            request.app.state.llm,
            request.app.state.retriever,
            grounded=request.app.state.grounded,
            memory=request.app.state.memory,
            history_limit=settings.memory_messages,
            history_chars=settings.memory_max_chars,
            agent=settings.agent_enabled,
            agent_max_rounds=settings.agent_max_rounds,
        )

        log.info(
            "chat.replied",
            user_id=payload.user_id,
            handled_by=handled_by,
            # The message text itself is NOT logged. Logs get shipped, shared
            # and kept; the conversation is the one thing here that is
            # genuinely private. Length is enough to debug with.
            chars_in=len(payload.text),
            chars_out=len(reply),
            sources=len(sources),
        )
        return ChatResponse(
            reply=reply,
            handled_by=handled_by,
            trace_id=trace_id,
            sources=sources,
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
