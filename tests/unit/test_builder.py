"""Unit tests for builder helper functions."""

from __future__ import annotations

from flowforge.graph.builder import _non_reasoning_max_tokens


def test_claude_models_get_32k_budget() -> None:
    assert _non_reasoning_max_tokens("claude-opus-4.8") == 32768
    assert _non_reasoning_max_tokens("anthropic/claude-sonnet-4.5") == 32768
    assert _non_reasoning_max_tokens("CLAUDE-3.5-SONNET") == 32768


def test_non_claude_models_get_16k_budget() -> None:
    assert _non_reasoning_max_tokens("gpt-4o") == 16384
    assert _non_reasoning_max_tokens("gpt-4.1") == 16384
    assert _non_reasoning_max_tokens("openai/gpt-4o-mini") == 16384
