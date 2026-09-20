"""Turns an incoming message into a reply.

This module is the seam.
------------------------
Phase 4 answered with `if/else` and no intelligence, so that a wrong reply
could only mean a broken pipe. The pipes are proven, so Phase 5 puts Claude
behind it - and nothing else in the system changed. n8n does not know a model
exists; `main.py` still calls `respond()` and gets back text.

What did *not* arrive with the model
------------------------------------
No memory: every message is a fresh conversation, because there is nowhere to
store one until Phase 9. No documents: retrieval is Phase 8. The system prompt
says so plainly, so the assistant admits it rather than inventing a past.

Two kinds of reply
------------------
Commands are answered locally, without calling the model. `/ping` asking a
frontier model to say "pong" would cost real money to answer a question the
code already knows. Everything else goes to Claude.
"""

from __future__ import annotations

import html

from app.api.schemas import ChatRequest
from app.api.telegram_html import sanitise, truncate
from app.core.logging import get_logger
from app.llm.client import AnthropicClient
from app.llm.prompts import SYSTEM_PROMPT

log = get_logger(__name__)

WELCOME = (
    "👋 <b>Hello {name}!</b>\n\n"
    "Ask me anything and I will answer with Claude.\n\n"
    "I have <b>no memory</b> of earlier messages and <b>no access</b> to your "
    "documents yet — both are coming in later phases.\n\n"
    "Try <code>/help</code> for the commands I handle myself."
)

HELP = (
    "<b>Commands</b> — answered locally, no tokens spent\n"
    "<code>/start</code> — what I am\n"
    "<code>/help</code> — this list\n"
    "<code>/ping</code> — check I am awake\n"
    "<code>/whoami</code> — the ids I see for you\n\n"
    "<b>Anything else</b> goes to Claude.\n\n"
    "<i>No memory, no documents — yet.</i>"
)

MODEL_UNAVAILABLE = (
    "⚠️ <b>I cannot reach Claude.</b>\n\n"
    "<code>ANTHROPIC_API_KEY</code> is not set in <code>.env</code>. "
    "Commands still work — try <code>/help</code>."
)

MODEL_FAILED = (
    "⚠️ <b>Claude did not answer.</b>\n\n"
    "The error is in the server log, under this message's trace id. "
    "Try again in a moment."
)


def _local_reply(request: ChatRequest, text: str) -> tuple[str, str] | None:
    """Answer without the model, or return None to mean 'ask Claude'."""
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
    return None


async def respond(
    request: ChatRequest,
    llm: AnthropicClient | None,
) -> tuple[str, str]:
    """Return `(reply_html, handled_by)` for one message.

    `llm` is passed in rather than imported so that tests can supply a fake and
    never touch the network - and so a missing API key degrades to commands
    instead of taking the whole service down.
    """
    text = request.text.strip()

    local = _local_reply(request, text)
    if local is not None:
        return local

    if llm is None:
        return MODEL_UNAVAILABLE, "model.unavailable"

    try:
        result = await llm.complete(system=SYSTEM_PROMPT, user_message=text)
    except Exception:
        # Deliberately broad, deliberately minimal. Retries, timeouts and
        # fallbacks are Phase 13; today the only promise is that a failing
        # model produces a message rather than silence.
        log.exception("llm.call_failed")
        return MODEL_FAILED, "model.error"

    # Truncate before sanitising: cutting sanitised HTML could slice a tag in
    # half, whereas sanitising afterwards closes whatever the cut left open.
    reply = sanitise(truncate(result.text))

    if not reply.strip():
        # An empty completion is rare but not impossible, and an empty
        # sendMessage is an error from Telegram rather than a silent no-op.
        log.warning("llm.empty_reply", stop_reason=result.stop_reason)
        return "I did not manage to answer that. Try rephrasing?", "model.empty"

    return reply, "model"
