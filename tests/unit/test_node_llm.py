"""Unit tests for the shared LLM invocation helper.

Some providers (notably GitHub Copilot / Anthropic) occasionally return a
response with zero generations. Inside LangChain that surfaces as an
``IndexError`` from ``generations[0][0]`` and previously crashed the whole
pipeline. ``invoke_llm`` retries once and otherwise raises a typed error.
"""

from __future__ import annotations

import pytest

from flowforge.nodes._llm import EmptyLLMResponseError, invoke_llm


class _FlakyLLM:
    """Mock LLM that raises IndexError (empty generations) N times first."""

    def __init__(self, fail_times: int, content: str = "ok") -> None:
        self._fail_times = fail_times
        self._content = content
        self.calls = 0

    def invoke(self, prompt: str) -> str:
        self.calls += 1
        if self.calls <= self._fail_times:
            # Mirror LangChain's empty-generations crash site.
            empty: list[str] = []
            return empty[0]
        return self._content


def test_returns_response_on_first_success() -> None:
    llm = _FlakyLLM(fail_times=0, content="plan")
    assert invoke_llm(llm, "prompt", node_name="plan_node") == "plan"
    assert llm.calls == 1


def test_retries_then_succeeds() -> None:
    llm = _FlakyLLM(fail_times=1, content="plan")
    assert invoke_llm(llm, "prompt", node_name="plan_node", retries=2) == "plan"
    assert llm.calls == 2


def test_raises_typed_error_after_exhausting_retries() -> None:
    llm = _FlakyLLM(fail_times=5)
    with pytest.raises(EmptyLLMResponseError) as exc_info:
        invoke_llm(llm, "prompt", node_name="plan_node", retries=2)
    assert exc_info.value.node_name == "plan_node"
    assert exc_info.value.attempts == 2
    assert llm.calls == 2


def test_error_message_is_actionable() -> None:
    llm = _FlakyLLM(fail_times=5)
    with pytest.raises(EmptyLLMResponseError, match="empty response"):
        invoke_llm(llm, "prompt", node_name="spec_node", retries=1)
