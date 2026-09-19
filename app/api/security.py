"""Authentication for the internal API.

Threat model for this one file
------------------------------
From Phase 4 on, this service is reachable from the public internet through a
tunnel, because n8n Cloud has to call it. That means the only thing between a
stranger and our endpoint is the header check below. Two habits make it hold:

1. **Compare in constant time.** `a == b` on strings returns as soon as two
   bytes differ, so the time it takes leaks how much of the key was correct.
   Over enough requests that is a guessable signal. `compare_digest` always
   examines the whole value.

2. **An unset key means closed, never open.** The tempting shortcut - "if no
   key is configured, skip the check" - turns a forgotten `.env` line into an
   open door. We fail the request instead, loudly.

The allowlist check lives here too. n8n already filters by user id, and this
is the *second* layer: the API must not assume its caller did its job.
"""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from app.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

# The header n8n will send. Any name works; what matters is that it is not a
# query parameter - URLs end up in access logs, browser history and error
# reports, and a secret in a URL is a secret you have published.
API_KEY_HEADER = "X-API-Key"


def require_api_key(x_api_key: str = Header(default="", alias=API_KEY_HEADER)) -> None:
    """FastAPI dependency: reject anyone without the shared secret."""
    expected = get_settings().internal_api_key.get_secret_value()

    if not expected:
        # Misconfiguration, not an attack - so it is a 500, and the operator
        # gets a clear log line. The caller learns nothing useful.
        log.error("auth.key_not_configured")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server is not configured for authentication",
        )

    if not secrets.compare_digest(x_api_key, expected):
        # Deliberately vague: "wrong key" and "no key" look identical from
        # outside. Never tell an attacker which half of the problem they have.
        log.warning("auth.rejected", has_header=bool(x_api_key))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )


def require_allowed_user(user_id: int) -> None:
    """Second layer: only the owner's Telegram account may use the assistant."""
    allowed = get_settings().telegram_allowed_ids

    if user_id not in allowed:
        # Logged at warning because, unlike a bad key, this can only happen if
        # the n8n allowlist was bypassed or misconfigured. It is worth noticing.
        log.warning("auth.user_not_allowed", user_id=user_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This assistant is private",
        )
