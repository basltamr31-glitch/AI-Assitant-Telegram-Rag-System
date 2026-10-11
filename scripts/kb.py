"""Manage what is in the knowledge base, and which advisor each book serves.

    .venv\\Scripts\\python.exe scripts/kb.py list
    .venv\\Scripts\\python.exe scripts/kb.py assign civil-law.pdf legal real_estate
    .venv\\Scripts\\python.exe scripts/kb.py remove civil-law.pdf --yes

`assign` replaces the book's advisors with the ones given - a payload
update, no re-embedding, a second even for a whole law. `remove` deletes
the book's passages from the knowledge base; its OCR cache stays, so adding
it back is an ingest, not a re-read.

Adding a book is still `ocr_book.py` then `ingest.py` - see the README.
"""

from __future__ import annotations

import argparse
import sys

from app.advisors import get_registry
from app.core.logging import configure_logging
from app.rag.store import VectorStore


def list_books(store: VectorStore) -> int:
    registry = get_registry()
    documents = store.documents()
    if not documents:
        print("the knowledge base is empty")
        return 0
    print(f"{'document':<40} {'passages':>8}  advisors")
    for source, count in sorted(documents.items()):
        advisors = store.advisors_of(source)
        names = ", ".join(registry.get(a).name if a in registry.ids else f"?{a}" for a in advisors)
        print(f"{source:<40} {count:>8}  {names or '(none - no advisor can see it)'}")
    print()
    for advisor in registry.advisors:
        books = store.documents(advisor.id)
        print(f"{advisor.icon} {advisor.name:<22} {len(books)} book(s)")
    return 0


def assign(store: VectorStore, source: str, advisors: list[str]) -> int:
    registry = get_registry()
    unknown = [a for a in advisors if a not in registry.ids]
    if unknown:
        print(f"unknown advisor(s) {unknown}; known: {registry.ids}", file=sys.stderr)
        return 1
    if source not in store.documents():
        print(f"no document '{source}' in the knowledge base - see `kb.py list`", file=sys.stderr)
        return 1
    store.ensure_advisor_index()
    store.set_advisors(source, advisors)
    print(f"{source} -> {', '.join(registry.get(a).name for a in advisors)}")
    return 0


def remove(store: VectorStore, source: str, confirmed: bool) -> int:
    documents = store.documents()
    if source not in documents:
        print(f"no document '{source}' in the knowledge base", file=sys.stderr)
        return 1
    if not confirmed:
        print(f"this deletes {documents[source]} passages of {source}. Run again with --yes.")
        return 1
    store.delete_source(source)
    print(f"removed {source}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="books, passage counts and advisors")
    a = sub.add_parser("assign", help="set the advisors a book belongs to")
    a.add_argument("source", help="the document name, as `list` shows it")
    a.add_argument("advisors", nargs="+", help="advisor ids from advisors.json")
    r = sub.add_parser("remove", help="delete a book from the knowledge base")
    r.add_argument("source")
    r.add_argument("--yes", action="store_true", help="really delete")
    args = parser.parse_args()

    if sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    configure_logging(level="WARNING", stream=sys.stderr)
    store = VectorStore()
    if args.command == "list":
        return list_books(store)
    if args.command == "assign":
        return assign(store, args.source, args.advisors)
    return remove(store, args.source, args.yes)


if __name__ == "__main__":
    sys.exit(main())
