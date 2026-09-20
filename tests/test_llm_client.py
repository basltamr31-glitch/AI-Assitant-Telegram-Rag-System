"""Tests for cost accounting.

No network: these check arithmetic and the unknown-model path only. What the
model *says* is not something a unit test can assert on - that is Phase 14's
job, with an eval set.
"""

from __future__ import annotations

from app.llm.client import PRICING, LLMResult


def _result(model: str, tokens_in: int, tokens_out: int) -> LLMResult:
    return LLMResult(
        text="x",
        model=model,
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        stop_reason="end_turn",
    )


def test_cost_matches_the_published_rates() -> None:
    # 1M input at $5 plus 1M output at $25.
    assert _result("claude-opus-5", 1_000_000, 1_000_000).cost_usd == 30.0


def test_a_typical_message_costs_about_a_cent() -> None:
    cost = _result("claude-opus-5", 1_200, 300).cost_usd
    assert cost == round((1_200 * 5 + 300 * 25) / 1_000_000, 6)
    assert 0.01 < cost < 0.02


def test_zero_tokens_cost_nothing() -> None:
    assert _result("claude-opus-5", 0, 0).cost_usd == 0.0


def test_unknown_model_does_not_crash() -> None:
    """A model missing from PRICING must not take down a reply."""
    assert _result("claude-from-the-future", 100, 100).cost_usd == 0.0


def test_configured_model_is_priced() -> None:
    """Guards against ANTHROPIC_MODEL drifting away from the price table."""
    from app.config import Settings

    assert Settings().anthropic_model in PRICING
