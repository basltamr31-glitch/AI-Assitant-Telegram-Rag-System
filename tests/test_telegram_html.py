"""Tests for the Telegram HTML sanitiser.

Every case here is something a language model plausibly writes. The sanitiser
exists because Telegram rejects the *whole message* when it cannot parse the
entities - so each of these, unhandled, means the user gets silence.
"""

from __future__ import annotations

from app.api.telegram_html import (
    TELEGRAM_MAX_CHARS,
    markdown_emphasis,
    sanitise,
    truncate,
)


def test_allowed_tags_survive() -> None:
    assert sanitise("<b>bold</b> and <i>italic</i>") == "<b>bold</b> and <i>italic</i>"


def test_disallowed_tag_is_dropped_but_its_text_is_kept() -> None:
    """Losing the heading is fine. Losing the sentence is not."""
    assert sanitise("<h2>Title</h2> body") == "Title body"


def test_unclosed_tags_are_closed() -> None:
    assert sanitise("<b>never closed") == "<b>never closed</b>"


def test_crossed_tags_are_nested_correctly() -> None:
    """`<b>a<i>b</b>` is invalid; the output must still nest properly."""
    assert sanitise("<b>bold <i>both</b> rest") == "<b>bold <i>both</i></b> rest"


def test_bare_angle_brackets_are_escaped() -> None:
    """The classic: a model writing about code."""
    assert sanitise("if x < 3 and y > 4") == "if x &lt; 3 and y &gt; 4"


def test_dangerous_attributes_are_stripped() -> None:
    out = sanitise('<a href="https://x.com" onclick="evil()">link</a>')
    assert out == '<a href="https://x.com">link</a>'


def test_script_tag_cannot_survive() -> None:
    assert "<script" not in sanitise("<script>alert(1)</script>ok")


def test_br_becomes_a_newline() -> None:
    assert sanitise("line<br>break") == "line\nbreak"


def test_short_text_is_not_truncated() -> None:
    assert truncate("short") == "short"


def test_long_text_is_cut_and_marked() -> None:
    out = truncate("word " * 2000)
    assert len(out) <= TELEGRAM_MAX_CHARS
    assert out.endswith("[...truncated]")


def test_truncation_then_sanitising_leaves_valid_html() -> None:
    """The documented order: cut first, then let the sanitiser close tags."""
    out = sanitise(truncate("<b>" + "word " * 2000))
    assert out.endswith("</b>")


def test_markdown_bold_becomes_html() -> None:
    """The model writes `**...**` even when asked for HTML."""
    assert markdown_emphasis("- **المادة 628**: الحبس") == "- <b>المادة 628</b>: الحبس"


def test_markdown_italic_becomes_html() -> None:
    assert markdown_emphasis("*ملاحظة: ليست مشورة قانونية.*") == (
        "<i>ملاحظة: ليست مشورة قانونية.</i>"
    )


def test_multiplication_is_not_mistaken_for_italic() -> None:
    assert markdown_emphasis("$a*b*c$ و 2*3*4") == "$a*b*c$ و 2*3*4"


def test_converted_emphasis_survives_sanitising() -> None:
    assert sanitise(markdown_emphasis("**عقوبة** السرقة")) == "<b>عقوبة</b> السرقة"


def test_markdown_quote_lines_become_one_blockquote() -> None:
    """Article 535 arrived with `&gt;` in front of every line."""
    text = "المادة 535:\n\n> يعاقب بالاعدام على القتل قصدا اذا ارتكب:\n> \n> 1. عمدا.\n\nتعليق."
    assert markdown_emphasis(text) == (
        "المادة 535:\n\n<blockquote>يعاقب بالاعدام على القتل قصدا اذا ارتكب:\n\n"
        "1. عمدا.</blockquote>\n\nتعليق."
    )


def test_a_quote_at_the_end_is_closed() -> None:
    assert markdown_emphasis("نص\n> اقتباس") == "نص\n<blockquote>اقتباس</blockquote>"


def test_a_greater_than_inside_a_line_is_not_a_quote() -> None:
    assert markdown_emphasis("اذا كان x > 3") == "اذا كان x > 3"


def test_a_markdown_table_becomes_one_line_per_row() -> None:
    """Telegram has no tables; the reader got rows of pipes."""
    table = (
        "مقارنة:\n"
        "| الظرف | المادة | العقوبة |\n"
        "|-------|--------|---------|\n"
        "| سلاح فقط | 628 | سنة حبس |\n"
        "| سلاح وعنف | 624 | 5 سنوات |\n"
        "\n---\n\nالخلاصة."
    )
    assert markdown_emphasis(table) == (
        "مقارنة:\n"
        "<b>الظرف — المادة — العقوبة</b>\n"
        "سلاح فقط — 628 — سنة حبس\n"
        "سلاح وعنف — 624 — 5 سنوات\n"
        "\n\nالخلاصة."
    )
