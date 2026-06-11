"""Sub-agent registry — named helpers attached to each parent role.

Lands in T3. T1 stub exposes ``get_subagents_for_role`` as the public
entry point and ``REGISTRY`` as the empty source of truth so dependent
modules type-check today.

See spec §5.4 (sub-agent table) for the catalog.
"""

from __future__ import annotations

from typing import Final

from flowforge.deep_agents import AgentRole


class SubAgentSpec:
    """Lightweight, frozen description of a named sub-agent.

    The full schema (tools, instructions, model overrides) lands in T3;
    today this only carries a ``name`` so the registry mapping is typed.
    """

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"SubAgentSpec(name={self.name!r})"


REGISTRY: Final[dict[AgentRole, tuple[SubAgentSpec, ...]]] = dict.fromkeys(
    AgentRole, (),
)
"""Empty per-role sub-agent catalog. Populated in T3."""


def get_subagents_for_role(role: AgentRole) -> tuple[SubAgentSpec, ...]:
    """Return the sub-agents registered for ``role``.

    Args:
        role: Parent agentic node role.

    Returns:
        Immutable tuple of sub-agent specs (empty until T3).

    Raises:
        NotImplementedError: T1 scaffold; full registry lands in T3.
    """

    raise NotImplementedError("sub-agent registry lands in T3")


__all__ = ["REGISTRY", "SubAgentSpec", "get_subagents_for_role"]
