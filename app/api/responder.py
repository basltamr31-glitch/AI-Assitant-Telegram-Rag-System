"""Turns an incoming message into a reply.

This module is the seam.
------------------------
Phase 4 answered with `if/else`, so a wrong reply could only mean a broken
pipe. Phase 5 put a model behind it. Phase 8 puts the user's own documents in
front of the model, and that is the change that matters: the assistant stops
answering from what it happens to remember and starts answering from what can
be shown.

Why the retrieval is unconditional here
---------------------------------------
Every message that is not a command is searched for, every time. That is
ADR-005's staging: Phase 8 ships a hardcoded RAG workflow and Phase 10 adds
the agent that *decides* whether to search. It has a visible cost - "مرحبا"
retrieves nothing and gets told so - and that cost is the argument for Phase
10. Building the decision first would mean debugging retrieval quality and
tool selection at the same time, with no known-good baseline for either.

Why nothing found means nothing said
------------------------------------
Phase 5 produced a fluent, confident, entirely wrong explanation of Syrian
contract law from parametric memory alone. Falling back to that behaviour
whenever retrieval comes up empty would make the knowledge base decorative:
the system would look grounded exactly when it was, and invent silently the
rest of the time. So an empty retrieval produces a refusal, not an answer.
"""

from __future__ import annotations

import html
from dataclasses import replace
import re
from typing import Protocol

from app.api.schemas import ChatRequest
from app.api.telegram_html import markdown_emphasis, sanitise, truncate
from app.core.logging import get_logger
from app.llm.base import LLMResult
from app.llm.prompts import (
    KNOWLEDGE_BASE_UNAVAILABLE,
    NOTHING_FOUND,
    SYSTEM_PROMPT,
    build_grounded_prompt,
)
from app.rag.quality import foreign_characters
from app.rag.retrieval import Retrieved

log = get_logger(__name__)

WELCOME = (
    "👋 <b>Hello {name}!</b>\n\n"
    "اسألني عن مادتك وسأجيب <b>من مستنداتك أنت</b>، مع الإشارة إلى المصدر.\n\n"
    "إن لم أجد ما يدعم الإجابة، سأقول ذلك بدل أن أخمّن.\n\n"
    "جرّب <code>/help</code> للأوامر."
)

HELP = (
    "<b>Commands</b> — answered locally, no tokens spent\n"
    "<code>/start</code> — what I am\n"
    "<code>/help</code> — this list\n"
    "<code>/ping</code> — check I am awake\n"
    "<code>/whoami</code> — the ids I see for you\n\n"
    "<b>أي سؤال آخر</b> يُبحث عنه في مستنداتك أولاً.\n\n"
    "<i>لا ذاكرة للمحادثة بعد — كل رسالة مستقلة.</i>"
)

MODEL_UNAVAILABLE = (
    "⚠️ <b>I cannot reach the model.</b>\n\n"
    "No provider is configured: set <code>LLM_PROVIDER</code> and its key — "
    "<code>OPENROUTER_API_KEY</code> or <code>ANTHROPIC_API_KEY</code> — in "
    "<code>.env</code>.\n\n"
    "Commands still work — try <code>/help</code>."
)

MODEL_FAILED = (
    "⚠️ <b>The model did not answer.</b>\n\n"
    "The error is in the server log, under this message's trace id. "
    "Try again in a moment."
)


class LLMProtocol(Protocol):
    """What the responder needs from a model - and nothing more.

    A Protocol rather than a base class: neither provider has to import this
    or inherit from it, and the fake in the tests satisfies it for free. The
    responder cannot name a vendor even by accident.
    """

    async def complete(
        self, system: str, user_message: str, max_tokens: int = ...
    ) -> LLMResult: ...


class RetrieverProtocol(Protocol):
    """Just enough of `Retriever` to stand a fake in front of."""

    def retrieve(self, query: str, **kwargs) -> Retrieved: ...


def _local_reply(request: ChatRequest, text: str) -> tuple[str, str] | None:
    """Answer without the model, or return None to mean 'go and look'."""
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


def _finish(reply: str, handled_by: str, stop_reason: str | None = None) -> tuple[str, str]:
    """Truncate, sanitise, and refuse to send an empty message.

    Truncation comes before sanitising: cutting sanitised HTML could slice a
    tag in half, whereas sanitising afterwards closes whatever the cut left
    open. Telegram rejects an empty `sendMessage`, so an empty completion has
    to become something.
    """
    cleaned = sanitise(truncate(markdown_emphasis(reply)))
    if not cleaned.strip():
        log.warning("llm.empty_reply", stop_reason=stop_reason)
        return "I did not manage to answer that. Try rephrasing?", "model.empty"
    return cleaned, handled_by


