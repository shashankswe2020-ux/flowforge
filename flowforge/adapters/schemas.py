"""Canonical schemas for adapter request/response normalization."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExecutionPolicy:
    """Execution constraints for a graph run."""

    max_tokens: int = 4096
    timeout_seconds: int = 300
    allow_tools: bool = True


@dataclass(frozen=True)
class CanonicalRequest:
    """Normalized input from any assistant adapter.

    All adapters produce this schema regardless of provider-specific format.
    """

    request_id: str
    assistant_provider: str
    user_prompt: str
    repository_context: str = ""
    constraints: list[str] = field(default_factory=list)
    execution_policy: ExecutionPolicy = field(default_factory=ExecutionPolicy)
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class CanonicalResponse:
    """Normalized output from a graph run back to the assistant.

    All adapters consume this schema to produce provider-specific responses.
    """

    request_id: str
    run_id: str
    terminal_status: str
    produced_artifacts: list[str] = field(default_factory=list)
    triaged_issues: list[str] = field(default_factory=list)
    shipping_readiness: dict[str, object] = field(default_factory=dict)
    shipping_result: dict[str, object] = field(default_factory=dict)
    diagnostics: dict[str, object] = field(default_factory=dict)
