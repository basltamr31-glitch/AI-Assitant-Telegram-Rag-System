"""Turns an incoming message into a reply.

This module is the seam.
------------------------
Right now it answers with fixed rules and no intelligence whatsoever. That is
the entire point of Phase 4: prove the plumbing - Telegram, n8n, HTTP, auth,
logging - works end to end while the answer is something you can predict
exactly. When a reply comes back wrong today, the bug is in the pipes, and
there is nowhere else for it to hide.

Phase 5 replaces `respond()` with a call to Claude. Phase 8 adds retrieval in
front of it, Phase 10 an agent loop around it. Every one of those is a change
to this file alone, because nothing above it knows how a reply is produced.

Escaping note
-------------
Replies are sent with `parse_mode: HTML`, so Telegram parses the text as
markup. Any user-supplied substring we echo must be escaped first, or a
message containing `<b>` either breaks the send or injects formatting. We
escape here, at the point where the value enters an HTML context - not at the
door, because "clean the input once" is the strategy that fails the moment the
same value is later used somewhere with different rules.
"""

from __future__ import annotations

import html

from app.api.schemas import ChatRequest

WELCOME = (
    "👋 <b>Hello {name}!</b>\n\n"
    "I am alive, and right now I am deliberately not very smart.\n"
    "Your message travelled: Telegram → n8n → Python → n8n → Telegram.\n\n"
    "Try <code>/help</code> to see what I can do so far."
)

HELP = (
    "<b>Commands</b>\n"
    "<code>/start</code> — what I am\n"
    "<code>/help</code> — this list\n"
    "<code>/ping</code> — check I am awake\n"
    "<code>/whoami</code> — the ids I see for you\n\n"
    "Anything else is echoed back with its length.\n\n"
    "<i>No AI yet — that is Phase 5.</i>"
)


def respond(request: ChatRequest) -> tuple[str, str]:
    """Return `(reply_html, handled_by)` for one message.

    `handled_by` names the branch that produced the answer. It is returned to
    n8n and written to the logs, so "why did it say that?" is a lookup rather
    than an investigation.
    """
    text = request.text.strip()
    name = html.escape(request.first_name) or "there"

    if text.startswith("/start"):
        return WELCOME.format(name=name), "command.start"

    if text.startswith("/help"):
        return HELP, "command.help"

    if text.startswith("/ping"):
        return "🏓 <b>pong</b> — the Python service answered this.", "command.ping"

    if text.startswith("/whoami"):
        return (
            f"<b>Name:</b> {name}\n"
            f"<b>User ID:</b> <code>{request.user_id}</code>\n"
            f"<b>Chat ID:</b> <code>{request.chat_id}</code>\n\n"
            "<i>Your user ID is on the allowlist — that is why this worked.</i>"
        ), "command.whoami"

    if not text:
        return (
            "I only understand text so far. Photos, voice notes and stickers "
            "come later.",
            "empty",
        )

    return (
        "🐍 <b>Python here.</b>\n\n"
        f"<blockquote>{html.escape(text)}</blockquote>\n"
        f"<b>Characters:</b> {len(text)}\n"
        f"<b>Words:</b> {len(text.split())}",
        "echo",
    )
