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
import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Protocol

from app.agent.loop import run_agent
from app.api.schemas import ChatRequest
from app.api.telegram_html import markdown_emphasis, sanitise, truncate
from app.core.logging import get_logger
from app.llm.base import LLMResult, Message
from app.llm.prompts import (
    KNOWLEDGE_BASE_UNAVAILABLE,
    NOTHING_FOUND,
    SYSTEM_PROMPT,
    build_grounded_prompt,
)
from app.rag.quality import drop_foreign_sentences, foreign_characters
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
    "<code>/whoami</code> — the ids I see for you\n"
    "<code>/reset</code> — forget this conversation and start fresh\n\n"
    "<b>أي سؤال آخر</b> يُبحث عنه في مستنداتك أولاً.\n\n"
    "<i>أتذكّر آخر رسائل هذه المحادثة، فيمكنك أن تسأل سؤال متابعة.</i>"
)

RESET_DONE = "🧹 <b>بدأنا محادثة جديدة.</b>\n\nنسيت ما سبق في هذه المحادثة."

RESET_UNAVAILABLE = (
    "⚠️ <b>الذاكرة غير متاحة الآن</b>، فلا شيء لأنساه.\n\n"
    "كل رسالة تُعامل كسؤال جديد إلى أن تعود."
)

MODEL_UNAVAILABLE = (
    "⚠️ <b>I cannot reach the model.</b>\n\n"
    "No provider is configured: set <code>LLM_PROVIDER</code> and its key — "
    "<code>OPENROUTER_API_KEY</code> or <code>ANTHROPIC_API_KEY</code> — in "
    "<code>.env</code>.\n\n"
    "Commands still work — try <code>/help</code>."
)

RATE_LIMITED = (
    "⏳ <b>رسائل كثيرة في وقت قصير.</b>\n\n"
    "أتوقف قليلاً كي لا تنفد حصة اليوم من الطلبات. "
    "حاول بعد نحو {minutes} دقيقة."
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
        self,
        system: str,
        user_message: str,
        max_tokens: int = ...,
        history: Sequence[Message] = ...,
    ) -> LLMResult: ...


class RetrieverProtocol(Protocol):
    """Just enough of `Retriever` to stand a fake in front of."""

    def retrieve(self, query: str, **kwargs) -> Retrieved: ...


class MemoryProtocol(Protocol):
    """Just enough of `ConversationStore` to stand a fake in front of."""

    async def recent(self, chat_id: int, limit: int) -> list[Message]: ...

    async def append(self, chat_id: int, user_id: int, turns: list[Message]) -> None: ...

    async def clear(self, chat_id: int) -> int: ...


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


CUT_OFF = "\n\n<i>⚠️ انقطع الجواب قبل أن يكتمل. اسأل عن جزء محدد منه.</i>"


def _visible_answer(result: LLMResult) -> str:
    """The answer, marked as incomplete when the model ran out of tokens.

    A legal answer that stops mid-list reads as finished: the reader takes
    the articles shown for all of them. Saying it was cut is the difference
    between a partial answer and a misleading one.
    """
    if result.stop_reason in ("length", "max_tokens"):
        log.warning("llm.reply_cut_off", output_tokens=result.output_tokens)
        return result.text + CUT_OFF
    return result.text


async def _complete_in_script(
    llm: LLMProtocol, system: str, text: str, history: Sequence[Message] = ()
) -> LLMResult:
    """Ask the model, and refuse to pass on an answer that drifted language.

    The free model sometimes switches mid-answer: a closing sentence in
    Chinese about Article 20, `पूर्व planning` inside an Arabic explanation of
    homicide. A prompt telling it not to is a suggestion; this is the check.
    One retry, because the drift is random rather than systematic. If the
    retry drifts too, the offending sentences are dropped - an answer missing
    a sentence is still readable, and a sentence in Chinese is not.
    """
    result = await llm.complete(system=system, user_message=text, history=history)
    if not foreign_characters(result.text):
        return result

    log.warning("llm.foreign_script", attempt=1)
    result = await llm.complete(system=system, user_message=text, history=history)
    if not foreign_characters(result.text):
        return result

    log.warning("llm.foreign_script", attempt=2, action="dropped_sentences")
    return replace(result, text=drop_foreign_sentences(result.text))


# --- Phase 9: memory ----------------------------------------------------------

# A refusal is stored as what it meant, not as its HTML: the model reading
# the history later needs to know nothing was found, not how it was styled.
REMEMBERED_REFUSAL = "لم أجد ما يجيب عن هذا السؤال في المواد المتاحة."

# `[2]` in an earlier answer pointed at that turn's passages. Left in, it
# would point at whatever happens to be passage 2 this time - a citation that
# looks valid and is wrong.
_CITATION = re.compile(r"\s*\[\d+\]")


