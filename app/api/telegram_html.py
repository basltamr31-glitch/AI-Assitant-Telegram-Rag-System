"""Make model output safe for Telegram's HTML parser.

Why this file exists
--------------------
Replies are sent with `parse_mode: HTML`, and Telegram does not accept general
HTML. It accepts a short allowlist - `b`, `i`, `u`, `s`, `a`, `code`, `pre`,
`blockquote`, `tg-spoiler` - and rejects the whole message with
`can't parse entities` if anything else appears, or if a tag is left unclosed.

A language model writes for humans, not for that parser. Ask it for HTML and
sooner or later it emits `<h2>`, an unclosed `<b>`, or a stray `<` in a code
snippet, and the user gets silence instead of an answer.

So we do not trust the output. We parse it, keep the tags Telegram allows,
escape everything else into text, and close whatever the model left open. The
worst case becomes "formatting looks plainer than intended" instead of "no
message at all".

This is the same principle as the escaping in `responder.py`, applied to a
harder case: there the untrusted text was the user's, here it is the model's.
Neither is trusted just because of where it came from.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

# Telegram's documented allowlist. `span` is omitted deliberately: it is only
# valid with class="tg-spoiler", and tg-spoiler covers that case already.
ALLOWED_TAGS = frozenset(
    {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
     "a", "code", "pre", "blockquote", "tg-spoiler"}
)

# Only these attributes survive, and only on these tags. Everything else is
# dropped - an `onclick` or a `style` has no business reaching a chat client.
ALLOWED_ATTRS = {"a": {"href"}, "code": {"class"}}

# Tags that carry no content and must never be emitted as a pair.
VOID_TAGS = frozenset({"br", "hr", "img", "input", "meta", "link"})


class _TelegramSanitiser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.open_tags: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in VOID_TAGS:
            # <br> has no Telegram equivalent; a newline is the honest
            # translation, and it keeps the text readable.
            if tag == "br":
                self.parts.append("\n")
            return
        if tag not in ALLOWED_TAGS:
            return  # drop the tag, keep whatever is inside it

        permitted = ALLOWED_ATTRS.get(tag, set())
        kept = "".join(
            f' {name}="{html.escape(value or "", quote=True)}"'
            for name, value in attrs
            if name in permitted
        )
        self.parts.append(f"<{tag}{kept}>")
        self.open_tags.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag not in ALLOWED_TAGS or tag not in self.open_tags:
            return
        # Close anything the model opened inside this tag and forgot about,
        # innermost first, so the result is still well nested.
        while self.open_tags:
            current = self.open_tags.pop()
            self.parts.append(f"</{current}>")
            if current == tag:
                break

    def handle_data(self, data: str) -> None:
        # Escaped, always. This is the step that makes a stray "<" harmless.
        self.parts.append(html.escape(data, quote=False))

    def result(self) -> str:
        # Whatever is still open at the end, close it.
        while self.open_tags:
            self.parts.append(f"</{self.open_tags.pop()}>")
        return "".join(self.parts)


# The prompt asks for HTML; the model writes Markdown anyway, and Telegram's
# HTML mode shows `**المادة 628**` with its asterisks. Only emphasis is
# translated - it is what the models actually use - and the italic form only
# where it cannot be multiplication: `*` opening after a space or line start,
# closing before a space, punctuation or line end, so `$a*b*c$` is left alone.
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_ITALIC = re.compile(r"(?<![^\s(])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![^\s.,:;!?؟،)])")


# Quoting an article, the model writes Markdown's `> ` - and Telegram showed
# `&gt;` in front of every line of Article 535. A run of such lines becomes
# one <blockquote>, which Telegram renders as a quote.
_QUOTE_LINE = re.compile(r"^>\s?(.*)$")


def _blockquotes(text: str) -> str:
    out: list[str] = []
    quote: list[str] = []
    for line in text.split("\n") + [""]:
        match = _QUOTE_LINE.match(line)
        if match:
            quote.append(match.group(1))
            continue
        if quote:
            out.append("<blockquote>" + "\n".join(quote).strip() + "</blockquote>")
            quote = []
        out.append(line)
    return "\n".join(out[:-1])


# Telegram has no tables. A model comparing three articles writes one anyway,
# and the reader got rows of pipes. Each row becomes one line - the header in
# bold - and the `|---|` separator row and `---` rules disappear.
_TABLE_ROW = re.compile(r"^\s*\|(.*)\|\s*$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?[\s:|-]*-{3,}[\s:|-]*\|?\s*$")


def _flatten_tables(text: str) -> str:
    out: list[str] = []
    header_done = False
    for line in text.split("\n"):
        if _TABLE_SEPARATOR.match(line):
            continue
        row = _TABLE_ROW.match(line)
        if not row:
            header_done = False
            out.append(line)
            continue
        cells = " — ".join(c.strip() for c in row.group(1).split("|") if c.strip())
        out.append(cells if header_done else f"<b>{cells}</b>")
        header_done = True
    return "\n".join(out)


def markdown_emphasis(text: str) -> str:
    """Turn Markdown quotes, tables, bold and italic into what Telegram renders."""
    text = _flatten_tables(_blockquotes(text))
    return _ITALIC.sub(r"<i>\1</i>", _BOLD.sub(r"<b>\1</b>", text))


def sanitise(text: str) -> str:
    """Return `text` with only Telegram-safe HTML left in it."""
    parser = _TelegramSanitiser()
    parser.feed(text)
    parser.close()
    return parser.result()


# Telegram rejects any message longer than this.
TELEGRAM_MAX_CHARS = 4096


def truncate(text: str, limit: int = TELEGRAM_MAX_CHARS) -> str:
    """Cut an over-long reply at a word boundary and say that it was cut.

    Sanitising first and truncating second would risk slicing through a tag,
    so callers must truncate *before* sanitising - `sanitise` then closes any
    tag the cut left open.
    """
    if len(text) <= limit:
        return text
    notice = "\n\n[...truncated]"
    cut = text[: limit - len(notice)]
    # Prefer the last space so a word is not chopped in half.
    space = cut.rfind(" ")
    if space > limit * 0.8:
        cut = cut[:space]
    return cut + notice
