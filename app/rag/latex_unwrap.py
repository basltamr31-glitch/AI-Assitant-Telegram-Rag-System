r"""Recover Arabic prose that a model buried inside LaTeX.

The problem
-----------
Told to write mathematics in LaTeX, some models write *everything* in LaTeX.
One fallback model returned page 60 of the maths book like this:

    \begin{tabular}{rll}
    \textbf{10} & \text{ليكن } g \text{ التابع المعرف على } \mathbb{R} \\
    & \text{اثبت ان } g \text{ محدود.} \\
    \end{tabular}

It is valid LaTeX and it is useless to us. Every Arabic word is wrapped in
`\text{}`, the sentence is cut into fragments by table cells, and a chunk made
from it would be mostly markup. Embedding that gives a vector for the notation
rather than for the meaning.

The fix is to unwrap: pull the Arabic back out of `\text{}`, drop the table
scaffolding, and leave real formulas alone. What comes out is prose with
mathematics in it, which is what the page actually was.

Why not simply reject these pages
---------------------------------
Because the content is correct - only its packaging is wrong - and a rejected
page costs another model, another minute, and sometimes a gap in the book.
Unwrapping is cheap and lossless.
"""

from __future__ import annotations

import re

# Whole-page environments a model reaches for when it decides the page is a
# table. Their delimiters carry no meaning for us; the rows do.
_ENVIRONMENTS = ("tabular", "aligned", "align", "array", "matrix", "cases")

_BEGIN_END = re.compile(
    r"\\(?:begin|end)\{(?:" + "|".join(_ENVIRONMENTS) + r")\*?\}(?:\{[^}]*\})?",
    re.IGNORECASE,
)

# Column separators and row breaks inside those environments.
_ROW_BREAK = re.compile(r"\\\\")
_CELL_SEP = re.compile(r"(?<!\\)&")

# Formatting commands whose argument is ordinary text, not mathematics.
_TEXT_COMMANDS = re.compile(
    r"\\(?:text|textbf|textit|mbox|textrm|mathrm)\s*\{([^{}]*)\}"
)

# A `$$ ... $$` block that turned out to contain no mathematics at all, only
# unwrapped prose. Leaving the delimiters would mark Arabic as a formula.
_EMPTY_MATH = re.compile(r"\$\$\s*([^$]*?)\s*\$\$", re.DOTALL)

_MATH_HINT = re.compile(r"\\[a-zA-Z]+|[=+\-*/^_<>]|\d")


def _looks_like_prose(fragment: str) -> bool:
    """True when a `$$...$$` block holds words rather than mathematics."""
    return not _MATH_HINT.search(fragment)


def unwrap(text: str) -> str:
    """Return `text` with LaTeX packaging removed and Arabic prose restored."""
    if "\\begin{" not in text and "\\text" not in text:
        return text  # the common case: nothing to do

    out = _BEGIN_END.sub("", text)
    out = _ROW_BREAK.sub("\n", out)
    out = _CELL_SEP.sub(" ", out)

    # Repeated, because `\textbf{\text{...}}` nests.
    previous = None
    while previous != out:
        previous = out
        out = _TEXT_COMMANDS.sub(r"\1", out)

    out = _EMPTY_MATH.sub(
        lambda m: m.group(1) if _looks_like_prose(m.group(1)) else m.group(0), out
    )

    # Unwrapping leaves ragged whitespace where the scaffolding used to be.
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = "\n".join(line.strip() for line in out.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", out).strip()
