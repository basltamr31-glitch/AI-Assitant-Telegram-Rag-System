"""The Anthropic adapter.

ADR-004 put the model behind an adapter rather than calling the SDK from the
request handler, for two reasons that both arrive later:

* **Phase 10** needs a tool-use loop. That belongs here, not scattered through
  the API layer.
* **Swapping models** should be one config line. `responder.py` asks for a
  completion and receives text and a token count; it never learns which
  vendor produced them.

Cost is measured from the first call, not bolted on in Phase 14. A number you
only start collecting once it hurts has no history to compare against, and
"is retrieval worth the extra tokens?" is unanswerable without one.
"""

from __future__ import annotations

from dataclasses import dataclass

from anthropic import AsyncAnthropic

from app.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

# USD per million tokens, (input, output). Kept as data rather than buried in
# a formula so that a price change is a one-line edit with an obvious diff.
# Source: ADR-004.
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
}


@dataclass(frozen=True)
class LLMResult:
    """One completion, plus what it cost to produce."""

    text: str
    model: str
    input_tokens: int
    output_tokens: int
    stop_reason: str | None

    @property
    def cost_usd(self) -> float:
        """Estimated cost. Unknown models price at zero rather than crash."""
        rates = PRICING.get(self.model)
        if rates is None:
            # Worth noticing: it means PRICING drifted from the configured
            # model, and every cost number since is silently wrong.
            log.warning("llm.unknown_pricing", model=self.model)
            return 0.0
        input_rate, output_rate = rates
        return round(
            (self.input_tokens * input_rate + self.output_tokens * output_rate)
            / 1_000_000,
            6,
        )


class AnthropicClient:
    """Thin async wrapper around the Anthropic Messages API."""

    def __init__(self) -> None:
        settings = get_settings()
        key = settings.anthropic_api_key.get_secret_value()
        if not key:
            # Fail here, at startup, rather than on the first user message.
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set - add it to .env before Phase 5."
            )
        self._client = AsyncAnthropic(api_key=key)
        self._model = settings.anthropic_model

    async def complete(
        self,
        system: str,
        user_message: str,
        max_tokens: int = 1024,
    ) -> LLMResult:
        """Send one stateless message and return the reply.

        Stateless on purpose: there is no conversation history yet. Phase 9
        adds it, and this signature grows a `messages` parameter then. Until
        the storage exists, pretending to remember would be a lie.
        """
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )

        text = "".join(
            block.text for block in response.content if block.type == "text"
        )
        result = LLMResult(
            text=text,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            stop_reason=response.stop_reason,
        )
        log.info(
            "llm.completion",
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
            stop_reason=result.stop_reason,
            # The prompt and the reply are not logged, for the same reason the
            # message text is not: logs travel, conversations should not.
        )
        return result
