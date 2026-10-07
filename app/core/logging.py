"""Structured logging setup.

Why structured logs?
--------------------
A traditional log line is a sentence:

    2026-09-08 10:31:02 INFO retrieved 5 chunks for user 12345 in 240ms

A structured log line is data:

    {"event": "retrieval.complete", "chunks": 5, "user_id": "12345",
     "duration_ms": 240, "trace_id": "a1b2c3", "level": "info"}

The second can be filtered, counted and graphed ("show me every retrieval
slower than 1s"). The first can only be read by a human, one line at a time.

In development we print colourful human-readable lines; in production we emit
JSON. Same call sites, different renderer.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import TextIO

import structlog


# --- secret redaction (Phase 12) ---------------------------------------------
#
# The code never logs a key on purpose. That is a habit, and habits fail: one
# `log.error(body=response.text)` from a provider that echoes the request, or
# a traceback that prints a connection string, and the secret is on disk in
# a file that gets pasted into bug reports. This is the guarantee behind the
# habit - every structlog line passes through it before it is written.
# THREAT_MODEL.md T8.
_SECRET_SHAPES = [
    re.compile(r"sk-or-v1-[A-Za-z0-9]{8,}"),  # OpenRouter
    re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}"),  # Anthropic
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}"),  # Telegram bot token
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
]
# The password inside a DSN: postgresql://user:PASSWORD@host
_DSN_PASSWORD = re.compile(r"(://[^:/@\s]+:)[^@\s]+(@)")
REDACTED = "[REDACTED]"


def _configured_secrets() -> list[str]:
    """The actual secret values in use, so they are caught whatever shape.

    Imported here, not at module level: config imports logging, and settings
    may not load at all (no .env yet) - redaction must never be what breaks
    a log line.
    """
    try:
        from app.config import get_settings

        s = get_settings()
        values = [
            s.internal_api_key, s.openrouter_api_key, s.anthropic_api_key,
            s.postgres_password,
        ]
        return [v.get_secret_value() for v in values if len(v.get_secret_value()) >= 8]
    except Exception:  # noqa: BLE001
        return []


def redact(text: str) -> str:
    for secret in _configured_secrets():
        text = text.replace(secret, REDACTED)
    for shape in _SECRET_SHAPES:
        text = shape.sub(REDACTED, text)
    return _DSN_PASSWORD.sub(rf"\1{REDACTED}\2", text)


def _redact_processor(logger, method_name, event_dict: dict) -> dict:
    for key, value in event_dict.items():
        if isinstance(value, str):
            event_dict[key] = redact(value)
        elif isinstance(value, (list, tuple, dict)):
            event_dict[key] = redact(str(value))
    return event_dict


def configure_logging(
    level: str = "INFO", json_output: bool = False, stream: TextIO | None = None
) -> None:
    """Configure structlog once, at application start.

    `stream` defaults to stdout. The MCP server passes stderr: over the stdio
    transport, stdout *is* the protocol, and one log line written there is a
    malformed message that ends the session.
    """
    stream = stream or sys.stdout
    logging.basicConfig(
        format="%(message)s",
        stream=stream,
        level=getattr(logging, level.upper(), logging.INFO),
    )

    processors: list = [
        structlog.contextvars.merge_contextvars,  # picks up bound trace_id etc.
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        # Last before rendering, so it also sees the formatted traceback.
        _redact_processor,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=stream.isatty())
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=stream),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a logger bound to a module name."""
    return structlog.get_logger(name)
