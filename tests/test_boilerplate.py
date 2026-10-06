"""Tests for removing a document's running furniture.

The case that prompted this: the Syrian penal code was printed to PDF from a
web page, so all 121 of its pages open with the browser's header. Left in, it
contributes 121 chunks that are nothing but boilerplate, and rides along
inside the real ones, diluting the embedding of whatever article it was glued
to.
"""

from __future__ import annotations

from app.rag.boilerplate import find_boilerplate, strip_boilerplate

HEADER = "1/25/2019 قانون العقوبات العام الصادر بالمرسوم التشريعي رقم 148"


def pages_with_header(n: int) -> list[str]:
    return [f"{HEADER}\nالمادة {i}\nنص المادة رقم {i} وتفاصيلها." for i in range(1, n + 1)]


def test_a_line_on_every_page_is_found() -> None:
    assert HEADER in find_boilerplate(pages_with_header(20))


def test_the_header_is_removed_and_the_content_is_not() -> None:
    cleaned = strip_boilerplate(pages_with_header(20))
    assert all(HEADER not in page for page in cleaned)
    assert "نص المادة رقم 7" in cleaned[6]


def test_a_line_on_a_few_pages_is_content() -> None:
    """Repetition has to be near-universal before it means furniture."""
    pages = [f"فقرة فريدة رقم {i} فيها نص كاف لتكون مميزة" for i in range(30)]
    pages[0] = pages[1] = "عبارة تتكرر مرتين فقط وهي طويلة بما يكفي للقياس"
    assert find_boilerplate(pages) == set()


def test_short_repeated_lines_are_structure_not_furniture() -> None:
    """A bare `المادة` repeats honestly; deleting it would remove structure."""
    pages = ["المادة\nنص مختلف " + str(i) * 20 for i in range(20)]
    assert "المادة" not in find_boilerplate(pages)


def test_too_few_pages_to_judge() -> None:
    assert find_boilerplate(["أ" * 40, "أ" * 40]) == set()


def test_a_document_without_furniture_is_returned_unchanged() -> None:
    pages = [f"نص الصفحة رقم {i} وهو مختلف تماما عن غيره" for i in range(10)]
    assert strip_boilerplate(pages) == pages


FOOTER = "http://parliament.gov.sy/arabic/eindex.php?node=201&print=1"


def test_a_footer_with_a_page_counter_is_found() -> None:
    """`... 45/121` and `... 105/121` are one footer, not 121 distinct lines."""
    pages = [f"نص الصفحة {i}\n{FOOTER} {i}/20" for i in range(1, 21)]
    cleaned = strip_boilerplate(pages)
    assert all(FOOTER not in page for page in cleaned)
    assert cleaned[4] == "نص الصفحة 5"


def test_lines_that_differ_only_by_number_are_content() -> None:
    """Articles of a law differ by their number; that is not repetition."""
    pages = [f"نص المادة رقم {i} من هذا القانون وتفاصيلها" for i in range(1, 21)]
    assert find_boilerplate(pages) == set()
