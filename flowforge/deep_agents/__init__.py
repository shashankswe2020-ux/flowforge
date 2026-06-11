"""FlowForge Deep Agents package.

Replaces single-shot LLM calls inside FlowForge's agentic nodes with
LangChain Deep Agents — multi-step agent loops with planning
(``write_todos``), a virtual file system, named sub-agents, and a
detailed per-role system prompt.

The outer LangGraph topology and Pydantic artifact contracts are
unchanged; the migration is gated behind a feature flag
(``FLOWFORGE_DEEP_AGENTS=1`` / ``--use-deep-agents``).

This is the T1 scaffold — modules expose typed stubs only; behavior
lands in subsequent tasks (T2–T9).
"""

from __future__ import annotations

from enum import StrEnum


class AgentRole(StrEnum):
    """Enumerates the eight FlowForge agentic node roles.

    One ``instructions/<value>.md`` system-prompt file exists per
    enum value. Mirrors spec §5.4.
    """

    CLARIFIER = "clarifier"
    SPEC_AUTHOR = "spec_author"
    PLANNER = "planner"
    IMPLEMENTER = "implementer"
    REVIEWER = "reviewer"
    AUDITOR = "auditor"
    TESTER = "tester"
    TRIAGER = "triager"


__all__ = ["AgentRole"]
