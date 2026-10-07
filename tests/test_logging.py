"""Tests for secret redaction in logs.

The code is written never to log a secret; these tests are about the day it
does anyway - a provider error body that echoes a key, a traceback that
prints a connection string.
"""

from __future__ import annotations

import io

import pytest
import structlog

from app.config import get_settings
from app.core.logging import REDACTED, configure_logging, redact


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("INTERNAL_API_KEY", "internal-key-that-is-long-enough")
    get_settings.cache_clear()
    stream = io.StringIO()
    configure_logging(stream=stream)
    yield stream
    get_settings.cache_clear()
    structlog.reset_defaults()


def test_the_configured_api_key_never_reaches_the_log(captured) -> None:
    structlog.get_logger("t").error("oops", body="key was internal-key-that-is-long-enough")
    out = captured.getvalue()
    assert "internal-key-that-is-long-enough" not in out
    assert REDACTED in out


def test_a_secret_inside_a_traceback_is_redacted(captured) -> None:
    log = structlog.get_logger("t")
    try:
        raise RuntimeError("could not connect to postgresql://assistant:hunter2pass@localhost/db")
    except RuntimeError:
        log.exception("db.failed")
    out = captured.getvalue()
    assert "hunter2pass" not in out
    assert "postgresql://assistant:[REDACTED]@localhost" in out


@pytest.mark.parametrize(
    "secret",
    [
        "sk-or-v1-0123456789abcdef0123456789abcdef",
        "sk-ant-api03-abcdefghijklmnopqrstuvwxyz",
        "7431125996:AAHk1xYzAbCdEfGhIjKlMnOpQrStUvWxYz0",
        "Bearer abcdefghijklmnopqrstuvwxyz012345",
    ],
)
def test_known_secret_shapes_are_redacted(secret: str) -> None:
    assert secret not in redact(f"request failed with {secret} attached")


def test_ordinary_text_is_left_alone() -> None:
    text = "retrieval.search best_score=0.691 query_chars=40 user_id=7431125996"
    assert redact(text) == text