def _fit_history(history: list[Message], max_chars: int) -> list[Message]:
    """Keep the newest turns that fit the budget, starting on a user turn.

    Oldest turns go first: what the user is following up on is almost always
    the last exchange, not the first. The Messages API also rejects a history
    that opens with an assistant turn, so a leading answer whose question was
    trimmed away goes with it.
    """
    kept: list[Message] = []
    used = 0
    for message in reversed(history):
        used += len(message.content)
        if used > max_chars:
            break
        kept.append(message)
    kept.reverse()
    while kept and kept[0].role != "user":
        kept.pop(0)
    return kept


def _previous_question(history: Sequence[Message]) -> str | None:
    """The question a follow-up is most likely following up on.

    "وإذا كان مسلحاً؟" can lose the word that matters - theft - to the
    previous question, so retrieval is given that question as context and
    searches with and without it (see `Retriever.retrieve`). ADR-017 records
    why this beat asking the model to rewrite the question first.
    """
    return next((m.content for m in reversed(history) if m.role == "user"), None)


async def _load_history(
    memory: MemoryProtocol | None, chat_id: int, limit: int, max_chars: int
) -> list[Message]:
    """The recent conversation, or none if memory is off or unreachable.

    A Postgres that is down costs the follow-up its context, not the user
    their answer: the question is still answered, as a fresh one.
    """
    if memory is None:
        return []
    try:
        history = await memory.recent(chat_id, limit)
    except Exception:
        log.exception("memory.load_failed")
        return []
    return _fit_history(history, max_chars)


async def _remember(
    memory: MemoryProtocol | None, request: ChatRequest, question: str, answer: str
) -> None:
    if memory is None:
        return
    turns = [
        Message(role="user", content=question),
        Message(role="assistant", content=_CITATION.sub("", answer).strip()),
    ]
    try:
        await memory.append(request.chat_id, request.user_id, turns)
    except Exception:
        # The reply is already written; failing to store it must not stop
        # it from being sent.
        log.exception("memory.store_failed")


async def _reset(memory: MemoryProtocol | None, chat_id: int) -> tuple[str, str]:
    if memory is None:
        return RESET_UNAVAILABLE, "command.reset"
    try:
        deleted = await memory.clear(chat_id)
    except Exception:
        log.exception("memory.clear_failed")
        return RESET_UNAVAILABLE, "command.reset"
    log.info("memory.cleared", messages=deleted)
    return RESET_DONE, "command.reset"


# --- Phase 10: the agent ------------------------------------------------------

_CITED = re.compile(r"\[(\d+)\]")
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_TAG = re.compile(r"<[^>]+>")


def _articles_named(answer: str, passages: list) -> list[int]:
    """Passages the answer cites by article number rather than by `[n]`.

    The model sometimes writes "المادة 628/ب تقضي..." with no `[n]` at all,
    and the reader got no sources. An article named in the answer *and*
    present in the retrieved passages is a citation that can be checked, so
    it is listed. The number has to follow the word مادة - a bare "628"
    could be a sum of money - and only retrieved passages qualify, so a
    number the model remembered from elsewhere is never given a source.
    """
    plain = _TAG.sub("", answer).translate(_ARABIC_DIGITS)
    named = []
    for i, passage in enumerate(passages, start=1):
        number = passage.label.removeprefix("المادة ").strip()
        if number.isdigit() and re.search(rf"ماد[ةت]\D{{0,15}}(?<!\d){number}(?!\d)", plain):
            named.append(i)
    return named


