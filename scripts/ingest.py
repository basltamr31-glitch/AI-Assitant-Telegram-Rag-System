"""Chunk the OCR cache, embed it, and store it in Qdrant.

The fast half of ingestion. `ocr_book.py` does the slow, expensive half once;
this runs in minutes and can be re-run freely whenever the chunking strategy
changes, which is exactly why the two are separate.

    python scripts/ingest.py 12-sci-math-1 --domain curriculum

The argument is the cache directory name, which is the PDF's stem. Chunk ids
are derived from their content and position, so re-running updates rows rather
than duplicating them - but note that changing the chunking changes the ids,
and old rows for the same document are removed first to avoid leaving orphans.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.rag.boilerplate import strip_boilerplate
from app.rag.chunking import Chunk, chunk_page
from app.rag.embedder import LocalEmbedder
from app.rag.normalise import normalise
from app.rag.store import VectorStore

log = get_logger(__name__)


def load_cached_pages(cache_dir: Path) -> list[dict]:
    """Read every cached page, in page order."""
    pages = []
    for path in sorted(cache_dir.glob("page_*.json")):
        try:
            pages.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            log.warning("ingest.unreadable_cache_file", path=str(path))
    return pages


def build_chunks(pages: list[dict], source: str, domain: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    for page in pages:
        text = (page.get("text") or "").strip()
        if not text:
            continue
        chunks.extend(
            chunk_page(
                text,
                source=source,
                page=page["page"],
                domain=domain,  # type: ignore[arg-type]
                start_index=len(chunks),
            )
        )
    return chunks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", help="cache directory name (the PDF's stem)")
    parser.add_argument(
        "--domain",
        default="generic",
        choices=["curriculum", "legal", "generic"],
        help="decides how pages are cut: exercises, articles, or paragraphs",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="chunk and report, but do not embed or store",
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(level=settings.log_level)

    cache_dir = Path(settings.ocr_cache_dir) / args.document
    if not cache_dir.exists():
        print(f"no cache at {cache_dir} - run scripts/ocr_book.py first", file=sys.stderr)
        return 1

    pages = load_cached_pages(cache_dir)
    source = f"{args.document}.pdf"

    # Running headers are invisible from inside one page and obvious across a
    # document. Stripping them here, where the whole document is in hand, is
    # the only place it can be done at all.
    # Normalising here rather than in the OCR cache: the rules change as new
    # documents reveal new damage, and re-running this is free while
    # re-reading a page is not. `normalise` is idempotent, so pages cached by
    # an older pipeline that normalised on the way in come out the same.
    texts = strip_boilerplate([normalise(p.get("text", "")) for p in pages])
    for page, text in zip(pages, texts):
        page["text"] = text

    chunks = build_chunks(pages, source, args.domain)

    print(f"document : {source}")
    print(f"domain   : {args.domain}")
    print(f"pages    : {len(pages)} cached")
    print(f"chunks   : {len(chunks)}")
    if chunks:
        sizes = sorted(len(c.text) for c in chunks)
        labelled = sum(1 for c in chunks if c.label)
        print(f"           {labelled} labelled, median {sizes[len(sizes) // 2]} chars")
        print()
        for chunk in chunks[:3]:
            head = chunk.text[:70].replace("\n", " ")
            print(f"  [{chunk.label or '-':<12}] p.{chunk.page:<4} {head}")

    if not chunks:
        print("\nnothing to ingest")
        return 0

    if args.dry_run:
        print("\ndry run - nothing embedded or stored")
        return 0

    embedder = LocalEmbedder()
    store = VectorStore()
    store.ensure_collection(embedder.dimension)
    # Clear this document's previous chunks first: new chunking or new
    # normalisation means new ids, and the old rows would otherwise stay
    # and keep matching searches.
    store.delete_source(source)

    print(f"\nembedding {len(chunks)} chunks with {embedder.model_name} ...")
    vectors = embedder.embed_documents([c.text for c in chunks])
    stored = store.upsert(chunks, vectors)

    print(f"stored   : {stored} chunks")
    print(f"total in collection: {store.count()} ({store.count(args.domain)} in this domain)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
