r"""Cut pages into the units a retriever should return.

The unit is the point
---------------------
A chunk is what gets embedded and what comes back from a search, so its
boundaries decide what the assistant can answer with. Half an exercise is
useless: the question without its conditions, or the conditions without the
question. Half an article of law is worse than useless, because it reads as
complete and is not.

This is why `ROADMAP.md` records "the two domains need different chunking" as
a consequence of covering both. They genuinely do:

* **Curriculum.** The natural unit is one numbered exercise. The textbook
  marks them clearly - `**10**`, `10.`, or a circled numeral - and an exercise
  is self-contained by construction, because a student is meant to be able to
  attempt it on its own.
* **Law.** The natural unit is the article (`المادة 5`). Articles are the
  thing lawyers cite, so a chunk that is exactly one article is also a chunk
  that can be cited precisely - which is what makes grounded legal answers
  checkable rather than merely confident.

Where no structure is found, the text falls back to paragraphs packed up to a
size limit. That path exists so an unexpected document still ingests; it is
not meant to be the good case.

Mathematics is never split
--------------------------
A `$$...$$` block is atomic. Splitting one produces two fragments of LaTeX,
neither of which is a formula, and both of which embed as noise.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Literal

Domain = Literal["curriculum", "legal", "generic"]

# An exercise number, and *only* an exercise number. Reading the real pages
# settled this: the textbook marks exercises in bold - `**20.**` - while the
# parts within an exercise are `(1)`, `(2)` and `ا.`, `ب.`, `ج.`.
#
# An earlier version also matched a bare `20.` and circled numerals, and cut
# every exercise into its own sub-questions: 111 chunks with a median length
# of 110 characters, each a sub-question severed from the stem that gave it
# meaning. The chunks looked plausible in a listing, which is how that kind of
# mistake survives. Sub-items must stay with their exercise.
_EXERCISE = re.compile(
    r"^\*\*\s*(\d{1,3})\s*[.)-]?\s*\*\*\s",
    re.MULTILINE,
)

# An article of law: المادة followed by a number, in Arabic or Western digits.
# An article of law. Two orderings, because PDF text extraction does not
# always preserve the visual one: a page that reads `المادة 109` can extract as
# `109المادة`, with the number first. The Syrian penal code does exactly
# that, and a pattern that only knew the natural order found zero articles
# in 121 pages while looking entirely reasonable.
_ARTICLE = re.compile(
    r"(?m)^[\s\(ـ]*(?:(?:المادة|مادة)[\s\(ـ]*([0-9٠-٩]{1,4})"
    r"|([0-9٠-٩]{1,4})[\s\(ـ]*(?:المادة|مادة))"
)

# Display mathematics, which must never be cut through.
_DISPLAY_MATH = re.compile(r"\$\$.*?\$\$", re.DOTALL)

_PARAGRAPH = re.compile(r"\n\s*\n")


@dataclass
class Chunk:
    """One retrievable unit, with everything needed to cite it."""

    text: str
    source: str  # file name, e.g. "12-sci-math-1.pdf"
    domain: Domain
    page: int
    index: int  # position within the document, for stable ordering
    label: str = ""  # "تمرين 10" or "المادة 5", when the structure gave one
    metadata: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        """Stable across re-ingestion, so a re-run updates rather than duplicates."""
        key = f"{self.source}:{self.page}:{self.index}:{self.content_hash}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:16]


def _protect_math(text: str) -> tuple[str, list[str]]:
    """Replace display formulas with placeholders so splitting cannot cut them."""
    blocks: list[str] = []

    def take(match: re.Match[str]) -> str:
        blocks.append(match.group(0))
        return f"\x00MATH{len(blocks) - 1}\x00"

    return _DISPLAY_MATH.sub(take, text), blocks


def _restore_math(text: str, blocks: list[str]) -> str:
    for i, block in enumerate(blocks):
        text = text.replace(f"\x00MATH{i}\x00", block)
    return text


def _split_on(pattern: re.Pattern[str], text: str) -> list[tuple[str, str]]:
    """Split `text` at each match, returning (label, body) pairs.

    Anything before the first match is returned with an empty label: it is
    usually a page header, and dropping it silently would lose content.
    """
    matches = list(pattern.finditer(text))
    if not matches:
        return []

    pieces: list[tuple[str, str]] = []
    if matches[0].start() > 0:
        lead = text[: matches[0].start()].strip()
        if lead:
            pieces.append(("", lead))

    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        number = next((g for g in match.groups() if g), "")
        pieces.append((number, text[match.start() : end].strip()))
    return pieces


def _pack_paragraphs(text: str, max_chars: int) -> list[str]:
    """Group paragraphs into chunks no larger than `max_chars`."""
    out: list[str] = []
    current = ""
    for para in _PARAGRAPH.split(text):
        para = para.strip()
        if not para:
            continue
        if current and len(current) + len(para) + 2 > max_chars:
            out.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        out.append(current)
    return out


def chunk_page(
    text: str,
    *,
    source: str,
    page: int,
    domain: Domain = "generic",
    max_chars: int = 1400,
    start_index: int = 0,
) -> list[Chunk]:
    """Cut one page of text into chunks, using whatever structure it has."""
    text = text.strip()
    if not text:
        return []

    protected, math_blocks = _protect_math(text)

    pattern = {"curriculum": _EXERCISE, "legal": _ARTICLE}.get(domain)
    pieces = _split_on(pattern, protected) if pattern else []

    if not pieces:
        pieces = [("", body) for body in _pack_paragraphs(protected, max_chars)]

    label_word = {"curriculum": "تمرين", "legal": "المادة"}.get(domain, "")

    chunks: list[Chunk] = []
    for offset, (number, body) in enumerate(pieces):
        body = _restore_math(body, math_blocks).strip()
        if not body:
            continue
        # A structural unit longer than the limit is still one unit; splitting
        # it would undo the only thing that made it worth finding.
        chunks.append(
            Chunk(
                text=body,
                source=source,
                domain=domain,
                page=page,
                index=start_index + offset,
                label=f"{label_word} {number}".strip() if number else "",
            )
        )
    return chunks
