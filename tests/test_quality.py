"""Tests for the OCR quality gate.

Every rejection case here is something the vision models actually produced
while reading `12-sci-math-1.pdf`, not an invented failure. That matters: the
gate exists because these outputs look plausible. A 2000-character
transcription with two Chinese characters in the middle of it passes every
casual glance, gets embedded, and then silently matches nothing forever.
"""

from __future__ import annotations

from app.rag.quality import (
    arabic_ratio,
    check_page,
    foreign_characters,
    strip_preamble,
)

GOOD = r"ليكن $g$ التابع المعرف على $\mathbb{R}$ وفق $g(x) = \frac{1}{3+2\sin x}$."


def test_a_good_page_is_accepted() -> None:
    assert check_page(GOOD) is None


def test_chinese_leak_is_rejected() -> None:
    """Observed: `ل那麼` where the page said `ليكن`."""
    problem = check_page("ل那麼 $g$ التابع المعرف على المجموعة")
    assert problem is not None
    assert "foreign script" in problem


def test_the_other_chinese_leak_is_rejected() -> None:
    """Observed six runs in a row on page 60: the word 结尾 appended."""
    assert check_page(GOOD + " 结尾") is not None


def test_a_translated_page_is_rejected() -> None:
    """A transcription that came back as English is not a transcription."""
    english = "Let g be the function defined on R by g(x) = 1/(3 + 2 sin x)."
    assert check_page(english) is not None


def test_an_empty_page_is_rejected() -> None:
    assert check_page("") is not None
    assert check_page("   \n  ") is not None


def test_a_formula_heavy_page_is_not_mistaken_for_english() -> None:
    r"""A page of exercises is mostly LaTeX, and \frac and \lim are Latin.

    The gate must not reject a page for containing mathematics. This string is
    taken from the real page 60 transcription, which measured 14% Arabic by
    letter - which is why the threshold sits below that.
    """
    heavy = (
        "ليكن $f$ وفق:\n"
        r"$$f(x) = \frac{3x^2 + 6x}{x^2 - x - 2} \quad "
        r"\lim_{x \to +\infty} \left( \frac{x + \sin x}{3 + 2\sin x} \right)$$"
    )
    assert check_page(heavy) is None


def test_greek_letters_are_allowed() -> None:
    """Alpha and pi are ordinary mathematics, not a foreign script."""
    assert foreign_characters(r"الزاوية $\alpha$ و $\pi$") == []


def test_arabic_ratio_counts_only_letters() -> None:
    assert arabic_ratio("ليكن") == 1.0
    assert arabic_ratio("abc") == 0.0
    assert arabic_ratio("$$123$$") == 0.0  # no letters at all


# --- preamble stripping ------------------------------------------------------


def test_the_observed_preamble_is_removed() -> None:
    """Observed verbatim, despite the prompt forbidding commentary."""
    src = "Here is the exact transcription of the text on the page:\n\n**10** ليكن $g$"
    assert strip_preamble(src) == "**10** ليكن $g$"


def test_a_code_fence_is_unwrapped() -> None:
    assert strip_preamble("```\nالخط البياني\n```") == "الخط البياني"


def test_arabic_text_is_never_touched() -> None:
    assert strip_preamble(GOOD) == GOOD


def test_an_arabic_line_ending_in_a_colon_is_kept() -> None:
    """The dangerous false positive: Arabic prose often ends in a colon."""
    src = "المستقيم الذي معادلته:\n$x = 3$"
    assert strip_preamble(src) == src


# --- telling a daily allowance from an ordinary rate limit -------------------


def test_a_daily_quota_is_recognised_from_the_body() -> None:
    """OpenRouter's wording, verbatim from a real 429."""
    import httpx

    from app.rag.ocr import _is_daily_quota

    response = httpx.Response(
        429,
        json={
            "error": {
                "message": "Rate limit exceeded: free-models-per-day",
                "metadata": {"limit_source": "openrouter_free_tier_daily"},
            }
        },
    )
    assert _is_daily_quota(response) is True


def test_a_daily_quota_is_recognised_from_the_header() -> None:
    import httpx

    from app.rag.ocr import _is_daily_quota

    response = httpx.Response(429, headers={"x-ratelimit-remaining": "0"}, json={})
    assert _is_daily_quota(response) is True


def test_an_ordinary_rate_limit_is_not_a_daily_quota() -> None:
    """"Slow down" is worth retrying; "come back tomorrow" is not."""
    import httpx

    from app.rag.ocr import _is_daily_quota

    response = httpx.Response(
        429,
        headers={"x-ratelimit-remaining": "17"},
        json={"error": {"message": "temporarily rate-limited upstream"}},
    )
    assert _is_daily_quota(response) is False


def test_a_body_that_is_not_json_does_not_crash_the_check() -> None:
    import httpx

    from app.rag.ocr import _is_daily_quota

    assert _is_daily_quota(httpx.Response(429, text="<html>502</html>")) is False
