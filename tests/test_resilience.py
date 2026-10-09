"""Tests for failing well (Phase 13).

The exit criterion is "kill Qdrant mid-conversation - the bot degrades
gracefully". These tests are that, in miniature: a store that starts
refusing connections, a model provider whose quota runs out, and checks that
each reaches the user as the right message - promptly, and without the
model being asked again for nothing.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_agent import FakeMemory, ScriptedLLM, answer, calls  # noqa: E402

from app.api.responder import respond  # noqa: E402
from app.api.schemas import ChatRequest  # noqa: E402
from app.core.errors import KnowledgeBaseUnavailable, QuotaExhausted  # noqa: E402
from app.rag.retrieval import BREAKER_COOLDOWN_S, Retriever  # noqa: E402
from app.rag.store import SearchResult  # noqa: E402


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Embedder:
    def embed_query(self, text: str) -> list[float]:
        return [0.1, 0.2]


class FlakyStore:
    """A Qdrant that can be switched off and on again."""

    def __init__(self) -> None:
        self.up = True
        self.calls = 0

    def search(self, vector, **kwargs):
        self.calls += 1
        if not self.up:
            raise ConnectionRefusedError("[WinError 10061] actively refused")
        return [SearchResult("نص المادة 628", 0.7, "law.pdf", 99, "legal", "المادة 628")]

    def by_label(self, label, domain=None):
        return self.search(None)


def retriever_with(store: FlakyStore, clock: Clock) -> Retriever:
    return Retriever(embedder=Embedder(), store=store, clock=clock)  # type: ignore[arg-type]


# --- the circuit breaker -------------------------------------------------------


def test_a_failing_store_is_reported_by_name() -> None:
    store, clock = FlakyStore(), Clock()
    store.up = False
    with pytest.raises(KnowledgeBaseUnavailable):
        retriever_with(store, clock).retrieve("سرقة")


def test_after_a_failure_the_store_is_not_tried_again_during_the_cooldown() -> None:
    """Each refused connection cost 4.6 s; the second one is skipped."""
    store, clock = FlakyStore(), Clock()
    retriever = retriever_with(store, clock)
    store.up = False
    with pytest.raises(KnowledgeBaseUnavailable):
        retriever.retrieve("سرقة")
    clock.now += BREAKER_COOLDOWN_S - 1
    with pytest.raises(KnowledgeBaseUnavailable):
        retriever.article(628)
    assert store.calls == 1
    assert retriever.available is False


def test_it_recovers_on_its_own_once_the_store_is_back() -> None:
    """A restarted Qdrant needs no restart of the API."""
    store, clock = FlakyStore(), Clock()
    retriever = retriever_with(store, clock)
    store.up = False
    with pytest.raises(KnowledgeBaseUnavailable):
        retriever.retrieve("سرقة")
    store.up = True
    clock.now += BREAKER_COOLDOWN_S
    assert retriever.retrieve("سرقة").found
    assert retriever.available is True


# --- the exit criterion, in miniature -------------------------------------------


def ask(llm, retriever, text: str, memory=None):
    request = ChatRequest(chat_id=1, user_id=1, text=text)
    return asyncio.run(respond(request, llm, retriever, memory=memory, agent=True))


def test_qdrant_stopping_mid_conversation_degrades_gracefully() -> None:
    store, clock = FlakyStore(), Clock()
    retriever = retriever_with(store, clock)
    memory = FakeMemory()
    search = calls(("search_knowledge_base", {"query": "عقوبة السرقة"}))

    # 1. A question, answered normally.
    reply, handled_by, _ = ask(ScriptedLLM(search, answer("الحبس [1].")), retriever, "ما عقوبة السرقة؟", memory)
    assert handled_by == "agent.grounded"

    # 2. Qdrant stops. The follow-up names the problem, and the model is not
    #    asked to write an answer it would have to invent.
    store.up = False
    llm = ScriptedLLM(search, answer("الحبس مع الشغل [1]."))
    reply, handled_by, sources = ask(llm, retriever, "وإذا كان مسلحاً؟", memory)
    assert handled_by == "retrieval.error"
    assert "قاعدة المعرفة غير متاحة" in reply and sources == []
    assert len(llm.requests) == 1

    # 3. A greeting needs no documents, so it still works.
    reply, handled_by, _ = ask(ScriptedLLM(answer("أهلاً!")), retriever, "مرحبا", memory)
    assert handled_by == "agent.direct"

    # 4. Qdrant comes back; after the cooldown the bot answers again.
    store.up = True
    clock.now += BREAKER_COOLDOWN_S
    _, handled_by, _ = ask(ScriptedLLM(search, answer("الحبس [1].")), retriever, "وإذا كان مسلحاً؟", memory)
    assert handled_by == "agent.grounded"


def test_a_spent_quota_says_when_it_comes_back() -> None:
    class SpentLLM(ScriptedLLM):
        async def complete_with_tools(self, *args, **kwargs):
            raise QuotaExhausted("free-models-per-day")

    reply, handled_by, _ = ask(SpentLLM(), retriever_with(FlakyStore(), Clock()), "سؤال")
    assert handled_by == "model.quota"
    assert "3:00" in reply


def test_an_unexpected_error_is_still_a_message() -> None:
    class BrokenLLM(ScriptedLLM):
        async def complete_with_tools(self, *args, **kwargs):
            raise KeyError("something nobody planned for")

    reply, handled_by, _ = ask(BrokenLLM(), retriever_with(FlakyStore(), Clock()), "سؤال")
    assert handled_by == "model.error"
    assert reply
