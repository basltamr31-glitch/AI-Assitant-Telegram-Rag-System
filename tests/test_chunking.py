r"""Tests for chunking.

The curriculum fixtures are shortened from the real OCR output of pages 62-64
of `12-sci-math-1.pdf`. The `**20.**` form matters: an earlier pattern assumed
`**20**` without the dot, matched nothing, and silently sent every page down
the paragraph-packing fallback. The chunks still looked reasonable, which is
exactly why it went unnoticed until the labels were inspected.
"""

from __future__ import annotations

from app.rag.chunking import Chunk, chunk_page

CURRICULUM_PAGE = (
    "**20.** ليكن $C$ الخط البياني للتابع $f$ المعرف وفق\n"
    "$$f(x) = \\frac{3x^2 + 6x}{x^2 - x - 2}$$\n"
    "ادرس نهاية $f$ عند حدود مجموعة التعريف.\n"
    "**21.** ليكن $g$ التابع المعرف على $\\mathbb{R}$.\n"
    "اثبت ان $g$ محدود.\n"
    "**22.** جد الاعداد $a$ و $b$.\n"
)

LEGAL_PAGE = (
    "المادة 5\n"
    "يلتزم البائع بتسليم المبيع في الحالة التي كان عليها وقت البيع.\n"
    "المادة 6\n"
    "اذا هلك المبيع قبل التسليم فلا يلتزم المشتري بالثمن.\n"
)


def _labels(chunks: list[Chunk]) -> list[str]:
    return [c.label for c in chunks]


# --- curriculum --------------------------------------------------------------


def test_each_exercise_becomes_its_own_chunk() -> None:
    """Half an exercise is useless: the question without its conditions."""
    chunks = chunk_page(
        CURRICULUM_PAGE, source="book.pdf", page=62, domain="curriculum"
    )
    assert len(chunks) == 3
    assert _labels(chunks) == ["تمرين 20", "تمرين 21", "تمرين 22"]


def test_an_exercise_keeps_its_own_formula() -> None:
    chunks = chunk_page(
        CURRICULUM_PAGE, source="book.pdf", page=62, domain="curriculum"
    )
    assert "\\frac{3x^2 + 6x}{x^2 - x - 2}" in chunks[0].text
    # ...and only its own.
    assert "\\frac" not in chunks[1].text


def test_display_maths_is_never_split() -> None:
    """Two halves of a formula are not two formulas; they are noise."""
    page = "مقدمة\n\n$$\\int_0^1 f(x)\\,dx = \\frac{1}{2}$$\n\nخاتمة"
    for chunk in chunk_page(page, source="b.pdf", page=1, max_chars=20):
        opens = chunk.text.count("$$")
        assert opens % 2 == 0, "a $$...$$ block was cut in half"


# --- legal -------------------------------------------------------------------


def test_each_article_becomes_its_own_chunk() -> None:
    """Articles are what lawyers cite, so they are what a chunk must be."""
    chunks = chunk_page(LEGAL_PAGE, source="law.pdf", page=3, domain="legal")
    assert len(chunks) == 2
    assert _labels(chunks) == ["المادة 5", "المادة 6"]


def test_an_article_chunk_carries_its_whole_text() -> None:
    chunks = chunk_page(LEGAL_PAGE, source="law.pdf", page=3, domain="legal")
    assert "يلتزم البائع بتسليم المبيع" in chunks[0].text
    assert "اذا هلك المبيع" in chunks[1].text


def test_the_wrong_domain_finds_no_structure() -> None:
    """A legal page chunked as curriculum must not invent exercises."""
    chunks = chunk_page(LEGAL_PAGE, source="law.pdf", page=3, domain="curriculum")
    assert all(c.label == "" for c in chunks)


# --- fallback and metadata ---------------------------------------------------


def test_unstructured_text_falls_back_to_paragraphs() -> None:
    page = "\n\n".join(f"فقرة رقم {i} فيها نص كاف لتكوين وحدة." for i in range(6))
    chunks = chunk_page(page, source="x.pdf", page=1, max_chars=120)
    assert len(chunks) > 1
    assert all(len(c.text) <= 200 for c in chunks)


def test_an_empty_page_produces_nothing() -> None:
    assert chunk_page("", source="x.pdf", page=1) == []
    assert chunk_page("   \n\n  ", source="x.pdf", page=1) == []


def test_chunk_ids_are_stable_across_runs() -> None:
    """Re-ingesting must update rows, not duplicate them."""
    first = chunk_page(CURRICULUM_PAGE, source="b.pdf", page=62, domain="curriculum")
    again = chunk_page(CURRICULUM_PAGE, source="b.pdf", page=62, domain="curriculum")
    assert [c.id for c in first] == [c.id for c in again]


def test_chunk_ids_differ_when_the_text_differs() -> None:
    a = chunk_page("نص أول", source="b.pdf", page=1)[0]
    b = chunk_page("نص ثان", source="b.pdf", page=1)[0]
    assert a.id != b.id


def test_every_chunk_can_cite_itself() -> None:
    """Without page and source, a retrieved passage cannot be checked."""
    for chunk in chunk_page(
        CURRICULUM_PAGE, source="12-sci-math-1.pdf", page=62, domain="curriculum"
    ):
        assert chunk.source == "12-sci-math-1.pdf"
        assert chunk.page == 62
        assert chunk.domain == "curriculum"
