"""Tests for the Telegram HTML sanitiser.

Every case here is something a language model plausibly writes. The sanitiser
exists because Telegram rejects the *whole message* when it cannot parse the
entities - so each of these, unhandled, means the user gets silence.
"""

from __future__ import annotations

from app.api.telegram_html import TELEGRAM_MAX_CHARS, sanitise, truncate


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
