"""Talk to the MCP server the way Claude Desktop does - and watch it happen.

Phase 11's exit criterion is "a real tool call over MCP, end to end". This
is the first client: it starts `app.mcp.server` as a separate process, speaks
JSON-RPC to it over stdin/stdout, and prints each step - what the server says
it offers, then a tool call and its result.

    .venv\\Scripts\\python.exe scripts/mcp_client.py
    .venv\\Scripts\\python.exe scripts/mcp_client.py "عقوبة الرشوة"
    .venv\\Scripts\\python.exe scripts/mcp_client.py --article 535

No model is involved. Here *you* choose the tool; in Claude Desktop its model
does. The server cannot tell the difference, which is the point of a protocol.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]


def text_of(result) -> str:
    return "\n".join(getattr(c, "text", "") for c in result.content)


async def run(query: str, article: int | None) -> None:
    params = StdioServerParameters(
        # This interpreter, so the server gets the venv's packages.
        command=sys.executable,
        args=["-m", "app.mcp.server"],
        cwd=str(ROOT),
    )
    print(f"starting the server: {sys.executable} -m app.mcp.server\n")

    async with Client(params, read_timeout_seconds=300) as client:
        info = client.server_info
        print(f"connected to  : {info.name if info else '?'}")

        tools = (await client.list_tools()).tools
        print(f"tools         : {', '.join(t.name for t in tools)}")
        resources = (await client.list_resources()).resources
        templates = (await client.list_resource_templates()).resource_templates
        print(f"resources     : {', '.join(str(r.uri) for r in resources)}")
        print(f"templates     : {', '.join(t.uri_template for t in templates)}\n")

        documents = await client.read_resource("kb://documents")
        print("kb://documents:")
        print(documents.contents[0].text, "\n")

        if article is not None:
            name, arguments = "get_article", {"number": article}
        else:
            name, arguments = "search_knowledge_base", {"query": query}
        print(f"call_tool({name}, {arguments})  - the embedder loads on the first call\n")
        result = await client.call_tool(name, arguments)
        print("error" if result.is_error else "result", "-" * 60)
        print(text_of(result))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("query", nargs="?", default="عقوبة السرقة ليلا")
    parser.add_argument("--article", type=int, help="look an article up by number instead")
    args = parser.parse_args()
    if sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(run(args.query, args.article))
    return 0


if __name__ == "__main__":
    sys.exit(main())
