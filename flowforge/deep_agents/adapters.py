"""GraphState ⇄ Deep Agent (messages + VFS) adapters.

Lands in T5. T1 stub exposes the round-trip API surface (``state_to_input``,
``apply_agent_result``) so ``factory.py`` and node wrappers can import
typed entry points today.

See spec §5.4 / §8.1 for the contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from flowforge.state.models import GraphState


def state_to_input(state: GraphState, *, seed_prompt: str) -> dict[str, Any]:
    """Translate a ``GraphState`` into the Deep Agent ``invoke`` payload.

    Args:
        state: Current FlowForge graph state.
        seed_prompt: Role-specific human seed message body.

    Returns:
        A dict with at least ``messages`` and ``files`` keys ready for
        ``CompiledStateGraph.invoke``.

    Raises:
        NotImplementedError: T1 scaffold; implementation lands in T5.
    """

    raise NotImplementedError("state_to_input lands in T5")


def apply_agent_result(
    state: GraphState,
    result: dict[str, Any],
    *,
    node_name: str,
) -> dict[str, Any]:
    """Merge a Deep Agent result back into a ``GraphState`` delta.

    Args:
        state: Pre-call graph state (used to resolve ``workdir`` etc.).
        result: Raw output of ``agent.invoke``.
        node_name: Name of the calling LangGraph node — used as the
            ``deep_agent_traces`` key (spec §8.1).

    Returns:
        A LangGraph-compatible state-delta dict.

    Raises:
        NotImplementedError: T1 scaffold; implementation lands in T5.
    """

    raise NotImplementedError("apply_agent_result lands in T5")


__all__ = ["apply_agent_result", "state_to_input"]
