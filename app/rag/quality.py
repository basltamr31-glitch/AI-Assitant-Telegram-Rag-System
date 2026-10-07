"""Catch OCR output that is visibly wrong before it reaches the index.

A vision model under pressure does not fail cleanly. On this very book it
produced, in separate runs:

* `\u0644\u0644-follow` where the page said `\u0644\u0644\u062a\u0627\u0628\u0639` - an Arabic technical term
  silently translated into English.
* `\u0644\u90a3\u9ebc` where the page said `\u0644\u064a\u0643\u0646` - Chinese characters emitted
  mid-Arabic-sentence.

Neither raises an error, and neither is obvious in a 2000-character
transcription. Both would be embedded and indexed, and would then quietly fail
to match any query for the rest of the system's life. A page that is 3% Chinese
is not a page worth keeping.

The checks here are deliberately blunt: they look for scripts that cannot
belong in an Arabic maths or legal text at all. Latin letters are allowed,
because LaTeX commands and variable names are full of them.
"""

from __future__ import annotations

import re

# Scripts that have no business appearing in an Arabic textbook. Greek is
# excluded on purpose: alpha, beta and pi are ordinary mathematics.
_FOREIGN_SCRIPT = re.compile(
    "["
    "\u3040-\u30ff"  # hiragana, katakana
    "\u4e00-\u9fff"  # CJK ideographs
    "\uac00-\ud7af"  # hangul
    "\u0400-\u04ff"  # cyrillic
    "\u0900-\u097f"  # devanagari
    "\u0e00-\u0e7f"  # thai
    "\u05d0-\u05ea"  # hebrew
    "]"
)

# Arabic letters, so we can tell "a page of Arabic with a glitch" from "a page
# that is legitimately mostly formulas".
_ARABIC = re.compile(r"[\u0620-\u064a\u0671-\u06d3]")


def foreign_characters(text: str) -> list[str]:
    """Return the characters from scripts that cannot belong in this corpus."""
    return _FOREIGN_SCRIPT.findall(text)


# A sentence ends at Western or Arabic punctuation, or at a line break.
_SENTENCE = re.compile(r"[^.!?؟\n]*(?:[.!?؟]+|\n|$)")


def drop_foreign_sentences(text: str) -> str:
    """Remove the sentences written in a script this assistant never uses.

    For model replies, not OCR: a reply with one Chinese sentence in it is
    still worth sending without that sentence (see `responder.py`).
    """
    return "".join(
        s for s in _SENTENCE.findall(text) if not foreign_characters(s)
    ).strip()


def arabic_ratio(text: str) -> float:
    """Share of letter-ish characters that are Arabic."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return len(_ARABIC.findall("".join(letters))) / len(letters)


class QualityProblem(str):
    """A human-readable reason a transcription was rejected."""


def check_page(text: str, min_arabic_ratio: float = 0.10) -> QualityProblem | None:
    r"""Return a reason to reject this transcription, or None to accept it.

    `min_arabic_ratio` is low on purpose. A page of a maths textbook can be
    mostly LaTeX, and Latin letters inside `\frac` and `\lim` are not a
    problem. The threshold exists to catch a page that came back as English
    prose, which is a different failure from a page that is mostly formulas.
    """
    stripped = text.strip()
    if not stripped:
        return QualityProblem("empty transcription")

    foreign = foreign_characters(stripped)
    if foreign:
        sample = "".join(dict.fromkeys(foreign))[:12]
        return QualityProblem(
            f"{len(foreign)} character(s) from a foreign script: {sample!r}"
        )

    ratio = arabic_ratio(stripped)
    if ratio < min_arabic_ratio:
        return QualityProblem(
            f"only {ratio:.0%} of letters are Arabic - the page may have been "
            "translated rather than transcribed"
        )

    return None


# A model told not to add commentary adds commentary anyway. Observed on this
# corpus: "Here is the exact transcription of the text on the page:". Indexed
# as content, that sentence matches English queries and means nothing.
#
# Written without newline escapes on purpose: `.` does not match a newline
# unless DOTALL is set, so MULTILINE plus `$` matches exactly one line.
_PREAMBLE = re.compile(
    r"^[ ]*(here (is|are)|sure|certainly|okay|the (exact )?transcription"
    r"|below is|this (page|image) (contains|shows)).*:[ ]*$",
    re.IGNORECASE | re.MULTILINE,
)

# Some models fence the whole answer. The fence is markup, not content.
_FENCE = re.compile(r"^\s*```[a-zA-Z]*(.*?)```\s*$", re.DOTALL)


def strip_preamble(text: str) -> str:
    """Remove a leading "here is the transcription:" line and any code fence.

    Deliberately conservative: it only removes a line that both opens with a
    known English lead-in and ends in a colon. A page that genuinely begins
    with Arabic is never touched, because none of these patterns match Arabic.
    """
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1)
    previous = None
    while previous != text:
        previous = text
        text = _PREAMBLE.sub("", text, count=1)
    return text.strip()
