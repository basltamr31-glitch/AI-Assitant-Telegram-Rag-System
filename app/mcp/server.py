"""The knowledge base, offered to other AI clients over MCP.

What this is, and what it is not
--------------------------------
ADR-006: the Telegram bot calls its retrieval code directly, in-process, and
always will. This server exists for *other* clients - Claude Desktop, an IDE,
another agent - that want the same documents. It runs as its own process and
speaks MCP over stdio: the client starts it, writes JSON-RPC to its stdin and
reads replies from its stdout.

It reuses the bot's code rather than copying it. The tools are the same two
the agent has, with the same descriptions, the same threshold and the same
`NO_RESULTS` refusal - a passage below 0.45 is no more trustworthy because a
different client asked for it. ADR-006's one cost is "two call paths to the
knowledge base, so the shared logic must stay in one module both can
import"; that module is `app/agent/tools.py`.

What it offers
--------------
* **Tools** - actions the client's model may decide to take:
  `search_knowledge_base(query)` and `get_article(number)`.
* **Resources** - data the client (or its user) can read directly:
  `kb://documents`, what is in the knowledge base, and
  `kb://article/{number}`, one article by number.

Read-only throughout. Nothing here writes, deletes or re-ingests: a client
that can reach this server can read your documents and nothing else.

Running it
----------
    .venv\\Scripts\\python.exe -m app.mcp.server

It then waits silently for a client on stdin - which is correct, not a hang.
`scripts/mcp_client.py` is a client that starts it and calls a tool.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from app.agent.tools import ARTICLE, NO_RESULTS, SEARCH, Evidence
from app.core.logging import get_logger
from app.rag.retrieval import Retriever

log = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

server = MCPServer(
    name="telegram-assistant-knowledge-base",
    # The SDK logs every HTTP request at INFO through its own handler; to a
    # client that forwards stderr, that is a screen of noise per question.
    log_level="WARNING",
    title="Arabic knowledge base: Syrian penal code and maths curriculum",
    instructions=(
        "Search the user's own Arabic documents. Answer only from what the "
        "tools return, cite the passage numbers they give, and when a tool "
        "returns NO_RESULTS say the documents do not cover it rather than "
        "answering from memory."
    ),
)

# Built on first use: the embedder takes seconds to load, and a client that
# only lists the tools should not have to wait for it. Tests replace it.
_retriever: Retriever | None = None


def retriever() -> Retriever:
    global _retriever
    if _retriever is None:
        _retriever = Retriever()
    return _retriever


READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)


@server.tool(name=SEARCH.name, description=SEARCH.description, annotations=READ_ONLY)
def search_knowledge_base(query: str) -> str:
    # Each call numbers its own passages from [1]. The bot numbers across a
    # whole turn; here there is no turn, only the client's calls, and a
    # client that calls twice sees two independently numbered lists.
    log.info("mcp.tool_call", tool=SEARCH.name)
    if not query.strip():
        return "ERROR: search_knowledge_base needs a non-empty 'query'."
    return Evidence().add(retriever().retrieve(query).results)


@server.tool(name=ARTICLE.name, description=ARTICLE.description, annotations=READ_ONLY)
def get_article(number: int) -> str:
    log.info("mcp.tool_call", tool=ARTICLE.name)
    return Evidence().add(retriever().article(number).results)


@server.resource(
    "kb://documents",
    name="documents",
    description="The documents in the knowledge base and how many passages each holds.",
    mime_type="application/json",
)
def documents() -> str:
    return json.dumps(retriever().documents(), ensure_ascii=False, indent=2)


@server.resource(
    "kb://article/{number}",
    name="article",
    description="One article of the Syrian penal code, by number.",
    mime_type="text/plain",
)
def article(number: str) -> str:
    found = retriever().article(number)
    if not found.results:
        return NO_RESULTS
    return "\n\n".join(f"{r.citation}\n{r.text}" for r in found.results)


def main() -> None:
    # A client such as Claude Desktop starts this from its own working
    # directory, where there is no `.env` - and settings are read from
    # `.env` relative to the working directory.
    os.chdir(PROJECT_ROOT)

    from app.config import get_settings
    from app.core.logging import configure_logging

    # stderr, never stdout: on the stdio transport, stdout is the protocol.
    configure_logging(level=get_settings().log_level, stream=sys.stderr)
    log.info("mcp.starting", transport="stdio", project=str(PROJECT_ROOT))
    server.run("stdio")


if __name__ == "__main__":
    main()
