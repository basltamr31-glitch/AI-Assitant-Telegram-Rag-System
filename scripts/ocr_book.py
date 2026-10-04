"""Read every page of a book into the OCR cache.

This is the slow half of ingestion, separated from the rest on purpose. Reading
226 scanned pages through free vision models takes an hour or two; chunking and
embedding the result takes minutes. Keeping them apart means the expensive work
is done once and the cheap work can be redone as often as the chunking strategy
changes.

    python scripts/ocr_book.py material/12-sci-math-1.pdf

Safe to interrupt and safe to re-run. Every page is written to the cache the
moment it is read, keyed by the hash of the source file, so a second run reads
only what is missing. Pages that defeat every model in the chain are recorded
as failures and skipped rather than ending the run: one unreadable page out of
226 is a page to look at by hand, not a reason to abandon a book.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from app.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.rag.loaders import document_sha, load_pdf
from app.rag.normalise import normalise
from app.rag.ocr import OcrError, PageCache, QuotaExhausted, VisionOcr

log = get_logger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", help="path to the source PDF")
    parser.add_argument(
        "--limit", type=int, default=0, help="stop after N pages (0 = the whole book)"
    )
    parser.add_argument(
        "--from-page", type=int, default=1, help="first page to read (1-based)"
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(level=settings.log_level)

    path = Path(args.pdf)
    if not path.exists():
        print(f"not found: {path}", file=sys.stderr)
        return 1

    document_id = path.stem
    doc_sha = document_sha(path)
    cache = PageCache(settings.ocr_cache_dir, document_id)
    ocr = VisionOcr()

    print(f"book      : {path.name}")
    print(f"sha       : {doc_sha}")
    print(f"cache     : {cache.dir}")
    print(f"models    : {', '.join(ocr._models)}")
    print()

    read = cached = failed = skipped = 0
    failures: list[int] = []
    consecutive = 0
    aborted = False
    started = time.time()

    for page in load_pdf(path, image_max_px=settings.ocr_image_max_px):
        if page.number < args.from_page:
            continue
        if args.limit and read + cached >= args.limit:
            break

        # A page whose text came straight out of the PDF needs no model.
        if not page.needs_ocr:
            if page.text:
                cache.put(page.number, doc_sha, normalise(page.text), "pdf-text")
                skipped += 1
            continue

        if cache.get(page.number, doc_sha) is not None:
            cached += 1
            continue

        page_started = time.time()
        try:
            text = normalise(ocr.read(page.image_png))
        except QuotaExhausted as exc:
            log.error("ocr.quota_exhausted", page=page.number)
            print()
            print(f"STOPPED at page {page.number}: {exc}")
            print("Everything read so far is cached. Re-run after the reset,")
            print("or add credits to raise the daily limit.")
            aborted = True
            break
        except OcrError as exc:
            # Recorded, not raised: the rest of the book is still worth having.
            log.error("ocr.page_failed", page=page.number, reason=str(exc)[:200])
            failures.append(page.number)
            failed += 1
            consecutive += 1
            if consecutive >= settings.ocr_abort_after_failures:
                # Not bad luck. Something the run cannot fix by continuing -
                # the network, the key, the quota - and grinding on costs
                # hours. Failures are already on disk, so a re-run resumes.
                log.error("ocr.aborted", consecutive_failures=consecutive)
                aborted = True
                break
            continue

        consecutive = 0

        cache.put(page.number, doc_sha, text, ",".join(ocr._models))
        read += 1
        elapsed = time.time() - page_started
        print(
            f"page {page.number:>4}  {elapsed:5.0f}s  {len(text):>5} chars"
            f"   (read {read}, cached {cached}, failed {failed})",
            flush=True,
        )

    minutes = (time.time() - started) / 60
    print()
    if aborted:
        print(f"STOPPED: {consecutive} pages failed in a row.")
        print("Something is wrong beyond this book. Check, in order:")
        print("  - the network: curl https://openrouter.ai/api/v1/models")
        print("  - OPENROUTER_API_KEY in .env")
        print("  - whether the models in OCR_MODELS still exist")
        print("Nothing is lost; re-running resumes from the cache.")
        print()
    print(f"read from models : {read}")
    print(f"already cached   : {cached}")
    print(f"text from PDF    : {skipped}")
    print(f"failed           : {failed}")
    print(f"elapsed          : {minutes:.1f} min")

    if failures:
        report = cache.dir / "failures.json"
        report.write_text(json.dumps(failures, indent=2), encoding="utf-8")
        print(f"\nfailed pages written to {report}")
        print("re-run this script to retry them; the rest will come from cache.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
