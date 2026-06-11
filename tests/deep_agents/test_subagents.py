"""Tests for the T3 sub-agent registry.

Covers spec §7.1 (catalog) and §7.2 (sub-agent contract):

* every catalog entry is present and well-formed;
* per-role lookup matches the canonical mapping;
* prompt bodies are loaded from
  ``flowforge/deep_agents/instructions/subagents/<name>.md``;
* the VFS namespace helper enforces the
  ``vfs:/subagent/<name>/`` write-prefix invariant.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flowforge.deep_agents import AgentRole
from flowforge.deep_agents.subagents import (
    SUBAGENT_REGISTRY,
    SubAgentSpec,
    is_in_subagent_namespace,
    namespace_vfs_path,
    subagents_for,
)

# ---------------------------------------------------------------------------
# Catalog (spec §7.1)
# ---------------------------------------------------------------------------

EXPECTED_CATALOG: dict[AgentRole, frozenset[str]] = {
    AgentRole.CLARIFIER: frozenset(),
    AgentRole.SPEC_AUTHOR: frozenset({"researcher"}),
    AgentRole.PLANNER: frozenset({"estimator"}),
    AgentRole.IMPLEMENTER: frozenset({"refactorer", "doc_writer"}),
    AgentRole.REVIEWER: frozenset({"arch_reviewer", "perf_reviewer"}),
    AgentRole.AUDITOR: frozenset({"dep_scanner", "secret_scanner"}),
    AgentRole.TESTER: frozenset({"coverage_analyst"}),
    AgentRole.TRIAGER: frozenset({"dedupe_helper"}),
}

ALL_NAMES: frozenset[str] = frozenset().union(*EXPECTED_CATALOG.values())


def test_registry_has_exactly_ten_entries() -> None:
    """Spec §7.1 lists exactly 10 sub-agents."""
    assert len(SUBAGENT_REGISTRY) == 10
    assert set(SUBAGENT_REGISTRY) == set(ALL_NAMES)


def test_registry_keyed_by_name() -> None:
    for name, spec in SUBAGENT_REGISTRY.items():
        assert spec.name == name


@pytest.mark.parametrize("name", sorted(ALL_NAMES))
def test_spec_fields_are_well_formed(name: str) -> None:
    spec = SUBAGENT_REGISTRY[name]
    assert isinstance(spec, SubAgentSpec)
    assert spec.name == name
    assert spec.description.strip(), "description must be non-empty"
    assert spec.prompt.strip(), "prompt must be non-empty"
    # tools is an immutable tuple (may be empty for read-only/planning subagents)
    assert isinstance(spec.tools, tuple)
    # model override is optional
    assert spec.model is None or isinstance(spec.model, str)


def test_specs_are_frozen_dataclasses() -> None:
    spec = next(iter(SUBAGENT_REGISTRY.values()))
    with pytest.raises((AttributeError, TypeError)):
        spec.name = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Per-role lookup (spec §5.1 + §7.1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(AgentRole))
def test_subagents_for_role_matches_catalog(role: AgentRole) -> None:
    result = subagents_for(role)
    assert isinstance(result, tuple)
    names = {spec.name for spec in result}
    assert names == EXPECTED_CATALOG[role]
    # All returned specs must come from the registry (single source of truth).
    for spec in result:
        assert SUBAGENT_REGISTRY[spec.name] is spec


def test_subagents_for_clarifier_is_empty() -> None:
    assert subagents_for(AgentRole.CLARIFIER) == ()


def test_subagents_for_returns_immutable_tuple() -> None:
    result = subagents_for(AgentRole.IMPLEMENTER)
    assert isinstance(result, tuple)
    # Mutating the returned tuple must not affect the registry.
    with pytest.raises((AttributeError, TypeError)):
        result.append(SubAgentSpec(  # type: ignore[attr-defined]
            name="x", description="x", prompt="x", tools=(),
        ))


# ---------------------------------------------------------------------------
# Prompt loading (spec §7.2 — versioned alongside parent prompts)
# ---------------------------------------------------------------------------


def test_each_subagent_has_an_instructions_md_stub() -> None:
    instructions_dir = (
        Path(__file__).resolve().parents[2]
        / "flowforge"
        / "deep_agents"
        / "instructions"
        / "subagents"
    )
    assert instructions_dir.is_dir(), instructions_dir
    for name in ALL_NAMES:
        path = instructions_dir / f"{name}.md"
        assert path.is_file(), f"missing instructions stub for sub-agent {name!r}"
        body = path.read_text(encoding="utf-8").strip()
        assert body, f"{path} is empty"


def test_prompts_are_loaded_from_disk_into_registry() -> None:
    instructions_dir = (
        Path(__file__).resolve().parents[2]
        / "flowforge"
        / "deep_agents"
        / "instructions"
        / "subagents"
    )
    for name, spec in SUBAGENT_REGISTRY.items():
        on_disk = (instructions_dir / f"{name}.md").read_text(encoding="utf-8")
        assert spec.prompt == on_disk


# ---------------------------------------------------------------------------
# VFS namespacing (spec §7.2 — writes confined to vfs:/subagent/<name>/)
# ---------------------------------------------------------------------------


def test_namespace_vfs_path_prefixes_sub_agent_writes() -> None:
    out = namespace_vfs_path("researcher", "notes.md")
    assert out == "vfs:/subagent/researcher/notes.md"


def test_namespace_vfs_path_strips_leading_slashes() -> None:
    assert (
        namespace_vfs_path("researcher", "/findings/a.json")
        == "vfs:/subagent/researcher/findings/a.json"
    )
    assert (
        namespace_vfs_path("researcher", "vfs:/findings/a.json")
        == "vfs:/subagent/researcher/findings/a.json"
    )


def test_namespace_vfs_path_idempotent_for_already_namespaced() -> None:
    already = "vfs:/subagent/researcher/notes.md"
    assert namespace_vfs_path("researcher", already) == already


def test_namespace_vfs_path_rejects_unknown_subagent() -> None:
    with pytest.raises(KeyError):
        namespace_vfs_path("nope", "x.md")


def test_namespace_vfs_path_rejects_traversal() -> None:
    with pytest.raises(ValueError, match="traversal"):
        namespace_vfs_path("researcher", "../../etc/passwd")


def test_namespace_vfs_path_rejects_other_subagent_namespace() -> None:
    """Cannot remap a write claimed by *another* sub-agent."""
    with pytest.raises(ValueError, match="namespace"):
        namespace_vfs_path("researcher", "vfs:/subagent/estimator/x.json")


def test_is_in_subagent_namespace_accepts_canonical_paths() -> None:
    assert is_in_subagent_namespace("researcher", "vfs:/subagent/researcher/a.md")
    assert is_in_subagent_namespace(
        "researcher", "vfs:/subagent/researcher/nested/b.json",
    )


def test_is_in_subagent_namespace_rejects_other_paths() -> None:
    assert not is_in_subagent_namespace("researcher", "vfs:/findings/a.json")
    assert not is_in_subagent_namespace(
        "researcher", "vfs:/subagent/estimator/a.json",
    )
    assert not is_in_subagent_namespace("researcher", "notes.md")
