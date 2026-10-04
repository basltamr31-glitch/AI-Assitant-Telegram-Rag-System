"""Remove the lines a document repeats on every page.

Why this needs the whole document
---------------------------------
A running header is invisible from inside a single page. `1/25/2019 ... قانون
العقوبات العام ...` looks like content until you notice it opens all 121 pages
of the Syrian penal code, which was printed to PDF from a web page and carries
the browser's header and footer throughout.

Left in, it costs twice. Every page contributes a chunk that is nothing but
boilerplate, so a search for the document's own title matches 121 identical
passages and nothing useful. And the boilerplate rides along inside real
chunks, diluting the embedding of the article it was glued to.

So the rule is statistical rather than hand-written: a line that appears on
most pages of a document is furniture, not content. No list of patterns to
maintain, and it works the same on the next document, whose header will be
different and equally unknown.

What it deliberately will not remove
------------------------------------
A line has to be long enough to be distinctive. Short lines repeat for honest
reasons - a page number, a bare `المادة`, an empty string - and dropping those
would quietly delete structure.
"""

from __future__ import annotations

from collections import Counter

from app.core.logging import get_logger

log = get_logger(__name__)

# Below this, a repeated line is more likely to be structure than furniture.
MIN_LENGTH = 25

# Appearing on this share of pages marks a line as boilerplate. High, because
# the cost of being wrong is deleting real content: a sentence that genuinely
# appears on 60% of a document's pages would be remarkable.
MIN_SHARE = 0.6


def find_boilerplate(
    pages: list[str],
    *,
    min_length: int = MIN_LENGTH,
    min_share: float = MIN_SHARE,
) -> set[str]:
    """Return the lines that appear on at least `min_share` of the pages."""
    if len(pages) < 3:
        # Too few pages for repetition to mean anything.
        return set()

    counts: Counter[str] = Counter()
    for page in pages:
        # Per page, not per occurrence: a line printed twice on one page is
        # still only evidence from one page.
        seen = {
            line.strip()
            for line in page.split("\n")
            if len(line.strip()) >= min_length
        }
        counts.update(seen)

    threshold = max(3, int(len(pages) * min_share))
    return {line for line, count in counts.items() if count >= threshold}


def strip_boilerplate(
    pages: list[str],
    *,
    min_length: int = MIN_LENGTH,
    min_share: float = MIN_SHARE,
) -> list[str]:
    """Return the pages with their repeated furniture removed."""
    furniture = find_boilerplate(pages, min_length=min_length, min_share=min_share)
    if not furniture:
        return pages

    log.info(
        "boilerplate.found",
        lines=len(furniture),
        pages=len(pages),
        sample=next(iter(furniture))[:60],
    )

    cleaned = []
    for page in pages:
        kept = [
            line
            for line in page.split("\n")
            if line.strip() not in furniture
        ]
        cleaned.append("\n".join(kept).strip())
    return cleaned
