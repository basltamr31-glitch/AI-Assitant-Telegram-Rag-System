"""Tests for the MCP server, through a real MCP client.

`Client` accepts the server object itself and connects in-process, so these
tests speak the actual protocol - initialise, list, call - without spawning a
subprocess or loading the embedder. The retriever is a fake; what is tested is
what the server offers and what a client receives.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from mcp import Client

import app.mcp.server as mcp_server
from app.agent.tools import NO_RESULTS
from app.rag.store import SearchResult


def passage(label: str) -> SearchResult:
    return SearchResult(
        text=f"نص {label}", score=0.7, source="law.pdf", page=84,
        domain="legal", label=label,
    )


class FakeRetriever:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def retrieve(self, query, **kwargs):
        self.queries.append(query)
        hits = [passage("المادة 533"), passage("المادة 535")] if "القتل" in query else []
        return SimpleNamespace(results=hits)

    def article(self, number):
        found = [passage(f"المادة {int(number)}")] if int(number) < 800 else []
        return SimpleNamespace(results=found)

    def documents(self):
        return {"law.pdf": 754}


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeRetriever:
    retriever = FakeRetriever()
    monkeypatch.setattr(mcp_server, "_retriever", retriever)
    return retriever


def talk(coroutine_fn):
    async def go():
        async with Client(mcp_server.server) as client:
            return await coroutine_fn(client)

    return asyncio.run(go())


def text(result) -> str:
    return "\n".join(c.text for c in result.content)


def test_the_server_offers_the_agents_two_tools_read_only(fake) -> None:
    tools = talk(lambda c: c.list_tools()).tools
    assert sorted(t.name for t in tools) == ["get_article", "search_knowledge_base"]
    assert all(t.annotations.read_only_hint for t in tools)


def test_a_search_returns_numbered_citable_passages(fake) -> None:
    result = talk(lambda c: c.call_tool("search_knowledge_base", {"query": "عقوبة القتل"}))
    body = text(result)
    assert not result.is_error
    assert body.startswith("[1] المادة 533 (law.pdf, p.84)")
    assert "[2] المادة 535" in body


def test_nothing_found_is_the_same_refusal_the_bot_gets(fake) -> None:
    """A weak passage is no more trustworthy because another client asked."""
    result = talk(lambda c: c.call_tool("search_knowledge_base", {"query": "كيكة"}))
    assert text(result) == NO_RESULTS


def test_get_article_by_number(fake) -> None:
    body = text(talk(lambda c: c.call_tool("get_article", {"number": 535})))
    assert body.startswith("[1] المادة 535")


def test_the_documents_resource_lists_what_is_indexed(fake) -> None:
    result = talk(lambda c: c.read_resource("kb://documents"))
    assert json.loads(result.contents[0].text) == {"law.pdf": 754}


def test_an_article_can_be_read_as_a_resource(fake) -> None:
    result = talk(lambda c: c.read_resource("kb://article/533"))
    assert result.contents[0].text.startswith("المادة 533 (law.pdf, p.84)")
    missing = talk(lambda c: c.read_resource("kb://article/900"))
    assert missing.contents[0].text == NO_RESULTS
