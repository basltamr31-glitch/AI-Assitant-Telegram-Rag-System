"""Conversation history, kept in Postgres.

What this is for
----------------
Until Phase 9 every message stood alone. Ask "ما عقوبة السرقة؟", then "وإذا
كان السارق مسلحاً؟", and the second question arrived with no idea what "it"
was. The fix is unglamorous: write each turn down, and read the last few back
before answering.

Why Postgres, and why this shape
--------------------------------
ADR-002 put everything relational in Postgres. A conversation is a list of
rows ordered by time, keyed by the Telegram chat it happened in - one table,
one index, nothing a vector store adds anything to.

The chat id is the conversation. Telegram gives every private chat its own
id, so "this conversation" needs no session concept of our own; `/reset`
deletes a chat's rows when the user wants a fresh start.

Why synchronous psycopg in a thread
-----------------------------------
psycopg's async mode cannot run on the event loop uvicorn uses on Windows
(the Proactor loop), and this project develops on Windows. A plain connection
inside `asyncio.to_thread` works on every loop, and one short query per
message gains nothing from being natively async. A connection is opened per
call: at one user, a pool is a dependency without a problem to solve. Phase
15's production setup is where that changes.

Privacy
-------
The logs still never contain message text. This table does - that is its
job - and it lives on this machine, in a container, behind a password.
"""

from __future__ import annotations

import asyncio

import psycopg

from app.core.logging import get_logger
from app.llm.base import Message

log = get_logger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          BIGSERIAL PRIMARY KEY,
    chat_id     BIGINT      NOT NULL,
    user_id     BIGINT      NOT NULL,
    role        TEXT        NOT NULL CHECK (role IN ('user', 'assistant')),
    content     TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS messages_chat_recent ON messages (chat_id, id DESC);

-- Phase 15: each advisor keeps its own conversation. Rows written before
-- advisors existed were all with the legal one, which is what the default
-- gives them.
ALTER TABLE messages ADD COLUMN IF NOT EXISTS advisor TEXT NOT NULL DEFAULT 'legal';
CREATE INDEX IF NOT EXISTS messages_chat_advisor_recent ON messages (chat_id, advisor, id DESC);

-- Which advisor a chat last chose. One row per chat; no row means the default.
CREATE TABLE IF NOT EXISTS chat_advisor (
    chat_id     BIGINT      PRIMARY KEY,
    advisor     TEXT        NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _default_advisor() -> str:
    from app.advisors import get_registry

    return get_registry().default_id


class ConversationStore:
    """Append turns, read back the recent ones, forget on request.

    Since Phase 15 every operation is per advisor: switching from the legal
    to the real-estate advisor and back finds the legal conversation where
    it was left, and a follow-up never reaches across advisors. `advisor`
    None means the default advisor.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _connect(self) -> psycopg.Connection:
        # Short timeout: a Postgres that is down should cost the user a
        # second of waiting, then an answer without memory - not a hang.
        return psycopg.connect(self._dsn, connect_timeout=3)

    # --- schema -------------------------------------------------------------

    def ensure_schema(self) -> None:
        """Create or extend the tables. Safe to run on every start.

        No migration tool yet: `IF NOT EXISTS` on every statement is a
        complete migration story while changes only add. Phase 15 added a
        column and a table that way; the first change that renames or
        removes is the day a real tool earns its place.
        """
        with self._connect() as conn:
            conn.execute(SCHEMA)
        log.info("memory.schema_ready")

    # --- conversation -------------------------------------------------------

    def _recent(self, chat_id: int, limit: int, advisor: str) -> list[Message]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE chat_id = %s AND advisor = %s "
                "ORDER BY id DESC LIMIT %s",
                (chat_id, advisor, limit),
            ).fetchall()
        # Newest-first out of the index; oldest-first is what a model reads.
        return [Message(role=role, content=content) for role, content in reversed(rows)]

    def _append(self, chat_id: int, user_id: int, turns: list[Message], advisor: str) -> None:
        with self._connect() as conn:
            # One transaction for the pair, so a question is never stored
            # without the answer it got - that would leave the next prompt
            # with two user turns in a row and no idea what was said between.
            with conn.transaction():
                for turn in turns:
                    conn.execute(
                        "INSERT INTO messages (chat_id, user_id, role, content, advisor) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (chat_id, user_id, turn.role, turn.content, advisor),
                    )

    def _clear(self, chat_id: int, advisor: str | None) -> int:
        with self._connect() as conn:
            if advisor is None:
                return conn.execute("DELETE FROM messages WHERE chat_id = %s", (chat_id,)).rowcount
            return conn.execute(
                "DELETE FROM messages WHERE chat_id = %s AND advisor = %s", (chat_id, advisor)
            ).rowcount

    # --- the chosen advisor ---------------------------------------------------

    def _get_advisor(self, chat_id: int) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT advisor FROM chat_advisor WHERE chat_id = %s", (chat_id,)
            ).fetchone()
        return row[0] if row else None

    def _set_advisor(self, chat_id: int, advisor: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO chat_advisor (chat_id, advisor) VALUES (%s, %s) "
                "ON CONFLICT (chat_id) DO UPDATE SET advisor = EXCLUDED.advisor, updated_at = now()",
                (chat_id, advisor),
            )

    def _ping(self) -> bool:
        try:
            with psycopg.connect(self._dsn, connect_timeout=1) as conn:
                conn.execute("SELECT 1")
            return True
        except psycopg.Error:
            return False

    async def ping(self) -> bool:
        """Does Postgres answer right now? For /healthz (Phase 13)."""
        return await asyncio.to_thread(self._ping)

    async def recent(self, chat_id: int, limit: int, advisor: str | None = None) -> list[Message]:
        """The last `limit` messages of this chat with this advisor, oldest first."""
        return await asyncio.to_thread(self._recent, chat_id, limit, advisor or _default_advisor())

    async def append(
        self, chat_id: int, user_id: int, turns: list[Message], advisor: str | None = None
    ) -> None:
        await asyncio.to_thread(self._append, chat_id, user_id, turns, advisor or _default_advisor())

    async def get_advisor(self, chat_id: int) -> str | None:
        """The advisor this chat chose, or None if it never chose."""
        return await asyncio.to_thread(self._get_advisor, chat_id)

    async def set_advisor(self, chat_id: int, advisor: str) -> None:
        await asyncio.to_thread(self._set_advisor, chat_id, advisor)

    async def clear(self, chat_id: int, advisor: str | None = None) -> int:
        """Forget this chat with one advisor - or, with None, entirely.

        Returns how many messages were deleted.
        """
        return await asyncio.to_thread(self._clear, chat_id, advisor)
