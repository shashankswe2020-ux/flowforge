"""Typed bounded-execution errors for Deep Agents (T10, spec §10.6).

Every error carries the role, the LangGraph node name, and a
:class:`flowforge.state.models.DeepAgentTrace` snapshot of work
completed before the limit fired so that wrappers can persist a
partial trace into ``GraphState.deep_agent_traces``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flowforge.deep_agents import AgentRole
    from flowforge.state.models import DeepAgentTrace

__all__ = [
    "AgentTimeoutError",
    "DeepAgentLimitError",
    "RecursionLimitExceededError",
    "ToolBudgetExceededError",
]


class DeepAgentLimitError(RuntimeError):
    """Base for typed Deep Agent limit errors."""

    def __init__(
        self,
        message: str,
        *,
        role: AgentRole,
        node_name: str,
        partial_trace: DeepAgentTrace,
    ) -> None:
        super().__init__(message)
        self.role = role
        self.node_name = node_name
        self.partial_trace = partial_trace


class RecursionLimitExceededError(DeepAgentLimitError):
    """Raised when LangGraph signals the configured recursion limit."""


class AgentTimeoutError(DeepAgentLimitError):
    """Raised when the wall-clock timeout for a Deep Agent run elapses."""


class ToolBudgetExceededError(DeepAgentLimitError):
    """Raised when the per-node tool-invocation budget is exhausted."""
