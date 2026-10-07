"""The conversation store against a real Postgres.

The rest of the suite uses a fake, which proves the responder's logic but not
the one claim Phase 9 exists to make: the conversation survives a restart.
That claim is about a database, so it is tested against one - and skipped,
not failed, when the development Postgres is not running.
"""

from __future__ import annotations

import asyncio
import random

import psycopg
import pytest

from app.config import get_settings
from app.llm.base import Message
from app.memory.store import ConversationStore


@pytest.fixture
def store() -> ConversationStore:
    dsn = get_settings().postgres_dsn
    try:
        psycopg.connect(dsn, connect_timeout=2).close()
    except psycopg.OperationalError:
        pytest.skip("Postgres is not running")
    s = ConversationStore(dsn)
    s.ensure_schema()
    return s


def test_turns_survive_a_new_store(store: ConversationStore) -> None:
    """A new instance is what a restart is: nothing shared but the database."""
    # A chat id no real Telegram chat will have.
    chat = -random.randint(10**12, 10**13)
    try:
        asyncio.run(store.append(chat, 1, [Message("user", "س"), Message("assistant", "ج")]))
        asyncio.run(store.append(chat, 1, [Message("user", "س2"), Message("assistant", "ج2")]))

        reopened = ConversationStore(get_settings().postgres_dsn)
        recent = asyncio.run(reopened.recent(chat, 3))

        assert [m.content for m in recent] == ["ج", "س2", "ج2"]
    finally:
        asyncio.run(store.clear(chat))


def test_clear_removes_only_that_chat(store: ConversationStore) -> None:
    mine, other = -random.randint(10**12, 10**13), -random.randint(10**12, 10**13)
    try:
        asyncio.run(store.append(mine, 1, [Message("user", "a"), Message("assistant", "b")]))
        asyncio.run(store.append(other, 1, [Message("user", "c"), Message("assistant", "d")]))

        assert asyncio.run(store.clear(mine)) == 2
        assert asyncio.run(store.recent(mine, 10)) == []
        assert len(asyncio.run(store.recent(other, 10))) == 2
    finally:
        asyncio.run(store.clear(mine))
        asyncio.run(store.clear(other))
