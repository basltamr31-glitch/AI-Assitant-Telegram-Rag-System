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


# --- damage specific to extracted PDF text ----------------------------------


def test_presentation_forms_become_standard_arabic() -> None:
    """Some PDFs store shaped glyphs (U+FE70..U+FEFF) instead of letters.

    1024 of 1384 Arabic characters on a page of the Syrian penal code were
    these. They look identical and are different codepoints, so without this
    the whole corpus is silently unsearchable.
    """
    shaped = "\ufef3\ufee3\ufedc\ufee6"  # ﻳﻤﻜﻦ
    assert normalise(shaped) == "يمكن"


def test_text_drawn_twice_is_collapsed() -> None:
    """A bold or shadow effect makes an extractor return both copies."""
    assert normalise("www.example.comwww.example.com") == "www.example.com"


def test_a_doubled_heading_keeps_its_real_number() -> None:
    """`المادة 1` renders as `11المادةالمادة`: the word doubles into six
    characters and is caught, the number doubles into `11` and is not.
    Article 1 then reads as article 11 - wrong in the one way a legal
    citation cannot be."""
    assert normalise("11المادةالمادة") == "1المادة"
    assert normalise("109109المادةالمادة") == "109المادة"


def test_a_genuine_article_eleven_is_left_alone() -> None:
    """The adjacent word is the evidence. Undoubled word, undoubled number."""
    assert normalise("11المادة") == "11المادة"


def test_a_repeat_separated_by_a_space_is_not_collapsed() -> None:
    assert normalise("ثلاثة ثلاثة") == "ثلاثة ثلاثة"
