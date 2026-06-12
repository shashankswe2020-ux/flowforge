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


def test_safe_path_rejects_escape() -> None:
    """T2 :func:`tools._safe_path` rejects paths that escape the workdir.

    Implemented modules covered elsewhere:

    * T2 — :mod:`flowforge.deep_agents.tools` (see ``test_tools.py``).
    * T4 — :func:`flowforge.deep_agents.factory.build_deep_agent`
      (see ``test_factory.py``).
    * T5 — :mod:`flowforge.deep_agents.adapters` (see ``test_adapters.py``).
    """
    from flowforge.deep_agents import tools

    with pytest.raises(tools.PathTraversalError):
        tools._safe_path(Path("/tmp"), "/foo/../../etc/passwd")


def test_pyproject_package_data_includes_subagent_instruction_stubs() -> None:
    """Release guard: wheel package-data must include nested instruction files."""
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert '"flowforge.deep_agents"' in text
    assert "instructions/**/*.md" in text
