"""Deep Agent factory — builds a configured Deep Agent per role.

Lands in T4. This module is the T1 stub: it declares the public
``build_deep_agent`` signature so dependent modules can import it,
but the implementation raises ``NotImplementedError``.

See spec §5.3 for the contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from langchain_core.language_models import BaseChatModel
    from langgraph.graph.state import CompiledStateGraph

    from flowforge.deep_agents import AgentRole


class _ToolLike(Protocol):
    """Structural protocol for LangChain ``BaseTool``-compatible objects."""

    name: str


def build_deep_agent(
    role: AgentRole,
    llm: BaseChatModel,
    workdir: Path,
    todo_seed: list[str] | None = None,
    extra_tools: list[_ToolLike] | None = None,
) -> CompiledStateGraph:  # type: ignore[type-arg]
    """Build a Deep Agent graph for the given ``role``.

    The returned graph has:

    * a system prompt loaded from ``instructions/<role>.md``;
    * the role-appropriate subset of the FlowForge tool library;
    * sub-agents pulled from the registry by role;
    * tools confined to ``workdir`` via the ``_safe_path`` policy.

    Args:
        role: Which agentic node this graph backs.
        llm: Chat model wired through the existing FlowForge adapter layer.
        workdir: Generated-repo workdir; tools constrain writes here.
        todo_seed: Optional initial plan seeded into ``write_todos``.
        extra_tools: Additional tools merged with the role's defaults.

    Returns:
        A compiled LangGraph state graph ready for ``.invoke``.

    Raises:
        NotImplementedError: T1 scaffold; implementation lands in T4.
    """

    raise NotImplementedError("build_deep_agent lands in T4")


__all__ = ["build_deep_agent"]
