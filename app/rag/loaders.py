"""Turn a source file into pages, each either text or an image to be read.

The book that prompted this design
----------------------------------
`12-sci-math-1.pdf` is 232 pages produced in Adobe InDesign, and it is a
*hybrid*: the copyright page and the table of contents carry real text, while
every content page carries only its page number as text plus a 2421x3307 image
of the actual page. A loader that asked "is this PDF scanned?" would get the
wrong answer either way.

So the question is asked per page, not per file: if a page yields enough
extractable text, use it; otherwise hand the page image to OCR. That also
handles the ordinary cases for free - a born-digital legal PDF never reaches
the OCR path and costs nothing to ingest.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from pypdf import PdfReader

from app.core.logging import get_logger

log = get_logger(__name__)

# Below this many characters, a page is assumed to be an image with only its
# page number set as text. The content pages of the maths book return 2-3
# characters; the table of contents returns 30-100; a real page of prose
# returns thousands. 200 sits in the wide gap between them.
TEXT_THRESHOLD_CHARS = 200


@dataclass
class Page:
    """One page, in whichever form we could get it."""

    number: int  # 1-based, as printed
    text: str | None  # set when the PDF had extractable text
    image_png: bytes | None  # set when the page must be read by OCR

    @property
    def needs_ocr(self) -> bool:
        return self.text is None


def _largest_image(page, max_px: int) -> bytes | None:
    """Return the page's main image as PNG bytes, scaled down for OCR."""
    from PIL import Image  # noqa: PLC0415 - optional until a scan is met

    images = list(getattr(page, "images", []) or [])
    if not images:
        return None
    # Pages often carry a sliver of decoration alongside the real scan; the
    # biggest one is the page.
    best = max(images, key=lambda im: im.image.size[0] * im.image.size[1])
    img: Image.Image = best.image.convert("RGB")
    img.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def load_pdf(path: str | Path, image_max_px: int = 1500) -> Iterator[Page]:
    """Yield every page of `path`, deciding per page whether OCR is needed."""
    reader = PdfReader(str(path))
    total = len(reader.pages)
    log.info("loader.pdf_opened", path=str(path), pages=total)

    for index, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if len(text) >= TEXT_THRESHOLD_CHARS:
            yield Page(number=index, text=text, image_png=None)
            continue

        image = _largest_image(page, image_max_px)
        if image is None:
            # Genuinely blank - a section divider, or the back of a cover.
            log.debug("loader.empty_page", page=index)
            yield Page(number=index, text=text or "", image_png=None)
            continue

        yield Page(number=index, text=None, image_png=image)


def document_sha(path: str | Path) -> str:
    """A cheap, stable identity for a source file.

    Hashing 118 MB takes under a second and happens once per run, whereas
    extracting one page image costs about 1.5 seconds. Keying the OCR cache on
    this lets a resumed run skip extraction entirely for pages it already has.
    """
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()[:16]
