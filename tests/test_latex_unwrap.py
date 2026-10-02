r"""Tests for recovering prose from over-eager LaTeX.

The input in `test_the_real_fallback_output_is_unwrapped` is copied from what
a fallback model actually returned for page 60 of `12-sci-math-1.pdf` after
the first model in the chain had been exhausted. It is valid LaTeX and it is
useless as a chunk: every Arabic word sits inside `\text{}` and the sentence
is cut apart by table cells.
"""

from __future__ import annotations

from app.rag.latex_unwrap import unwrap

REAL_FALLBACK = (
    "\\begin{tabular}{rll}\n"
    "\\textbf{10} & \\text{ليكن } g \\text{ التابع المعرف على } \\mathbb{R} "
    "\\text{ وفق } g(x) = \\dfrac{1}{3 + 2\\sin x} \\\\\n"
    "& \\text{اثبت ان } g \\text{ محدود.} \\\\\n"
    "\\end{tabular}"
)

CLEAN = r"ليكن $g$ التابع المعرف على $\mathbb{R}$ وفق $g(x)=\frac{1}{3+2\sin x}$."


def test_the_real_fallback_output_is_unwrapped() -> None:
    out = unwrap(REAL_FALLBACK)
    assert "\\begin{tabular}" not in out
    assert "\\text{" not in out
    assert "ليكن" in out
    assert "اثبت ان" in out
    # The row break became a line, not a stray backslash.
    assert "\\\\" not in out


def test_a_clean_page_is_untouched() -> None:
    """The common case must cost nothing and change nothing."""
    assert unwrap(CLEAN) == CLEAN


def test_a_real_display_formula_survives() -> None:
    src = "وفق:\n$$f(x) = \\frac{3x^2}{x-2}$$\nاثبت"
    assert "$$f(x) = \\frac{3x^2}{x-2}$$" in unwrap(src)


def test_nested_text_commands_are_unwrapped() -> None:
    assert unwrap("\\textbf{\\text{عنوان}}") == "عنوان"


def test_cell_separators_become_spaces_not_ampersands() -> None:
    out = unwrap("\\begin{tabular}{ll}\\text{أ} & \\text{ب}\\end{tabular}")
    assert "&" not in out
    assert "أ" in out and "ب" in out


def test_an_escaped_ampersand_is_not_treated_as_a_separator() -> None:
    """`\\&` is a literal ampersand in the text, not a column break."""
    assert "\\&" in unwrap("\\text{الشركة \\& شركاه}") or "&" in unwrap(
        "\\text{الشركة \\& شركاه}"
    )


def test_prose_wrongly_marked_as_display_maths_loses_the_delimiters() -> None:
    """`$$ليكن التابع محدودا$$` has no mathematics in it at all."""
    assert unwrap("\\text{x}$$ليكن التابع محدودا$$") == "x ليكن التابع محدودا".replace(
        " ", " "
    ) or "$$" not in unwrap("\\text{x}$$ليكن التابع محدودا$$")


def test_text_with_no_latex_at_all_is_returned_as_is() -> None:
    plain = "ليكن التابع محدودا على المجال"
    assert unwrap(plain) == plain
