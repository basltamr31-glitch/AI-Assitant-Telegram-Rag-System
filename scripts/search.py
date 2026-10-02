"""Ask the knowledge base a question from the terminal.

Phase 7's whole point. Before retrieval is wired into the chat, you need to be
able to see what it actually returns - the passages, their scores, and where
the threshold falls - without a language model in the way rewriting the
evidence into something plausible.

    .venv\\Scripts\\python.exe scripts/search.py "ما هو المستقيم المقارب؟"
    .venv\\Scripts\\python.exe scripts/search.py "..." --domain curriculum
    .venv\\Scripts\\python.exe scripts/search.py "..." --threshold 0

Run it with `--threshold 0` to see the near-misses the real threshold is
hiding. That is the number Phase 14 will tune, and the only way to form a
view about it is to look at what sits just below it.

Without an argument it opens a loop, so you can probe the collection the way
you would a database.
"""

from __future__ import annotations

import argparse
import sys

from app.config import get_settings
from app.core.logging import configure_logging
from app.rag.retrieval import Retrieved, Retriever


def show(found: Retrieved, *, show_rejected: bool) -> None:
    if found.found:
        print(f"\n{len(found.results)} passage(s) at or above {found.threshold}:\n")
        for number, result in enumerate(found.results, start=1):
            print(f"  [{number}] {result.score:.3f}  {result.citation}")
            body = result.text.strip().replace("\n", " ")
            print(f"       {body[:200]}")
            print()
    else:
        print(f"\nNothing at or above {found.threshold}.")
        best = found.best_rejected_score
        if best is not None:
            print(f"Best was {best:.3f} - close enough to be worth looking at:")
        print()

    if show_rejected and found.rejected:
        print("  below the threshold:")
        for result in found.rejected:
            body = result.text.strip().replace("\n", " ")
            print(f"  [-] {result.score:.3f}  {result.citation}")
            print(f"       {body[:140]}")
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", help="the question; omit for a loop")
    parser.add_argument("--domain", default=None, choices=["curriculum", "legal"])
    parser.add_argument("--source", default=None, help="restrict to one document")
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="override the configured cutoff; 0 shows everything",
    )
    parser.add_argument(
        "--show-rejected",
        action="store_true",
        help="also print passages that fell below the threshold",
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(level="WARNING")  # the results are the output here

    retriever = Retriever()
    print(f"collection : {settings.qdrant_collection}")
    print(f"embedder   : {settings.embedding_model}")
    print(f"threshold  : {settings.retrieval_score_threshold}")

    def run(question: str) -> None:
        found = retriever.retrieve(
            question,
            domain=args.domain,
            source=args.source,
            top_k=args.top_k,
            threshold=args.threshold,
        )
        show(found, show_rejected=args.show_rejected or args.threshold == 0)

    if args.query:
        run(args.query)
        return 0

    print("\nType a question, or an empty line to quit.")
    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not question:
            return 0
        try:
            run(question)
        except Exception as exc:  # noqa: BLE001 - a REPL should survive a typo
            print(f"  failed: {type(exc).__name__}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
