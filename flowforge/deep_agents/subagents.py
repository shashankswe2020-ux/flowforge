"""Sub-agent registry (T3 — spec §7.1, §7.2).

Encodes the 10-entry catalog of named sub-agents that FlowForge's Deep
Agents can delegate to via the framework's ``task`` tool. Each entry is
a frozen :class:`SubAgentSpec` carrying the canonical ``name``,
``description``, ``prompt`` (loaded from
``flowforge/deep_agents/instructions/subagents/<name>.md``), an
allow-listed ``tools`` tuple, and an optional ``model`` override.

The registry also owns the **VFS namespace invariant** from spec §7.2:
sub-agent writes must live under ``vfs:/subagent/<name>/``. The
:func:`namespace_vfs_path` and :func:`is_in_subagent_namespace`
helpers are consumed by :mod:`flowforge.deep_agents.adapters` to
enforce that invariant.

Catalog (spec §7.1):

* ``spec_author`` → ``researcher``
* ``planner``     → ``estimator``
* ``implementer`` → ``refactorer``, ``doc_writer``
* ``reviewer``    → ``arch_reviewer``, ``perf_reviewer``
* ``auditor``     → ``dep_scanner``, ``secret_scanner``
* ``tester``      → ``coverage_analyst``
* ``triager``     → ``dedupe_helper``
* ``clarifier``   → (none)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from flowforge.deep_agents import AgentRole

__all__ = [
    "SUBAGENT_REGISTRY",
    "SubAgentSpec",
    "get_subagents_for_role",
    "is_in_subagent_namespace",
    "namespace_vfs_path",
    "subagents_for",
]

_VFS_PREFIX: Final[str] = "vfs:/"
_SUBAGENT_PREFIX: Final[str] = "vfs:/subagent/"

_INSTRUCTIONS_DIR: Final[Path] = (
    Path(__file__).resolve().parent / "instructions" / "subagents"
)


# ---------------------------------------------------------------------------
# Spec model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SubAgentSpec:
    """Frozen description of a named Deep Agent sub-agent (spec §7).

    Attributes:
        name: Unique sub-agent identifier (matches the registry key and
            the ``instructions/subagents/<name>.md`` filename).
        description: One-line summary surfaced to the parent agent.
        prompt: Full system prompt body, loaded from disk so prompt
            changes are versioned alongside parent prompts.
        tools: Names of FlowForge tools (from
            :mod:`flowforge.deep_agents.tools`) the sub-agent is
            allowed to invoke. Built-in framework tools (``ls``,
            ``read_file``, ``write_file``, ``edit_file``,
            ``write_todos``, ``task``) are always implicitly available
            and are NOT listed here.
        model: Optional per-sub-agent model override; ``None`` means
            inherit the parent agent's model (spec §14 Q1 default).
    """

    name: str
    description: str
    prompt: str
    tools: tuple[str, ...] = field(default=())
    model: str | None = None


# ---------------------------------------------------------------------------
# Catalog construction
# ---------------------------------------------------------------------------


def _load_prompt(name: str) -> str:
    path = _INSTRUCTIONS_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(
            f"missing sub-agent instructions stub: {path}",
        )
    return path.read_text(encoding="utf-8")


# (sub-agent name, description, allow-listed FlowForge tool names)
#
# Tool selections follow the spirit of spec §6: each sub-agent gets the
# minimum set required for its read-mostly purpose. Built-in framework
# tools are always available and are not listed.
_CATALOG_SEED: Final[tuple[tuple[str, str, tuple[str, ...]], ...]] = (
    (
        "researcher",
        "Gather references and prior art; writes go to vfs:/subagent/researcher/.",
        ("web_search",),
    ),
    (
        "estimator",
        "Estimate task size and dependencies; emits estimates.json under vfs:/subagent/estimator/.",
        (),
    ),
    (
        "refactorer",
        "Apply mechanical refactors without changing behavior.",
        ("run_lint", "run_typecheck"),
    ),
    (
        "doc_writer",
        "Generate docstrings and README sections from existing code.",
        (),
    ),
    (
        "arch_reviewer",
        "Architectural and module-boundary critique only.",
        ("git_diff",),
    ),
    (
        "perf_reviewer",
        "Performance and complexity critique only.",
        ("git_diff",),
    ),
    (
        "dep_scanner",
        "Inspect dependency manifests for known-vulnerable versions.",
        ("git_diff", "mcp_invoke"),
    ),
    (
        "secret_scanner",
        "Scan diffs for accidental secrets via regex + entropy.",
        ("git_diff",),
    ),
    (
        "coverage_analyst",
        "Identify under-tested modules and suggest a prioritized test list.",
        ("run_tests",),
    ),
    (
        "dedupe_helper",
        "Cluster overlapping findings into single issues before filing.",
        ("gh_issue_create", "gh_label_ensure"),
    ),
)


def _build_registry() -> dict[str, SubAgentSpec]:
    return {
        name: SubAgentSpec(
            name=name,
            description=description,
            prompt=_load_prompt(name),
            tools=tools,
        )
        for name, description, tools in _CATALOG_SEED
    }


SUBAGENT_REGISTRY: Final[dict[str, SubAgentSpec]] = _build_registry()
"""Single source of truth for the 10-entry sub-agent catalog (spec §7.1)."""


# ---------------------------------------------------------------------------
# Per-role lookup
# ---------------------------------------------------------------------------


_ROLE_SUBAGENTS: Final[dict[AgentRole, tuple[str, ...]]] = {
    AgentRole.CLARIFIER: (),
    AgentRole.SPEC_AUTHOR: ("researcher",),
    AgentRole.PLANNER: ("estimator",),
    AgentRole.IMPLEMENTER: ("refactorer", "doc_writer"),
    AgentRole.REVIEWER: ("arch_reviewer", "perf_reviewer"),
    AgentRole.AUDITOR: ("dep_scanner", "secret_scanner"),
    AgentRole.TESTER: ("coverage_analyst",),
    AgentRole.TRIAGER: ("dedupe_helper",),
}


def subagents_for(role: AgentRole) -> tuple[SubAgentSpec, ...]:
    """Return the sub-agent specs registered for a parent role.

    Args:
        role: Parent agentic-node role.

    Returns:
        Immutable tuple of :class:`SubAgentSpec` instances drawn from
        :data:`SUBAGENT_REGISTRY`. Empty for roles with no delegation
        partners (e.g. :attr:`AgentRole.CLARIFIER`).
    """

    return tuple(SUBAGENT_REGISTRY[name] for name in _ROLE_SUBAGENTS[role])


def get_subagents_for_role(role: AgentRole) -> tuple[SubAgentSpec, ...]:
    """Backwards-compatible alias for :func:`subagents_for` (T1 callers)."""

    return subagents_for(role)


# ---------------------------------------------------------------------------
# VFS namespace enforcement (spec §7.2)
# ---------------------------------------------------------------------------


def _require_known_subagent(name: str) -> None:
    if name not in SUBAGENT_REGISTRY:
        raise KeyError(f"unknown sub-agent: {name!r}")


def is_in_subagent_namespace(name: str, path: str) -> bool:
    """Return ``True`` if ``path`` is already under the sub-agent's VFS prefix.

    Args:
        name: Registered sub-agent name.
        path: Candidate VFS path.

    Raises:
        KeyError: If ``name`` is not in :data:`SUBAGENT_REGISTRY`.
    """

    _require_known_subagent(name)
    expected_prefix = f"{_SUBAGENT_PREFIX}{name}/"
    return path.startswith(expected_prefix)


def namespace_vfs_path(name: str, path: str) -> str:
    """Project ``path`` into the sub-agent's mandatory VFS write namespace.

    Spec §7.2 requires every sub-agent write to live beneath
    ``vfs:/subagent/<name>/``. This helper:

    * is a no-op when ``path`` is already namespaced for ``name``;
    * strips a leading ``vfs:/`` or ``/`` and re-roots the rest under
      the namespace;
    * rejects ``..`` traversal segments;
    * rejects paths claimed by a *different* sub-agent's namespace.

    Args:
        name: Registered sub-agent name.
        path: Candidate VFS write path.

    Returns:
        The canonical ``vfs:/subagent/<name>/...`` path.

    Raises:
        KeyError: If ``name`` is not in :data:`SUBAGENT_REGISTRY`.
        ValueError: If ``path`` contains traversal segments, or maps
            to another sub-agent's namespace.
    """

    _require_known_subagent(name)

    if is_in_subagent_namespace(name, path):
        return path

    if path.startswith(_SUBAGENT_PREFIX):
        # Belongs to a different sub-agent — reject rather than rewrite.
        raise ValueError(
            f"path {path!r} lives in another sub-agent's namespace; "
            f"writes for {name!r} must use {_SUBAGENT_PREFIX}{name}/",
        )

    relative = path
    if relative.startswith(_VFS_PREFIX):
        relative = relative[len(_VFS_PREFIX):]
    relative = relative.lstrip("/")

    segments = relative.split("/")
    if any(segment == ".." for segment in segments):
        raise ValueError(
            f"path traversal rejected in sub-agent VFS path: {path!r}",
        )

    return f"{_SUBAGENT_PREFIX}{name}/{relative}"
