"""Smoke tests for the ``flowforge.deep_agents`` T1 scaffold.

Verifies that:

* the ``deepagents`` runtime dependency is installed and importable;
* every module in the package is importable;
* the ``AgentRole`` enum has exactly the 8 values described in the
  spec (one per agentic node);
* an ``instructions/<role>.md`` file exists for every enum value.

No behavior is exercised — implementation lands in T2 onward.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from flowforge.deep_agents import AgentRole

EXPECTED_ROLES: frozenset[str] = frozenset(
    {
        "clarifier",
        "spec_author",
        "planner",
        "implementer",
        "reviewer",
        "auditor",
        "tester",
        "triager",
    },
)


def test_deepagents_dependency_importable() -> None:
    """The ``deepagents`` package declared in requirements is installed."""
    deepagents = importlib.import_module("deepagents")
    assert deepagents is not None


@pytest.mark.parametrize(
    "module_name",
    [
        "flowforge.deep_agents",
        "flowforge.deep_agents.factory",
        "flowforge.deep_agents.subagents",
        "flowforge.deep_agents.tools",
        "flowforge.deep_agents.adapters",
    ],
)
def test_module_is_importable(module_name: str) -> None:
    module = importlib.import_module(module_name)
    assert module.__doc__, f"{module_name} must have a module docstring"


def test_agent_role_enum_has_all_eight_roles() -> None:
    actual = {role.value for role in AgentRole}
    assert actual == EXPECTED_ROLES


def test_instructions_stub_exists_per_role() -> None:
    instructions_dir = (
        Path(__file__).resolve().parents[2]
        / "flowforge"
        / "deep_agents"
        / "instructions"
    )
    assert instructions_dir.is_dir(), instructions_dir

    for role in AgentRole:
        path = instructions_dir / f"{role.value}.md"
        assert path.is_file(), f"missing instructions stub for {role.value}"
        assert path.read_text(encoding="utf-8").strip(), (
            f"{path} is empty"
        )


def test_stubs_raise_not_implemented() -> None:
    """T1 stubs must raise ``NotImplementedError`` (sentinel for unimplemented)."""
    from flowforge.deep_agents import adapters, factory, subagents, tools

    with pytest.raises(NotImplementedError):
        subagents.get_subagents_for_role(AgentRole.CLARIFIER)

    with pytest.raises(NotImplementedError):
        tools._safe_path(Path("/tmp"), "x")

    with pytest.raises(NotImplementedError):
        factory.build_deep_agent(
            role=AgentRole.CLARIFIER,
            llm=None,  # type: ignore[arg-type]
            workdir=Path("/tmp"),
        )

    with pytest.raises(NotImplementedError):
        adapters.state_to_input(None, seed_prompt="x")  # type: ignore[arg-type]

    with pytest.raises(NotImplementedError):
        adapters.apply_agent_result(None, {}, node_name="x")  # type: ignore[arg-type]
