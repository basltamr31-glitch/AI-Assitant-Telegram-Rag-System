"""Tests for cost accounting.

No network: these check arithmetic and the unknown-model path only. What the
model *says* is not something a unit test can assert on - that is Phase 14's
job, with an eval set.
"""

from __future__ import annotations

import pytest

from app.llm.base import PRICING, LLMResult


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


# --- the local provider ------------------------------------------------------


def test_a_local_model_costs_nothing() -> None:
    """Electricity is real, but it is not per-token. A fake number in the
    logs would poison the comparison Phase 14 exists to make."""
    r = LLMResult(
        text="x",
        model="qwen3:1.7b",
        input_tokens=5_000,
        output_tokens=5_000,
        stop_reason="stop",
        local=True,
    )
    assert r.cost_usd == 0.0


def test_a_local_model_does_not_warn_about_missing_pricing() -> None:
    """`local=True` must short-circuit before the PRICING lookup, or every
    local reply would log a spurious 'unknown pricing' warning."""
    assert "qwen3:1.7b" not in PRICING
    assert _result("qwen3:1.7b", 10, 10).cost_usd == 0.0  # unknown, not local
    assert LLMResult("x", "qwen3:1.7b", 10, 10, "stop", local=True).cost_usd == 0.0


def test_thinking_blocks_are_stripped() -> None:
    """Reasoning models narrate their scratchpad. The user must never see it."""
    from app.llm.base import strip_thinking

    raw = "<think>Let me work this out...\nstep 2</think>The capital is Paris."
    assert strip_thinking(raw) == "The capital is Paris."


def test_the_factory_returns_the_configured_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ADR-004 promise, asserted: switching providers is one config line."""
    from app.config import get_settings
    from app.llm.client import create_llm_client
    from app.llm.ollama_client import OllamaClient

    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    get_settings.cache_clear()
    try:
        assert isinstance(create_llm_client(), OllamaClient)
    finally:
        get_settings.cache_clear()


# --- provider-reported cost --------------------------------------------------


def test_reported_cost_wins_over_the_local_table() -> None:
    """OpenRouter prices change without notice. A stale table looks
    authoritative and is wrong, so a figure from the provider beats it."""
    r = LLMResult(
        text="x",
        model="claude-opus-5",          # present in PRICING...
        input_tokens=1_000_000,
        output_tokens=1_000_000,        # ...which would say $30.00
        stop_reason="stop",
        reported_cost_usd=0.004321,
    )
    assert r.cost_usd == 0.004321


def test_a_free_model_records_a_measured_zero() -> None:
    """Zero because the provider said so, not because we failed to price it."""
    r = LLMResult(
        text="x",
        model="qwen/qwen3.8-27b:free",
        input_tokens=900,
        output_tokens=120,
        stop_reason="stop",
        reported_cost_usd=0.0,
    )
    assert r.cost_usd == 0.0


def test_openrouter_is_reachable_through_the_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import get_settings
    from app.llm.client import create_llm_client
    from app.llm.openrouter_client import OpenRouterClient

    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-a-real-key")
    get_settings.cache_clear()
    try:
        assert isinstance(create_llm_client(), OpenRouterClient)
    finally:
        get_settings.cache_clear()


def test_openrouter_without_a_key_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selecting a provider you have not configured must say so at startup,
    not produce a confusing 401 on the user's first message."""
    from app.config import get_settings
    from app.llm.client import create_llm_client

    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
            create_llm_client()
    finally:
        get_settings.cache_clear()
