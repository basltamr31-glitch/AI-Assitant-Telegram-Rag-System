"""The agent inside one advisor's books (Phase 15, step 2).

Each test picks an advisor and checks what the model is told and what the
code lets it reach: its own books in the prompt, a search limited to them,
article lookup only where there are laws, and an honest message from an
advisor that has no books yet.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))
from test_agent import ScriptedLLM, answer, calls, passage  # noqa: E402

from app.advisors import get_registry  # noqa: E402
from app.api.responder import respond  # noqa: E402
from app.api.schemas import ChatRequest  # noqa: E402


class WatchingLLM(ScriptedLLM):
    """Also records the instructions and the tools it was offered."""

    def __init__(self, *script) -> None:
        super().__init__(*script)
        self.systems: list[str] = []
        self.tools: list[list[str]] = []

    async def complete_with_tools(self, system, user_message, *, tools, **kwargs):
        self.systems.append(system)
        self.tools.append([t.name for t in tools])
        return await super().complete_with_tools(system, user_message, tools=tools, **kwargs)


class Library:
    """A retriever whose books belong to advisors."""

    def __init__(self, books: dict[str, list[str]], laws: set[str] | None = None, down: bool = False):
        self.books = books  # advisor id -> document names
        self.laws = laws or set()
        self.down = down
        self.searched_with: list[str | None] = []

    def documents(self, advisor=None):
        if self.down:
            raise ConnectionRefusedError("qdrant is down")
        return {name: 10 for name in self.books.get(advisor, [])}

    def has_laws(self, advisor):
        return advisor in self.laws

    def retrieve(self, query, *, context=None, advisor=None, **kwargs):
        self.searched_with.append(advisor)
        hits = [passage("المادة 1")] if self.books.get(advisor) else []
        return SimpleNamespace(results=hits)

    def article(self, number, *, advisor=None):
        return SimpleNamespace(results=[])


def ask(llm, library, text: str, advisor_id: str):
    request = ChatRequest(chat_id=1, user_id=1, text=text)
    advisor = get_registry().get(advisor_id)
    return asyncio.run(respond(request, llm, library, agent=True, advisor=advisor))


SEARCH = calls(("search_knowledge_base", {"query": "سؤال"}))


def test_the_prompt_names_the_advisor_and_its_own_books_only() -> None:
    llm = WatchingLLM(answer("أهلاً"))
    library = Library({"real_estate": ["rent-law.pdf"], "legal": ["penal-code.pdf"]})

    ask(llm, library, "مرحبا", "real_estate")

    system = llm.systems[0]
    assert "المستشار العقاري" in system and "rent-law.pdf" in system
    assert "penal-code.pdf" not in system
    # Nothing about a specific corpus is written into the prompt any more.
    assert "penal code" not in system.lower()


def test_a_search_never_leaves_the_chosen_advisor() -> None:
    library = Library({"real_estate": ["rent-law.pdf"]})
    ask(WatchingLLM(SEARCH, answer("جواب [1].")), library, "سؤال عقاري", "real_estate")
    assert library.searched_with == ["real_estate"]


def test_article_lookup_is_offered_only_to_an_advisor_with_laws() -> None:
    with_laws = WatchingLLM(answer("أهلاً"))
    ask(with_laws, Library({"legal": ["law.pdf"]}, laws={"legal"}), "مرحبا", "legal")
    without = WatchingLLM(answer("أهلاً"))
    ask(without, Library({"economic": ["economics.pdf"]}), "مرحبا", "economic")

    assert with_laws.tools[0] == ["search_knowledge_base", "get_article"]
    assert without.tools[0] == ["search_knowledge_base"]


def test_an_advisor_with_no_books_says_so() -> None:
    reply, handled_by, _ = ask(WatchingLLM(SEARCH, answer("...")), Library({}), "سؤال", "companies")
    assert handled_by == "agent.empty_advisor"
    assert "مستشار الشركات" in reply and "لا يحتوي على كتب" in reply


def test_a_greeting_still_works_when_the_book_list_cannot_be_read() -> None:
    """Qdrant down must not take small talk with it."""
    llm = WatchingLLM(answer("أهلاً بك"))
    reply, handled_by, _ = ask(llm, Library({}, down=True), "مرحبا", "legal")
    assert handled_by == "agent.direct"
    assert "unavailable right now" in llm.systems[0]
