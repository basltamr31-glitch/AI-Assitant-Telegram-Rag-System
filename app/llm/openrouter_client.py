"""One key, many models, through OpenRouter.

Why this provider
-----------------
ADR-015. Two reasons, both about Arabic:

* The local 1.7B model answers Arabic, but roughly - one reply mixed a
  Vietnamese word into an Arabic sentence. The knowledge base is Arabic legal
  and curriculum text, so a weak Arabic model would make every later
  retrieval measurement untrustworthy: you could not tell a bad chunk from a
  bad reader.
* OpenRouter's API is OpenAI-compatible, so this single adapter reaches
  hundreds of models. Changing which one answers becomes `OPENROUTER_MODEL`,
  with no code change at all - one step beyond what ADR-004 promised.

Cost
----
Prices vary per model and change without notice, so hardcoding them would
guarantee wrong numbers. We ask OpenRouter to return the actual cost of each
call (`usage.include`) and record that. A price table we forgot to update is
worse than no table, because it looks authoritative.
"""

from __future__ import annotations

import httpx

from app.config import get_settings
from app.core.logging import get_logger
from app.llm.base import LLMResult, strip_thinking

log = get_logger(__name__)


class OpenRouterClient:
    """OpenAI-compatible chat completions, via OpenRouter."""

    def __init__(self) -> None:
        settings = get_settings()
        key = settings.openrouter_api_key.get_secret_value()
        if not key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set - add it to .env, or set "
                "LLM_PROVIDER to ollama."
            )
        self._model = settings.openrouter_model
        self._client = httpx.AsyncClient(
            base_url=settings.openrouter_base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {key}",
                # OpenRouter attributes usage to an app when these are sent.
                # Neither is a secret and both are optional.
                "HTTP-Referer": "https://github.com/basltamr31-glitch/AI-Assitant-Telegram-Rag-System",
                "X-Title": "Telegram AI Assistant",
            },
            timeout=httpx.Timeout(120.0, connect=10.0),
        )

    async def complete(
        self,
        system: str,
        user_message: str,
        max_tokens: int = 1024,
    ) -> LLMResult:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_message},
            ],
            "max_tokens": max_tokens,
            # Ask for the real cost of this call rather than guessing it.
            "usage": {"include": True},
        }
        # A 200 is not always an answer. The free upstreams sometimes return a
        # success status whose body carries an error and no `choices` - six of
        # the first ten benchmark calls to Nemotron Ultra, and a real Telegram
        # question on 2026-10-07. It is transient, so it gets exactly one
        # retry. General retry policy is still Phase 13's.
        for attempt in (1, 2):
            response = await self._client.post("/chat/completions", json=payload)
            if response.status_code >= 400:
                # OpenRouter puts the useful part in the body; the status alone
                # does not distinguish "no credit" from "unknown model".
                log.error(
                    "llm.openrouter_error",
                    status=response.status_code,
                    body=response.text[:400],
                )
            response.raise_for_status()
            data = response.json()
            if data.get("choices"):
                break
            log.warning(
                "llm.openrouter_no_choices", attempt=attempt, body=response.text[:400]
            )
        else:
            raise RuntimeError("OpenRouter returned no choices, twice")

        choice = data["choices"][0]
        text = strip_thinking(choice["message"].get("content") or "")
        usage = data.get("usage") or {}

        result = LLMResult(
            text=text,
            # What answered, which can differ from what was asked for when a
            # model is routed to a fallback.
            model=data.get("model", self._model),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            stop_reason=choice.get("finish_reason"),
            # Reported cost wins over any local table; `local` stays False so
            # a free model still records a real, measured zero.
            reported_cost_usd=usage.get("cost"),
        )
        log.info(
            "llm.completion",
            provider="openrouter",
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
            stop_reason=result.stop_reason,
        )
        return result
