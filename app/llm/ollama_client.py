"""A model running on this machine, through Ollama.

Why this exists
---------------
ADR-004 put the model behind an adapter so that swapping it would be a config
line rather than a refactor. ADR-014 is the first time that promise is called
in: the Anthropic account ran out of credit, and work had to continue.

It is deliberately the same shape as `AnthropicClient` - same `complete()`
signature, same `LLMResult` - because `responder.py` must not be able to tell
them apart.

What you give up
----------------
A 1.7B model on a laptop CPU is not Opus 5. It is good enough to exercise the
pipeline, and measurably weaker at the thing this project cares most about:
saying "I do not know" instead of inventing an answer. Phase 14 will put a
number on that gap rather than leaving it a feeling.
"""

from __future__ import annotations

import httpx

from app.config import get_settings
from app.core.logging import get_logger
from app.llm.base import LLMResult, strip_thinking

log = get_logger(__name__)

class OllamaClient:
    """Talks to a local Ollama server over its HTTP API."""

    def __init__(self) -> None:
        settings = get_settings()
        self._base_url = settings.ollama_base_url.rstrip("/")
        self._model = settings.ollama_model
        # No SDK: Ollama's API is two fields of JSON, and httpx is already a
        # dependency. A package would be one more thing to keep current.
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            # Generous, because generation happens on a CPU here. The n8n node
            # has its own, shorter timeout - whichever fires first wins.
            timeout=httpx.Timeout(180.0, connect=5.0),
        )

    async def complete(
        self,
        system: str,
        user_message: str,
        max_tokens: int = 512,
    ) -> LLMResult:
        response = await self._client.post(
            "/api/chat",
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_message},
                ],
                "stream": False,
                "think": False,
                "options": {"num_predict": max_tokens},
            },
        )
        response.raise_for_status()
        data = response.json()

        text = strip_thinking(data.get("message", {}).get("content", ""))
        result = LLMResult(
            text=text,
            model=data.get("model", self._model),
            # Ollama's names for the same two numbers.
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
            stop_reason=data.get("done_reason"),
            local=True,
        )
        log.info(
            "llm.completion",
            provider="ollama",
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
            stop_reason=result.stop_reason,
            # Nanoseconds from Ollama; seconds are what a human debugs with.
            duration_s=round(data.get("total_duration", 0) / 1e9, 1),
        )
        return result
