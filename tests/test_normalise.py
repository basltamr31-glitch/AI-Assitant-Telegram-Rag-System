"""Tests for Arabic normalisation.

Several of these come from the real OCR output of page 60 of
`12-sci-math-1.pdf`, not from imagination. The Persian yeh in particular is
what the vision model actually produced.
"""

from __future__ import annotations

from app.rag.normalise import normalise


def test_persian_yeh_becomes_arabic_yeh() -> None:
    """Straight from the OCR run: `لیکن` came back with U+06CC."""
    assert normalise("ل\u06ccكن") == "ليكن"


def test_persian_kaf_becomes_arabic_kaf() -> None:
    assert normalise("ل\u06a9ن") == "لكن"


def test_hamza_forms_fold_to_bare_alef() -> None:
    """A question typed `استخرج` must match text set as `إستخرج`."""
    assert normalise("إستخرج") == normalise("استخرج") == "استخرج"


def test_tashkeel_is_removed() -> None:
    """Textbooks are vocalised; nobody types the marks into a chat box."""
    assert normalise("أثبتَ أنَّ الدَّالةَ مُحدودة") == "اثبت ان الدالة محدودة"


def test_tatweel_is_removed() -> None:
    assert normalise("المـــدة") == "المدة"


def test_ta_marbuta_is_preserved() -> None:
    """Folding ة to ه is common and wrong: in legal text `المدة` (the period)
    and `المده` are different words."""
    assert "\u0629" in normalise("المدة القانونية")


def test_alef_maqsura_is_preserved() -> None:
    assert "\u0649" in normalise("على")


def test_inline_maths_survives_untouched() -> None:
    """Stripping a mark from inside a formula corrupts it silently."""
    src = r"حد $\lim_{x \to +\infty}$ عند"
    assert r"$\lim_{x \to +\infty}$" in normalise(src)


def test_display_maths_survives_untouched() -> None:
    src = "وفق:\n$$g(x) = \frac{1}{3 + 2\sin x}$$\nأثبت"
    assert "$$g(x) = \frac{1}{3 + 2\sin x}$$" in normalise(src)


def test_document_and_query_spellings_meet() -> None:
    """The whole point: both sides are normalised with the same function."""
    document = "أثبتْ أنَّ التَّابعَ مُحدود"
    query = "اثبت ان التابع محدود"
    assert normalise(document) == normalise(query)


def test_whitespace_is_tidied_without_losing_paragraphs() -> None:
    assert normalise("سطر   أول\n\n\n\nسطر ثانٍ") == "سطر اول\n\nسطر ثان"
