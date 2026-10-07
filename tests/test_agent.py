"""Tests for the agent loop and the rules it cannot talk its way past.

The model is scripted: each call returns the next prepared result, so a test
states exactly what the model "decided" and checks what the code did with it.
Whether a real model decides well is Phase 14's question; whether the rules
hold no matter what it decides is this file's.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.agent.loop import looks_like_small_talk
from app.agent.tools import NO_RESULTS, Evidence, execute
from app.api.responder import NOTHING_FOUND, respond
from app.api.schemas import ChatRequest
from app.llm.base import LLMResult, Message, ToolCall, ToolSpec, ToolStep
from app.rag.store import SearchResult


def passage(label: str, page: int = 99) -> SearchResult:
    return SearchResult(
        text=f"نص {label}", score=0.7, source="law.pdf", page=page,
        domain="legal", label=label,
    )


def answer(text: str) -> LLMResult:
    return LLMResult(text=text, model="m", input_tokens=1, output_tokens=1, stop_reason="stop")


def calls(*pairs: tuple[str, dict]) -> LLMResult:
    return LLMResult(
        text="", model="m", input_tokens=1, output_tokens=1, stop_reason="tool_calls",
        tool_calls=tuple(ToolCall(id=f"c{i}", name=n, arguments=a) for i, (n, a) in enumerate(pairs)),
    )


class ScriptedLLM:
    """Returns the next scripted result on every call, and records the calls."""

    def __init__(self, *script: LLMResult) -> None:
        self.script = list(script)
        self.requests: list[dict] = []

    async def complete(self, system, user_message, max_tokens=1024, history=()):
        raise AssertionError("the agent path must not use plain complete()")

    async def complete_with_tools(
        self, system, user_message, *, tools, steps=(), history=(), allow_tools=True
    ):
        self.requests.append(
            {"steps": list(steps), "allow_tools": allow_tools, "history": list(history)}
        )
        return self.script.pop(0)


class FakeRetriever:
    def __init__(self, by_query: dict[str, list[SearchResult]] | None = None,
                 articles: dict[str, list[SearchResult]] | None = None) -> None:
        self.by_query = by_query or {}
        self.articles = articles or {}
        self.searches: list[tuple[str, str | None]] = []
        self.lookups: list = []

    def retrieve(self, query, *, context=None, **kwargs):
        self.searches.append((query, context))
        return SimpleNamespace(results=self.by_query.get(query, []))

    def article(self, number):
        self.lookups.append(number)
        return SimpleNamespace(results=self.articles.get(str(number), []))


class FakeMemory:
    def __init__(self, history=()) -> None:
        self.rows = list(history)

    async def recent(self, chat_id, limit):
        return self.rows[-limit:]

    async def append(self, chat_id, user_id, turns):
        self.rows.extend(turns)

    async def clear(self, chat_id):
        n = len(self.rows)
        self.rows = []
        return n


def ask(llm, retriever, text: str, memory=None):
    request = ChatRequest(chat_id=1, user_id=1, text=text)
    return asyncio.run(
        respond(request, llm, retriever, grounded=True, memory=memory, agent=True)
    )


THEFT = "ما عقوبة السرقة؟"


# --- the exit criterion ---------------------------------------------------------


def test_a_greeting_is_answered_without_searching() -> None:
    """Phase 8 searched for "مرحبا" and refused. The agent just says hello."""
    llm = ScriptedLLM(answer("أهلاً! كيف أساعدك؟"))
    retriever = FakeRetriever()

    reply, handled_by, sources = ask(llm, retriever, "مرحبا")

    assert handled_by == "agent.direct"
    assert "أهلاً" in reply
    assert retriever.searches == [] and sources == []
    assert len(llm.requests) == 1


def test_a_legal_question_is_searched_and_cited() -> None:
    llm = ScriptedLLM(
        calls(("search_knowledge_base", {"query": "عقوبة السرقة"})),
        answer("الحبس سنة على الأقل [1]."),
    )
    retriever = FakeRetriever({"عقوبة السرقة": [passage("المادة 628")]})

    reply, handled_by, sources = ask(llm, retriever, THEFT)

    assert handled_by == "agent.grounded"
    assert sources == ["المادة 628 (law.pdf, p.99)"]
    assert "[1] المادة 628" in reply
    # The model saw the passage it then cited.
    assert "[1] المادة 628" in llm.requests[1]["steps"][0].results[0]


# --- rules the model cannot override -------------------------------------------


def test_nothing_found_is_a_refusal_whatever_the_model_says() -> None:
    llm = ScriptedLLM(
        calls(("search_knowledge_base", {"query": "كيكة"})),
        answer("الكيكة تحتاج 3 بيضات وكوب سكر."),
    )

    reply, handled_by, _ = ask(llm, FakeRetriever(), "كيف احضر كيكة؟")

    assert handled_by == "agent.nothing_found"
    assert reply == NOTHING_FOUND


def test_a_substantive_answer_without_a_search_is_searched_anyway() -> None:
    """From memory, "15 to 20 years" - plausible, uncited, and not allowed."""
    llm = ScriptedLLM(
        answer("عقوبة القتل قصداً من 15 إلى 20 سنة."),
        answer("الأشغال الشاقة من خمس عشرة سنة إلى عشرين سنة [1]."),
    )
    question = "ما عقوبة القتل قصدا؟"
    retriever = FakeRetriever({question: [passage("المادة 533", page=84)]})

    reply, handled_by, sources = ask(llm, retriever, question)

    assert retriever.searches == [(question, None)]
    assert handled_by == "agent.grounded"
    assert sources == ["المادة 533 (law.pdf, p.84)"]
    # The forced answer had to be written without further tools.
    assert llm.requests[1]["allow_tools"] is False


def test_a_forced_search_carries_the_previous_question() -> None:
    memory = FakeMemory([Message("user", THEFT), Message("assistant", "الحبس.")])
    llm = ScriptedLLM(answer("الحبس مع الشغل سنة على الأقل في المادة 628."), answer("جواب [1]."))
    retriever = FakeRetriever({"وإذا كان مسلحاً؟": [passage("المادة 628")]})

    ask(llm, retriever, "وإذا كان مسلحاً؟", memory)

    assert retriever.searches == [("وإذا كان مسلحاً؟", THEFT)]


def test_the_loop_stops_after_the_round_limit() -> None:
    search = calls(("search_knowledge_base", {"query": "عقوبة السرقة"}))
    llm = ScriptedLLM(search, search, search, answer("الحبس [1]."))
    retriever = FakeRetriever({"عقوبة السرقة": [passage("المادة 628")]})

    _, handled_by, _ = ask(llm, retriever, THEFT)

    assert len(retriever.searches) == 3
    assert [r["allow_tools"] for r in llm.requests] == [True, True, True, False]
    assert handled_by == "agent.grounded"


def test_a_citation_to_a_passage_that_was_never_retrieved_is_removed() -> None:
    llm = ScriptedLLM(
        calls(("search_knowledge_base", {"query": "عقوبة السرقة"})),
        answer("الحبس [1] والإعدام [7]."),
    )
    retriever = FakeRetriever({"عقوبة السرقة": [passage("المادة 628")]})

    reply, _, sources = ask(llm, retriever, THEFT)

    assert "[7]" not in reply
    assert sources == ["المادة 628 (law.pdf, p.99)"]


def test_a_remembered_agent_answer_loses_its_citations() -> None:
    memory = FakeMemory()
    llm = ScriptedLLM(
        calls(("search_knowledge_base", {"query": "عقوبة السرقة"})),
        answer("الحبس [1]."),
    )
    ask(llm, FakeRetriever({"عقوبة السرقة": [passage("المادة 628")]}), THEFT, memory)

    assert [(m.role, m.content) for m in memory.rows] == [
        ("user", THEFT), ("assistant", "الحبس."),
    ]


# --- the tools ------------------------------------------------------------------


def test_get_article_looks_up_by_number_including_arabic_digits() -> None:
    from app.rag.retrieval import Retriever

    class LabelStore:
        def __init__(self):
            self.labels = []

        def by_label(self, label, domain=None):
            self.labels.append(label)
            return [passage(label)]

    store = LabelStore()
    retriever = Retriever(embedder=object(), store=store)  # type: ignore[arg-type]

    assert retriever.article("٥٣٥").results[0].label == "المادة 535"
    assert retriever.article(535).found
    assert store.labels == ["المادة 535", "المادة 535"]


def test_evidence_numbers_passages_once_across_searches() -> None:
    evidence = Evidence()
    first = evidence.add([passage("المادة 628"), passage("المادة 626", page=98)])
    second = evidence.add([passage("المادة 626", page=98), passage("المادة 622", page=97)])

    assert first.startswith("[1] المادة 628")
    # 626 was already [2]; 622 is new and becomes [3].
    assert "[2] المادة 626" in second and "[3] المادة 622" in second
    assert len(evidence.passages) == 3


def test_an_empty_search_tells_the_model_not_to_improvise() -> None:
    assert Evidence().add([]) == NO_RESULTS


def test_tool_mistakes_are_reported_to_the_model_not_raised() -> None:
    evidence, retriever = Evidence(), FakeRetriever()
    unknown = execute(ToolCall("1", "delete_everything", {}), retriever, evidence)
    missing = execute(ToolCall("2", "get_article", {}), retriever, evidence)

    assert unknown.startswith("ERROR") and "search_knowledge_base" in unknown
    assert missing.startswith("ERROR")
    assert evidence.searched is False


@pytest.mark.parametrize(
    ("text", "small_talk"),
    [
        ("أهلاً! كيف أساعدك؟", True),
        ("عقوبة القتل من 15 إلى 20 سنة.", False),
        ("المادة ٥٣٣ تعاقب على القتل.", False),
        ("أ" * 400, False),
    ],
)
def test_what_counts_as_small_talk(text: str, small_talk: bool) -> None:
    assert looks_like_small_talk(text) is small_talk


# --- the OpenRouter wire format -----------------------------------------------


def test_openrouter_replays_tool_rounds_and_parses_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import get_settings
    from app.llm.openrouter_client import OpenRouterClient

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-a-real-key")
    get_settings.cache_clear()
    try:
        client = OpenRouterClient()
    finally:
        get_settings.cache_clear()

    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "tool_calls",
            "message": {"content": None, "tool_calls": [
                {"id": "x", "type": "function",
                 "function": {"name": "get_article", "arguments": '{"number": 535}'}},
                {"id": "y", "type": "function",
                 "function": {"name": "search_knowledge_base", "arguments": "{not json"}},
            ]},
        }]})

    client._client = httpx.AsyncClient(
        base_url="https://openrouter.test", transport=httpx.MockTransport(handler)
    )
    step = ToolStep(
        calls=(ToolCall("c0", "search_knowledge_base", {"query": "عقوبة السرقة"}),),
        results=("[1] المادة 628\nنص",),
    )
    tool = ToolSpec("search_knowledge_base", "d", {"type": "object"})

    result = asyncio.run(client.complete_with_tools(
        "sys", THEFT, tools=[tool], steps=[step], allow_tools=False,
    ))

    body = sent[0]
    assert body["tool_choice"] == "none"
    assert [m["role"] for m in body["messages"]] == ["system", "user", "assistant", "tool"]
    assert json.loads(body["messages"][2]["tool_calls"][0]["function"]["arguments"]) == {
        "query": "عقوبة السرقة"
    }
    assert body["messages"][3] == {"role": "tool", "tool_call_id": "c0", "content": "[1] المادة 628\nنص"}
    assert result.tool_calls[0] == ToolCall("x", "get_article", {"number": 535})
    # Malformed arguments become {} so the tool can report what is missing.
    assert result.tool_calls[1].arguments == {}


def test_articles_named_without_brackets_still_become_sources() -> None:
    """"المادة <b>628</b>/ب" with no [n]: checkable, so listed."""
    llm = ScriptedLLM(
        calls(("search_knowledge_base", {"query": "سرقة مع سلاح"})),
        answer("المادة <b>628</b>/ب تقضي بالحبس، و٦٢٢ أشد. الغرامة 300 ليرة، والمادة 999 لا علاقة."),
    )
    retriever = FakeRetriever({"سرقة مع سلاح": [
        passage("المادة 628"), passage("المادة 622", page=97), passage("المادة 300", page=50),
    ]})

    reply, _, sources = ask(llm, retriever, "وإذا كان مسلحاً؟")

    # 628 follows the word مادة; ٦٢٢ and 300 do not; 999 was never retrieved.
    assert sources == ["المادة 628 (law.pdf, p.99)"]
    assert "<i>المصادر:</i>" in reply


def test_an_overlong_search_query_is_cut() -> None:
    """A query is a few key words; 50,000 characters is a model gone wrong."""
    from app.agent.tools import MAX_QUERY_CHARS

    retriever = FakeRetriever()
    execute(ToolCall("1", "search_knowledge_base", {"query": "س" * 50_000}), retriever, Evidence())
    assert len(retriever.searches[0][0]) == MAX_QUERY_CHARS
