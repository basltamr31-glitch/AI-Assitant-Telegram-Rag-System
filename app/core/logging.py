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
import sys
from typing import TextIO

import structlog


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
