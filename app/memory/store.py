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
"""


class ConversationStore:
    """Append turns, read back the recent ones, forget on request."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _connect(self) -> psycopg.Connection:
        # Short timeout: a Postgres that is down should cost the user a
        # second of waiting, then an answer without memory - not a hang.
        return psycopg.connect(self._dsn, connect_timeout=3)

    # --- schema -------------------------------------------------------------

    def ensure_schema(self) -> None:
        """Create the table if it is missing. Safe to run on every start.

        No migration tool yet: there is one table, and `IF NOT EXISTS` is a
        complete migration story for one table. The day a column changes is
        the day that stops being true.
        """
        with self._connect() as conn:
            conn.execute(SCHEMA)
        log.info("memory.schema_ready")

    # --- the three operations -------------------------------------------------

    def _recent(self, chat_id: int, limit: int) -> list[Message]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE chat_id = %s "
                "ORDER BY id DESC LIMIT %s",
                (chat_id, limit),
            ).fetchall()
        # Newest-first out of the index; oldest-first is what a model reads.
        return [Message(role=role, content=content) for role, content in reversed(rows)]

    def _append(self, chat_id: int, user_id: int, turns: list[Message]) -> None:
        with self._connect() as conn:
            # One transaction for the pair, so a question is never stored
            # without the answer it got - that would leave the next prompt
            # with two user turns in a row and no idea what was said between.
            with conn.transaction():
                for turn in turns:
                    conn.execute(
                        "INSERT INTO messages (chat_id, user_id, role, content) "
                        "VALUES (%s, %s, %s, %s)",
                        (chat_id, user_id, turn.role, turn.content),
                    )

    def _clear(self, chat_id: int) -> int:
        with self._connect() as conn:
            return conn.execute(
                "DELETE FROM messages WHERE chat_id = %s", (chat_id,)
            ).rowcount

    async def recent(self, chat_id: int, limit: int) -> list[Message]:
        """The last `limit` messages of this chat, oldest first."""
        return await asyncio.to_thread(self._recent, chat_id, limit)

    async def append(self, chat_id: int, user_id: int, turns: list[Message]) -> None:
        await asyncio.to_thread(self._append, chat_id, user_id, turns)

    async def clear(self, chat_id: int) -> int:
        """Forget this chat. Returns how many messages were deleted."""
        return await asyncio.to_thread(self._clear, chat_id)