# A sentence ends at Western or Arabic punctuation, or at a line break.
_SENTENCE = re.compile(r"[^.!?؟\n]*(?:[.!?؟]+|\n|$)")


def _drop_foreign_sentences(text: str) -> str:
    """Remove the sentences written in a script this assistant never uses."""
    return "".join(
        s for s in _SENTENCE.findall(text) if not foreign_characters(s)
    ).strip()


async def _complete_in_script(llm: LLMProtocol, system: str, text: str) -> LLMResult:
    """Ask the model, and refuse to pass on an answer that drifted language.

    The free model sometimes switches mid-answer: a closing sentence in
    Chinese about Article 20, `पूर्व planning` inside an Arabic explanation of
    homicide. A prompt telling it not to is a suggestion; this is the check.
    One retry, because the drift is random rather than systematic. If the
    retry drifts too, the offending sentences are dropped - an answer missing
    a sentence is still readable, and a sentence in Chinese is not.
    """
    result = await llm.complete(system=system, user_message=text)
    if not foreign_characters(result.text):
        return result

    log.warning("llm.foreign_script", attempt=1)
    result = await llm.complete(system=system, user_message=text)
    if not foreign_characters(result.text):
        return result

    log.warning("llm.foreign_script", attempt=2, action="dropped_sentences")
    return replace(result, text=_drop_foreign_sentences(result.text))


async def respond(
    request: ChatRequest,
    llm: LLMProtocol | None,
    retriever: RetrieverProtocol | None = None,
    *,
    grounded: bool = True,
) -> tuple[str, str, list[str]]:
    """Return `(reply_html, handled_by, citations)` for one message.

    Dependencies are passed in rather than imported so that tests can supply
    fakes and never touch the network, and so a missing key or an unavailable
    knowledge base degrades to something honest instead of taking the service
    down.
    """
    text = request.text.strip()

    local = _local_reply(request, text)
    if local is not None:
        return (*local, [])

    if llm is None:
        return MODEL_UNAVAILABLE, "model.unavailable", []

    # `grounded=False` is an operator's explicit decision to run without a
    # knowledge base, which is honest. A retriever that is merely *missing*
    # while grounding is on is a fault, and faults do not get to fall back to
    # inventing - that is the behaviour this phase exists to remove.
    if not grounded:
        try:
            result = await _complete_in_script(llm, SYSTEM_PROMPT, text)
        except Exception:
            log.exception("llm.call_failed")
            return MODEL_FAILED, "model.error", []
        reply, handled_by = _finish(result.text, "model", result.stop_reason)
        return reply, handled_by, []

    if retriever is None:
        log.warning("retrieval.unavailable")
        return KNOWLEDGE_BASE_UNAVAILABLE, "retrieval.unavailable", []

    try:
        found = retriever.retrieve(text)
    except Exception:
        log.exception("retrieval.failed")
        return KNOWLEDGE_BASE_UNAVAILABLE, "retrieval.error", []

    if not found.found:
        # Deliberate: no passages means no answer. Answering anyway is the
        # failure this whole phase exists to prevent.
        log.info(
            "retrieval.nothing_found",
            best_rejected=found.best_rejected_score,
            threshold=found.threshold,
        )
        return NOTHING_FOUND, "retrieval.empty", []

    system = build_grounded_prompt(found.as_context())
    try:
        result = await _complete_in_script(llm, system, text)
    except Exception:
        # Deliberately broad, deliberately minimal. Retries, timeouts and
        # fallbacks are Phase 13; today the only promise is that a failing
        # model produces a message rather than silence.
        log.exception("llm.call_failed")
        return MODEL_FAILED, "model.error", []

    citations = found.citations()
    # A bare "[1]" in the answer tells the reader nothing. Appending what the
    # numbers refer to is what turns a citation into something checkable, and
    # checkability is the entire reason for retrieving at all. Only the
    # passages the model actually cited are listed, so a reader is not sent to
    # look up material the answer never used.
    cited = [
        f"[{n}] {c}" for n, c in enumerate(citations, 1) if f"[{n}]" in result.text
    ]
    body = result.text
    if cited:
        body += "\n\n<i>المصادر:</i>\n" + "\n".join(cited)

    reply, handled_by = _finish(body, "model.grounded", result.stop_reason)
    if handled_by != "model.grounded":
        citations = []

    log.info(
        "chat.grounded_reply",
        passages=len(found.results),
        best_score=round(found.results[0].score, 3),
        citations=len(citations),
    )
    return reply, handled_by, citations
