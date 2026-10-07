"""The agent loop: the model decides whether to look, Python decides the rules.

What changed from Phase 8
-------------------------
Phase 8 searched on every message, so "مرحبا" was searched for, found
nothing, and was refused - the visible cost ADR-005 accepted in exchange for
a baseline. Here the model is given tools and chooses: greet back, or search,
or look an article up by number, or search twice with better words. It also
writes its own search query, which is how a follow-up like "وإذا كان
مسلحاً؟" becomes a searchable "عقوبة السرقة مع حمل السلاح" without a
separate rewriting step.

What the model does not decide
------------------------------
`ARCHITECTURE.md`: the model chooses which tool, never which rule. Three
rules are enforced here, and one more in the responder:

1. **A ceiling on rounds.** After `max_rounds` rounds of tool calls the model
   is told to answer with what it has. A model that keeps searching is a loop
   that never replies, and on a free tier every round is one of fifty
   requests a day.
2. **No substantive answer without evidence.** If the model answers without
   calling a tool, the answer must look like small talk: short, and with no
   numbers in it. Anything else - "عقوبة القتل من 15 إلى 20 سنة" from memory -
   is discarded, the question is searched by the code itself, and the model
   answers again from what that found. A heuristic, stated as one: Phase 14
   measures how often it misfires.
3. **The language guard from Phase 8** applies to the final answer.

The fourth - nothing found means a refusal, whatever the model wrote - is in
`responder.py`, next to the Phase 8 version of the same rule.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from app.agent.tools import TOOLS, Evidence, execute
from app.core.logging import get_logger
from app.llm.base import LLMResult, Message, ToolCall, ToolSpec, ToolStep
from app.llm.prompts import AGENT_SYSTEM_PROMPT
from app.rag.quality import drop_foreign_sentences, foreign_characters
from app.rag.retrieval import Retriever

log = get_logger(__name__)

# Rule 2. Small talk is short and has no figures in it; a penalty, an article
# number or a worked answer has digits almost by definition.
SMALL_TALK_MAX_CHARS = 300
_DIGIT = re.compile(r"[0-9٠-٩]")


class ToolCapableLLM(Protocol):
    async def complete_with_tools(
        self,
        system: str,
        user_message: str,
        *,
        tools: Sequence[ToolSpec],
        steps: Sequence[ToolStep] = ...,
        history: Sequence[Message] = ...,
        allow_tools: bool = ...,
    ) -> LLMResult: ...


@dataclass
class AgentResult:
    result: LLMResult  # the final answer
    evidence: Evidence
    rounds: int
    forced_search: bool


def looks_like_small_talk(text: str) -> bool:
    return len(text) <= SMALL_TALK_MAX_CHARS and not _DIGIT.search(text)


async def run_agent(
    llm: ToolCapableLLM,
    retriever: Retriever,
    text: str,
    history: Sequence[Message] = (),
    *,
    previous_question: str | None = None,
    max_rounds: int = 3,
    context_chars: int = 6000,
) -> AgentResult:
    evidence = Evidence()
    steps: list[ToolStep] = []

    async def ask(allow_tools: bool) -> LLMResult:
        return await llm.complete_with_tools(
            AGENT_SYSTEM_PROMPT,
            text,
            tools=TOOLS,
            steps=steps,
            history=history,
            allow_tools=allow_tools,
        )

    def run(calls: Sequence[ToolCall], context: str | None = None) -> None:
        results = tuple(
            execute(c, retriever, evidence, context=context, max_chars=context_chars)
            for c in calls
        )
        steps.append(ToolStep(calls=tuple(calls), results=results))

    # Rule 1: at most `max_rounds` rounds of tools, then one last call that
    # may not use them.
    result = await ask(allow_tools=True)
    while result.tool_calls and len(steps) < max_rounds:
        run(result.tool_calls)
        result = await ask(allow_tools=len(steps) < max_rounds)
    if result.tool_calls:
        # Told "none" and asked for tools anyway. Its text, if any, is the
        # answer; the responder deals with an empty one.
        log.warning("agent.ignored_tool_choice", rounds=len(steps))

    # Rule 2.
    forced = False
    if not evidence.searched and not looks_like_small_talk(result.text):
        log.warning("agent.answered_without_evidence", chars=len(result.text))
        forced = True
        run(
            [ToolCall(id="forced-search", name="search_knowledge_base", arguments={"query": text})],
            # The model's own query would have resolved a follow-up; this
            # one is the raw message, so it gets the previous question as
            # context, as in Phase 9.
            context=previous_question,
        )
        result = await ask(allow_tools=False)

    # Rule 3.
    if foreign_characters(result.text):
        log.warning("llm.foreign_script", attempt=1)
        result = await ask(allow_tools=False)
        if foreign_characters(result.text):
            log.warning("llm.foreign_script", attempt=2, action="dropped_sentences")
            result = replace(result, text=drop_foreign_sentences(result.text))

    log.info(
        "agent.done",
        rounds=len(steps),
        passages=len(evidence.passages),
        searched=evidence.searched,
        forced_search=forced,
    )
    return AgentResult(result=result, evidence=evidence, rounds=len(steps), forced_search=forced)
