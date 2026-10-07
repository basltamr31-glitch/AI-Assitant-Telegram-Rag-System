"""The tools the model may call, and the evidence they collect.

What a tool is here
-------------------
A Python function, plus a description written for the model. The model reads
the description and decides whether to call it; Python runs it. Nothing the
model writes is executed - it chooses a name from a fixed list and fills in
arguments, and an unknown name or a missing argument is reported back to it
as an error, not trusted.

Evidence, numbered once per turn
--------------------------------
In Phase 8 there was one search per message, so `[1]` meant "the first
passage of that search". An agent can search twice, or search and then look
up an article, and two results both numbered `[1]` would make every citation
ambiguous. So every passage retrieved in a turn joins one list, numbered in
the order it arrived, and a passage found twice keeps its first number. The
model sees those numbers in the tool results and cites them; the responder
checks each citation against this same list.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.llm.base import ToolCall, ToolSpec
from app.rag.retrieval import Retriever
from app.rag.store import SearchResult

log = get_logger(__name__)

SEARCH = ToolSpec(
    name="search_knowledge_base",
    description=(
        "Search the user's own documents: the Syrian penal code (قانون العقوبات) "
        "and a Syrian school mathematics textbook. Use it for any question about "
        "law, crimes, punishments, or the curriculum - never answer those from "
        "memory. Write the query in Arabic, as the key words of what is being "
        "asked; for a follow-up, include what it follows up on (not 'وإذا كان "
        "مسلحاً' but 'عقوبة السرقة مع حمل السلاح'). Returns numbered passages "
        "to cite, or NO_RESULTS."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for, in Arabic."},
        },
        "required": ["query"],
    },
)

ARTICLE = ToolSpec(
    name="get_article",
    description=(
        "Fetch one article of the Syrian penal code by its number, exactly. Use "
        "it when the user names an article (المادة 535) - a search would return "
        "neighbouring articles that merely sound similar. Returns the article "
        "as a numbered passage, or NO_RESULTS if there is no such article."
    ),
    parameters={
        "type": "object",
        "properties": {
            "number": {"type": "integer", "description": "The article number, e.g. 535."},
        },
        "required": ["number"],
    },
)

TOOLS = (SEARCH, ARTICLE)

NO_RESULTS = (
    "NO_RESULTS: nothing in the user's documents matches this. Do not answer "
    "from your own knowledge."
)


@dataclass
class Evidence:
    """Every passage retrieved during one turn, numbered for citation."""

    passages: list[SearchResult] = field(default_factory=list)
    searched: bool = False

    def add(self, results: list[SearchResult], max_chars: int = 6000) -> str:
        """Number new passages and return them formatted for the model."""
        self.searched = True
        if not results:
            return NO_RESULTS
        blocks: list[str] = []
        used = 0
        for result in results:
            number = self._number_of(result)
            block = f"[{number}] {result.citation}\n{result.text}"
            if used + len(block) > max_chars and blocks:
                # Stop at a passage boundary: half an article read as whole
                # is the error that matters here.
                break
            blocks.append(block)
            used += len(block)
        return "\n\n".join(blocks)

    def _number_of(self, result: SearchResult) -> int:
        key = (result.source, result.page, result.label, result.text)
        for i, known in enumerate(self.passages, start=1):
            if (known.source, known.page, known.label, known.text) == key:
                return i
        self.passages.append(result)
        return len(self.passages)

    def citations(self) -> list[str]:
        return [p.citation for p in self.passages]


def execute(
    call: ToolCall,
    retriever: Retriever,
    evidence: Evidence,
    *,
    context: str | None = None,
    max_chars: int = 6000,
) -> str:
    """Run one tool call and return what the model will read.

    Errors come back as text, not exceptions: a model told "get_article
    needs a number" can correct itself on the next round, while an
    exception would end the turn over a fixable mistake.
    """
    log.info("agent.tool_call", tool=call.name, arguments=sorted(call.arguments))
    if call.name == SEARCH.name:
        query = str(call.arguments.get("query", "")).strip()
        if not query:
            return "ERROR: search_knowledge_base needs a non-empty 'query'."
        found = retriever.retrieve(query, context=context)
        return evidence.add(found.results, max_chars)
    if call.name == ARTICLE.name:
        number = call.arguments.get("number")
        if number in (None, ""):
            return "ERROR: get_article needs an article 'number'."
        found = retriever.article(number)
        return evidence.add(found.results, max_chars)
    log.warning("agent.unknown_tool", tool=call.name)
    return f"ERROR: there is no tool called '{call.name}'. Use one of: " + ", ".join(
        t.name for t in TOOLS
    )