async def _respond_with_agent(
    request: ChatRequest,
    llm,
    retriever,
    text: str,
    history: list[Message],
    memory: MemoryProtocol | None,
    max_rounds: int,
) -> tuple[str, str, list[str]]:
    """Let the model decide whether to look; enforce what it found.

    Two rules live here rather than in the loop, because they are about what
    reaches the user. Nothing found is a refusal, as in Phase 8 - the model's
    text is discarded, however plausible. And a citation must point at a
    passage that was actually retrieved: `[7]` when four passages came back
    is removed, not shown as if it could be checked.
    """
    try:
        outcome = await run_agent(
            llm,
            retriever,
            text,
            history,
            previous_question=_previous_question(history),
            max_rounds=max_rounds,
        )
    except Exception:
        log.exception("agent.failed")
        return MODEL_FAILED, "model.error", []

    result, evidence = outcome.result, outcome.evidence

    if evidence.searched and not evidence.passages:
        log.info("agent.nothing_found", rounds=outcome.rounds)
        await _remember(memory, request, text, REMEMBERED_REFUSAL)
        return NOTHING_FOUND, "agent.nothing_found", []

    if not evidence.searched:
        reply, handled_by = _finish(_visible_answer(result), "agent.direct", result.stop_reason)
        if handled_by == "agent.direct":
            await _remember(memory, request, text, result.text)
        return reply, handled_by, []

    citations = evidence.citations()
    invalid: list[str] = []

    def keep_if_real(match: re.Match[str]) -> str:
        if 1 <= int(match.group(1)) <= len(citations):
            return match.group(0)
        invalid.append(match.group(0))
        return ""

    answer = _CITED.sub(keep_if_real, result.text)
    if invalid:
        log.warning("agent.invalid_citations", citations=invalid)
    cited = sorted({int(n) for n in _CITED.findall(answer)})
    if not cited:
        cited = _articles_named(answer, evidence.passages)
        if cited:
            log.info("agent.citations_by_article_number", cited=len(cited))
    sources = [citations[n - 1] for n in cited]

    body = _visible_answer(replace(result, text=answer))
    if sources:
        body += "\n\n<i>المصادر:</i>\n" + "\n".join(
            f"[{n}] {citations[n - 1]}" for n in cited
        )
    reply, handled_by = _finish(body, "agent.grounded", result.stop_reason)
    if handled_by != "agent.grounded":
        return reply, handled_by, []
    await _remember(memory, request, text, answer)
    log.info(
        "chat.agent_reply",
        rounds=outcome.rounds,
        passages=len(evidence.passages),
        cited=len(sources),
        forced_search=outcome.forced_search,
    )
    return reply, handled_by, sources


async def respond(
    request: ChatRequest,
    llm: LLMProtocol | None,
    retriever: RetrieverProtocol | None = None,
    *,
    grounded: bool = True,
    memory: MemoryProtocol | None = None,
    history_limit: int = 8,
    history_chars: int = 6000,
    agent: bool = False,
    agent_max_rounds: int = 3,
) -> tuple[str, str, list[str]]:
    """Return `(reply_html, handled_by, citations)` for one message.

    Dependencies are passed in rather than imported so that tests can supply
    fakes and never touch the network, and so a missing key or an unavailable
    knowledge base degrades to something honest instead of taking the service
    down.

    What is remembered: a question and its answer, or its refusal. Not
    commands, and not failures - after "the model did not answer", asking
    again should look like asking for the first time.
    """
    text = request.text.strip()

    if text.startswith("/reset"):
        reply, handled_by = await _reset(memory, request.chat_id)
        return reply, handled_by, []

    local = _local_reply(request, text)
    if local is not None:
        return (*local, [])

    if llm is None:
        return MODEL_UNAVAILABLE, "model.unavailable", []

    history = await _load_history(memory, request.chat_id, history_limit, history_chars)
    log.info("memory.loaded", messages=len(history))

    # `grounded=False` is an operator's explicit decision to run without a
    # knowledge base, which is honest. A retriever that is merely *missing*
    # while grounding is on is a fault, and faults do not get to fall back to
    # inventing - that is the behaviour this phase exists to remove.
    if not grounded:
        try:
            result = await _complete_in_script(llm, SYSTEM_PROMPT, text, history)
        except Exception:
            log.exception("llm.call_failed")
            return MODEL_FAILED, "model.error", []
        reply, handled_by = _finish(_visible_answer(result), "model", result.stop_reason)
        if handled_by == "model":
            await _remember(memory, request, text, result.text)
        return reply, handled_by, []

    if retriever is None:
        log.warning("retrieval.unavailable")
        return KNOWLEDGE_BASE_UNAVAILABLE, "retrieval.unavailable", []

    # Phase 10, where the provider can call tools. Ollama and Anthropic
    # cannot yet (ADR-018), and keep the Phase 8 pipeline below - which is
    # also what AGENT_ENABLED=false brings back.
    if agent and hasattr(llm, "complete_with_tools"):
        return await _respond_with_agent(
            request, llm, retriever, text, history, memory, agent_max_rounds
        )

    try:
        found = retriever.retrieve(text, context=_previous_question(history))
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
        await _remember(memory, request, text, REMEMBERED_REFUSAL)
        return NOTHING_FOUND, "retrieval.empty", []

    system = build_grounded_prompt(found.as_context())
    try:
        result = await _complete_in_script(llm, system, text, history)
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
    body = _visible_answer(result)
    if cited:
        body += "\n\n<i>المصادر:</i>\n" + "\n".join(cited)

    reply, handled_by = _finish(body, "model.grounded", result.stop_reason)
    if handled_by == "model.grounded":
        await _remember(memory, request, text, result.text)
    else:
        citations = []

    log.info(
        "chat.grounded_reply",
        passages=len(found.results),
        best_score=round(found.results[0].score, 3),
        citations=len(citations),
    )
    return reply, handled_by, citations
