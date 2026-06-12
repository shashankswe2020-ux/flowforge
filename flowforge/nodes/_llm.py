"""Shared LLM invocation helper with empty-response resilience.

Some providers (notably GitHub Copilot / Anthropic) occasionally return a
response that contains zero generations. Inside LangChain this surfaces as an
``IndexError`` raised from ``generations[0][0]`` deep in
``BaseChatModel.invoke``. Without handling, a single empty response crashes the
entire pipeline with a cryptic stack trace. ``invoke_llm`` retries the call a
small number of times and otherwise raises a typed, actionable error.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger("flowforge.nodes.llm")


class _Invokable(Protocol):
    """Minimal LLM interface: an object exposing ``invoke(prompt)``."""

    def invoke(self, prompt: str) -> Any: ...  # noqa: ANN401


class EmptyLLMResponseError(RuntimeError):
    """Raised when the LLM returns no completion after all retries.

    Replaces the cryptic ``IndexError: list index out of range`` that
    LangChain raises from ``generations[0][0]`` when a provider returns a
    response with zero generations.
    """

    def __init__(self, node_name: str, attempts: int) -> None:
        super().__init__(
            f"The model returned an empty response in {node_name} after "
            f"{attempts} attempt(s). This is usually a transient provider "
            "issue (rate limiting or a momentary outage). Re-run the pipeline; "
            "if it persists, try a different model or reduce the prompt size.",
        )
        self.node_name = node_name
        self.attempts = attempts


def invoke_llm(
    llm: _Invokable,
    prompt: str,
    *,
    node_name: str,
    retries: int = 2,
) -> Any:  # noqa: ANN401
    """Invoke the LLM, retrying on an empty (zero-generation) response.

    Args:
        llm: Object exposing ``invoke(prompt) -> response``.
        prompt: The prompt string to send.
        node_name: Identifier used in log messages and the raised error.
        retries: Total number of attempts (clamped to >= 1).

    Returns:
        The provider response object from the first successful attempt.

    Raises:
        EmptyLLMResponseError: If every attempt yields an empty response.
    """
    attempts = max(1, retries)
    for attempt in range(1, attempts + 1):
        try:
            return llm.invoke(prompt)
        except IndexError:
            logger.warning(
                "Empty LLM response in %s (attempt %d/%d)",
                node_name,
                attempt,
                attempts,
            )
    raise EmptyLLMResponseError(node_name, attempts)
