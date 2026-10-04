"""Clean up Arabic text before it is chunked, embedded or stored.

Why this is not optional
------------------------
Two different spellings of the same word produce two different vectors, and a
query written one way then fails to find a passage written the other. Arabic
makes this easy to trip over, and OCR makes it worse:

* The OCR test on the real textbook returned `لیکن` - a **Persian** yeh (U+06CC)
  where Arabic uses ي (U+064A). Visually identical in most fonts, a different
  character to every search index in the world.
* Alef appears as ا أ إ آ. A user typing `استخرج` should still match text that
  was set as `إستخرج`.
* Diacritics (tashkeel) are common in textbooks and almost never typed in a
  question.
* Tatweel (ـ) stretches glyphs for justification and carries no meaning.

Normalising both sides - documents at ingestion, queries at search time - is
what makes them meet. The same function must be used for both, which is why it
lives here rather than inside either pipeline.

What is deliberately NOT done
-----------------------------
Ta marbuta (ة) is *not* folded to ه, and final yeh is not folded to alef
maqsura. Both are common in aggressive normalisation and both destroy meaning
in legal text, where `المدة` (the period) and `المده` are not the same word.
"""

from __future__ import annotations

import re
import unicodedata

# Characters that look Arabic but are not, mostly from Persian and Urdu
# keyboards - and from OCR models that were trained on all of them at once.
_CONFUSABLES = {
    "\u06cc": "\u064a",  # Persian yeh   ی -> ي
    "\u0649": "\u0649",  # alef maqsura  ى -> kept, it is meaningful
    "\u06a9": "\u0643",  # Persian kaf   ک -> ك
    "\u06af": "\u06af",  # gaf: not Arabic, but leave it rather than guess
    "\u064a\u0651": "\u064a\u0651",
}

_ALEF = {"\u0623": "\u0627", "\u0625": "\u0627", "\u0622": "\u0627"}  # أ إ آ -> ا

# U+064B..U+0652 are the tashkeel marks; U+0640 is tatweel.
_TASHKEEL = re.compile(r"[\u064b-\u0652\u0670]")
_TATWEEL = re.compile("\u0640+")
_WHITESPACE = re.compile(r"[ \t\u00a0]+")
_BLANK_LINES = re.compile(r"\n{3,}")

# LaTeX must survive untouched: $...$ and $$...$$ are mathematics, and
# stripping a diacritic-looking character out of a formula corrupts it.
_MATH = re.compile(r"(\$\$.*?\$\$|\$[^$\n]*?\$)", re.DOTALL)


# Some PDFs draw their text twice - a bold or shadow effect, or two
# overlapping text layers - and an extractor faithfully returns both copies
# glued together: the Syrian penal code yields `109109`,
# `المادةالمادة` and `www.parliament.gov.sywww.parliament.gov.sy`, 1597 of
# them across 121 pages. Left in, they break every pattern that expects a
# heading to look like a heading - the article matcher found exactly zero -
# and they double the weight of whatever was duplicated once it is embedded.
#
# Three characters minimum, and the repetition must be immediate and
# unbroken by whitespace, so an ordinary repeated word is left alone.
_DOUBLED = re.compile(r"(\S{3,}?)\1")


# A heading drawn twice does not always double into something `_DOUBLED`
# can see. The Syrian penal code renders `المادة 1` as `11المادةالمادة`:
# the word doubles into six characters and is caught, while the number
# doubles into `11` and is not, because one digit repeated is indvisible
# from the number eleven. Article 1 then reads as article 11 - a citation
# that is wrong in the one way a legal answer cannot afford.
#
# The adjacent word settles it. If the word beside the digits was doubled,
# the digits were too; if it was not, `11المادة` really is article eleven
# and is left alone.
_DOUBLED_HEADING = re.compile(
    r"(\d{1,8})([\u0600-\u06ff]{3,})\2"
)


def _undouble_heading(match: 're.Match[str]') -> str:
    digits, word = match.group(1), match.group(2)
    half = len(digits) // 2
    if half and digits[:half] == digits[half:]:
        digits = digits[:half]
    return digits + word


def _keep_one(match: 're.Match[str]') -> str:
    return match.group(1)


def _normalise_arabic_run(text: str) -> str:
    # NFKC, not NFC. Some PDFs store Arabic as *presentation forms* - the
    # shaped glyphs U+FE70..U+FEFF that a renderer would produce - rather than
    # as logical letters. The Syrian penal code is one: 1024 of its 1384
    # Arabic characters on a sample page were presentation forms. They look
    # identical and are different codepoints, so a question typed `يمكن`
    # would never match a document stored as `ﻳﻤﻜﻦ`, and the entire corpus
    # would be silently unsearchable. NFC leaves them alone; NFKC folds them.
    text = unicodedata.normalize("NFKC", text)
    for bad, good in _CONFUSABLES.items():
        text = text.replace(bad, good)
    for bad, good in _ALEF.items():
        text = text.replace(bad, good)
    # Before tashkeel removal: the two copies must still be identical.
    text = _DOUBLED_HEADING.sub(_undouble_heading, text)
    previous = None
    while previous != text:
        previous = text
        text = _DOUBLED.sub(_keep_one, text)
    text = _TASHKEEL.sub("", text)
    text = _TATWEEL.sub("", text)
    return text


def normalise(text: str) -> str:
    """Return `text` with Arabic spelling unified and layout tidied.

    Mathematics between `$` delimiters is passed through untouched.
    """
    parts = _MATH.split(text)
    # split() with a capturing group alternates: text, math, text, math, ...
    cleaned = [
        part if i % 2 else _normalise_arabic_run(part) for i, part in enumerate(parts)
    ]
    out = "".join(cleaned)
    out = _WHITESPACE.sub(" ", out)
    out = "\n".join(line.strip() for line in out.split("\n"))
    return _BLANK_LINES.sub("\n\n", out).strip()
