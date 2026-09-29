"""What every model provider returns, and what it costs.

Splitting this out from the providers themselves is what lets `responder.py`
stay ignorant of which one answered. It asks for a completion and receives
text plus a token count; whether that came from a data centre or from the CPU
in this laptop is not its concern.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.logging import get_logger

log = get_logger(__name__)

# Reasoning models narrate their scratchpad in <think> blocks. Providers offer
# flags to suppress it; a model that ignores the flag would otherwise leak its
# working into the chat. Two cheap defences beat one clever one.
THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def strip_thinking(text: str) -> str:
    return THINK_BLOCK.sub("", text).strip()


# USD per million tokens, (input, output). Data, not logic, so a price change
# is a one-line edit with an obvious diff. Source: ADR-004.
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
    # What the provider says this call actually cost. Preferred over PRICING
    # whenever it is available: a table we forgot to update looks
    # authoritative and is wrong, which is worse than having no table.
    reported_cost_usd: float | None = None
    # A model running on this machine bills nothing. Electricity is real but
    # not per-token, and pretending otherwise would put a fake number in the
    # logs we plan to compare against in Phase 14.
    local: bool = False

    @property
    def cost_usd(self) -> float:
        if self.reported_cost_usd is not None:
            return round(self.reported_cost_usd, 6)
        if self.local:
            return 0.0
        rates = PRICING.get(self.model)
        if rates is None:
            # Worth noticing: PRICING has drifted from the configured model,
            # and every cost number since is silently wrong.
            log.warning("llm.unknown_pricing", model=self.model)
            return 0.0
        input_rate, output_rate = rates
        return round(
            (self.input_tokens * input_rate + self.output_tokens * output_rate)
            / 1_000_000,
            6,
        )
