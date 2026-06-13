"""Deep Agent factory — builds a configured Deep Agent per role (T4).

Implements the spec §5.3 contract:

* loads ``instructions/<role>.md`` as the system prompt;
* attaches the role-specific subset of the FlowForge tool library
  (spec §6) wrapped as LangChain tools with ``workdir`` bound;
* attaches the role's named sub-agents from
  :data:`flowforge.deep_agents.subagents.SUBAGENT_REGISTRY` (spec §7.1);
* applies a recursion limit (spec §10 default ``50``, overridable via
  ``FLOWFORGE_DEEP_AGENT_RECURSION``) using
  :meth:`langgraph.graph.state.CompiledStateGraph.with_config`.

The returned :class:`langgraph.graph.state.CompiledStateGraph` is the
artifact each agentic node wrapper invokes (spec §5.4). Wall-clock
timeouts and tool budgets land in T10.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from threading import Lock
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, TypedDict, cast

from deepagents import create_deep_agent as _create_deep_agent
from deepagents.backends import (
    CompositeBackend,
    FilesystemBackend,
    StateBackend,
)
from deepagents.backends.protocol import BackendProtocol  # noqa: TC002 — runtime hints
from deepagents.middleware.subagents import (
    SubAgent as _DeepSubAgent,  # noqa: TC002 — runtime cast target
)
from langchain_core.language_models import BaseChatModel  # noqa: TC002 — runtime hints
from langchain_core.runnables import RunnableConfig  # noqa: TC002 — runtime hints
from langchain_core.tools import tool as _lc_tool
from langchain_core.tools.base import BaseTool
from langgraph.errors import GraphRecursionError
from langgraph.graph.state import CompiledStateGraph  # noqa: TC002 — runtime hints

from flowforge.deep_agents import AgentRole
from flowforge.deep_agents import tools as _ftools
from flowforge.deep_agents.errors import (
    AgentTimeoutError,
    RecursionLimitExceededError,
    ToolBudgetExceededError,
)
from flowforge.deep_agents.subagents import subagents_for
from flowforge.state.models import DeepAgentTrace, ToolInvocationRecord

__all__ = [
    "DEFAULT_RECURSION_LIMIT",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_TOOL_BUDGET",
    "ROLE_TOOL_ALLOWLIST",
    "build_deep_agent",
    "run_deep_agent_bounded",
    "tools_for_role",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_RECURSION_LIMIT: Final[int] = 50
"""Spec §10 default. Overridable via ``FLOWFORGE_DEEP_AGENT_RECURSION``."""

DEFAULT_TIMEOUT_S: Final[int] = 300
"""Spec §10 wall-clock default (seconds). Overridable via
``FLOWFORGE_DEEP_AGENT_TIMEOUT_S``."""

DEFAULT_TOOL_BUDGET: Final[int] = 200
"""Spec §10 per-node tool budget. Overridable via
``FLOWFORGE_DEEP_AGENT_TOOL_BUDGET``."""

_RECURSION_ENV_VAR: Final[str] = "FLOWFORGE_DEEP_AGENT_RECURSION"
_TIMEOUT_ENV_VAR: Final[str] = "FLOWFORGE_DEEP_AGENT_TIMEOUT_S"
_TOOL_BUDGET_ENV_VAR: Final[str] = "FLOWFORGE_DEEP_AGENT_TOOL_BUDGET"

_INSTRUCTIONS_DIR: Path = (
    Path(__file__).resolve().parent / "instructions"
)
"""Module-level so tests can monkeypatch it cleanly."""

_SKILLS_ROOT: Path = (
    Path(__file__).resolve().parent / "skills"
)
"""Skill bundle shipped inside the ``flowforge`` package.

Lives at ``flowforge/deep_agents/skills/`` so it is included in the
wheel via ``[tool.setuptools.package-data]``. The repository keeps a
``.github/skills`` symlink pointing here for IDE / Copilot integrations
that expect skills under ``.github/`` — the runtime always reads from
this canonical package path.

Each subdirectory contains a ``SKILL.md`` with YAML frontmatter that
encodes domain knowledge (code review, security, testing, …). At
agent-build time the relevant subset is mounted under ``/skills/`` via
a :class:`CompositeBackend` and surfaced to the model through the
``skills=`` argument of :func:`deepagents.create_deep_agent`.
"""

_SKILL_MOUNT_PREFIX: Final[str] = "/skills/"

_SKILLS_BY_ROLE: Final[dict[AgentRole, tuple[str, ...]]] = {
    AgentRole.CLARIFIER: (
        "spec-driven-development",
        "idea-refine",
    ),
    AgentRole.SPEC_AUTHOR: (
        "spec-driven-development",
        "source-driven-development",
        "api-and-interface-design",
    ),
    AgentRole.PLANNER: (
        "planning-and-task-breakdown",
        "incremental-implementation",
    ),
    AgentRole.IMPLEMENTER: (
        "incremental-implementation",
        "source-driven-development",
        "test-driven-development",
    ),
    AgentRole.REVIEWER: (
        "code-review-and-quality",
        "code-simplification",
        "debugging-and-error-recovery",
    ),
    AgentRole.AUDITOR: (
        "security-and-hardening",
    ),
    AgentRole.TESTER: (
        "test-driven-development",
        "debugging-and-error-recovery",
    ),
    AgentRole.TRIAGER: (
        "debugging-and-error-recovery",
        "documentation-and-adrs",
    ),
}
"""Per-role skill bundle. Values are subdirectory names under
:data:`_SKILLS_ROOT`. Each name MUST resolve to ``<root>/<name>/SKILL.md``.
"""


# ---------------------------------------------------------------------------
# Bounded execution — recursion / timeout / tool-budget (spec §10.6, T10)
# ---------------------------------------------------------------------------


@dataclass
class _RunBudget:
    """Per-invocation budget tracker for one Deep Agent run."""

    role: AgentRole
    node_name: str
    deadline: float  # ``time.monotonic`` deadline
    remaining_calls: int
    invocations: list[ToolInvocationRecord] = field(default_factory=list)


_BUDGET_VAR: ContextVar[_RunBudget | None] = ContextVar(
    "flowforge_deep_agent_budget", default=None,
)


def _partial_trace(budget: _RunBudget) -> DeepAgentTrace:
    return DeepAgentTrace(
        role=budget.role,
        messages_digest=DeepAgentTrace.digest_messages([]),
        tool_invocations=list(budget.invocations),
    )


def _consume_tool_budget(tool_name: str) -> None:
    """Charge one tool invocation against the active run budget.

    No-op when called outside a :func:`run_deep_agent_bounded` context
    so individual tools remain unit-testable.

    Raises:
        AgentTimeoutError: If the wall-clock deadline has passed.
        ToolBudgetExceededError: If the per-node tool budget is
            exhausted.
    """
    budget = _BUDGET_VAR.get()
    if budget is None:
        return
    if time.monotonic() > budget.deadline:
        raise AgentTimeoutError(
            f"deep agent run for {budget.node_name!r} exceeded wall-clock deadline",
            role=budget.role,
            node_name=budget.node_name,
            partial_trace=_partial_trace(budget),
        )
    if budget.remaining_calls <= 0:
        raise ToolBudgetExceededError(
            f"deep agent run for {budget.node_name!r} exhausted tool budget",
            role=budget.role,
            node_name=budget.node_name,
            partial_trace=_partial_trace(budget),
        )
    budget.remaining_calls -= 1
    budget.invocations.append(ToolInvocationRecord(tool=tool_name, ok=True))


def _extract_subagent_dispatches(messages: object) -> list[ToolInvocationRecord]:
    """Synthesise records for every ``task`` tool dispatch in ``messages``.

    The deepagents-built ``task`` tool is not FlowForge-wrapped, so it
    does not flow through :func:`_consume_tool_budget`. Walking the
    message stream after the run gives every parent → child dispatch a
    :class:`ToolInvocationRecord` whose ``parent`` field carries the
    sub-agent name (spec §7.1, T8 acceptance criterion).
    """
    if not isinstance(messages, list):
        return []
    records: list[ToolInvocationRecord] = []
    for msg in messages:
        tool_calls: object = getattr(msg, "tool_calls", None)
        if tool_calls is None and isinstance(msg, dict):
            tool_calls = msg.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for tc in tool_calls:
            name: object = None
            raw_args: object = None
            if isinstance(tc, dict):
                name = tc.get("name")
                raw_args = tc.get("args")
                if raw_args is None:
                    raw_args = tc.get("arguments")
            else:
                name = getattr(tc, "name", None)
                raw_args = getattr(tc, "args", None)
                if raw_args is None:
                    raw_args = getattr(tc, "arguments", None)
            if name != "task":
                continue
            args: dict[str, object] = raw_args if isinstance(raw_args, dict) else {}
            subagent: object = (
                args.get("subagent_type")
                or args.get("subagent")
                or args.get("name")
            )
            records.append(
                ToolInvocationRecord(
                    tool="task",
                    ok=True,
                    parent=subagent if isinstance(subagent, str) else None,
                ),
            )
    return records


def _resolve_positive_int(env_var: str, default: int) -> int:
    raw = os.environ.get(env_var)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"{env_var} must be a positive integer, got {raw!r}",
        ) from exc
    if value <= 0:
        raise ValueError(
            f"{env_var} must be a positive integer, got {value}",
        )
    return value


def _resolve_timeout_s() -> int:
    return _resolve_positive_int(_TIMEOUT_ENV_VAR, DEFAULT_TIMEOUT_S)


def _resolve_tool_budget() -> int:
    return _resolve_positive_int(_TOOL_BUDGET_ENV_VAR, DEFAULT_TOOL_BUDGET)


def run_deep_agent_bounded(
    graph: CompiledStateGraph,  # type: ignore[type-arg]
    payload: dict[str, object],
    *,
    role: AgentRole,
    node_name: str,
    timeout_s: float | None = None,
    tool_budget: int | None = None,
    invocation_sink: list[ToolInvocationRecord] | None = None,
) -> dict[str, object]:
    """Invoke ``graph`` with wall-clock + tool-budget caps.

    Args:
        graph: A compiled Deep Agent graph (see :func:`build_deep_agent`).
        payload: ``invoke`` input dict.
        role: The agentic-node role (used to label errors and traces).
        node_name: LangGraph node name (used as trace key).
        timeout_s: Wall-clock seconds; defaults to
            :func:`_resolve_timeout_s`.
        tool_budget: Max tool invocations; defaults to
            :func:`_resolve_tool_budget`.
        invocation_sink: Optional list extended with the captured
            :class:`ToolInvocationRecord` entries on completion (and
            also on error paths). Lets wrappers stamp trace metadata
            without exposing the run-budget context var.

    Returns:
        The raw ``graph.invoke`` result.

    Raises:
        AgentTimeoutError: Wall-clock deadline elapsed.
        RecursionLimitExceededError: LangGraph signalled a recursion
            limit (typically translated from
            :class:`langgraph.errors.GraphRecursionError`).
        ToolBudgetExceededError: Tool-invocation budget exhausted.
    """
    resolved_timeout = (
        float(timeout_s) if timeout_s is not None else float(_resolve_timeout_s())
    )
    resolved_budget = (
        tool_budget if tool_budget is not None else _resolve_tool_budget()
    )
    budget = _RunBudget(
        role=role,
        node_name=node_name,
        deadline=time.monotonic() + resolved_timeout,
        remaining_calls=resolved_budget,
    )
    token = _BUDGET_VAR.set(budget)
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        ctx = copy_context()
        future = executor.submit(lambda: ctx.run(graph.invoke, payload))
        try:
            result = future.result(timeout=resolved_timeout)
        except FuturesTimeoutError as exc:
            raise AgentTimeoutError(
                f"deep agent run for {node_name!r} timed out after "
                f"{resolved_timeout:g}s",
                role=role,
                node_name=node_name,
                partial_trace=_partial_trace(budget),
            ) from exc
        except GraphRecursionError as exc:
            raise RecursionLimitExceededError(
                f"deep agent run for {node_name!r} hit recursion limit",
                role=role,
                node_name=node_name,
                partial_trace=_partial_trace(budget),
            ) from exc
        except json.JSONDecodeError:
            # The underlying model can emit a tool call whose JSON
            # arguments are truncated or otherwise malformed; the
            # deepagents/LangChain tool-call parser then raises a
            # ``json.JSONDecodeError`` from deep inside ``graph.invoke``.
            # A single bad tool call must not abort the whole pipeline:
            # return an empty structured result so the node's
            # ``_extract_*`` sees no artifact and falls back to its legacy
            # single-shot path (which does not depend on tool-calling).
            logger.warning(
                "deep agent run for %r produced malformed tool-call JSON; "
                "falling back to legacy path",
                node_name,
            )
            return {"messages": [], "files": {}}
        if not isinstance(result, dict):
            raise TypeError(
                f"graph.invoke must return a dict (got {type(result).__name__})",
            )
        # Spec §7.1 / T8: surface parent → child sub-agent dispatches
        # that the deepagents-built ``task`` tool would otherwise hide.
        budget.invocations.extend(_extract_subagent_dispatches(result.get("messages")))
        return result
    finally:
        # Audit HIGH-2: never block the caller on a wedged worker.
        # ``cancel_futures=True`` cancels not-yet-started futures; an
        # already-running future cannot be cancelled (CPython does not
        # support thread cancellation), but ``wait=False`` ensures the
        # caller is freed regardless. The orphan thread is bounded by
        # the per-subprocess timeout enforced inside ``_run_subprocess``.
        executor.shutdown(wait=False, cancel_futures=True)
        if invocation_sink is not None:
            invocation_sink.extend(budget.invocations)
        _BUDGET_VAR.reset(token)


# ---------------------------------------------------------------------------
# Spec §6 — per-role tool allowlist
# ---------------------------------------------------------------------------


ROLE_TOOL_ALLOWLIST: Final[dict[AgentRole, tuple[str, ...]]] = {
    AgentRole.CLARIFIER: ("mcp_invoke",),
    AgentRole.SPEC_AUTHOR: ("web_search", "mcp_invoke"),
    AgentRole.PLANNER: ("web_search", "mcp_invoke"),
    AgentRole.IMPLEMENTER: (
        "run_tests",
        "run_lint",
        "run_typecheck",
        "git_status",
        "mcp_invoke",
    ),
    AgentRole.REVIEWER: (
        "run_lint",
        "run_typecheck",
        "git_status",
        "git_diff",
        "mcp_invoke",
    ),
    AgentRole.AUDITOR: ("git_diff", "web_search", "mcp_invoke"),
    AgentRole.TESTER: ("run_tests", "mcp_invoke"),
    AgentRole.TRIAGER: ("gh_issue_create", "gh_label_ensure", "mcp_invoke"),
}


# ---------------------------------------------------------------------------
# Tool wrappers — bind ``workdir`` and expose Pydantic-validated args
# ---------------------------------------------------------------------------


_MAX_CONSECUTIVE_FAILURES = 3
"""Stop re-running a verification tool after this many consecutive non-zero exits."""

_MAX_VERIFIER_CALLS_WITHOUT_CHANGE = 3
"""Stop re-running the same verifier when workdir content has not changed."""


class _VerifierState(TypedDict):
    last_fp: str
    consecutive_failures: int
    calls_without_change: int
    blocked: bool


_VERIFIER_GUARD: dict[tuple[str, str], _VerifierState] = {}
_VERIFIER_GUARD_LOCK = Lock()


def _verifier_state_key(workdir: Path, tool_name: str) -> tuple[str, str]:
    return (str(workdir.resolve()), tool_name)


def _get_verifier_state(workdir: Path, tool_name: str) -> _VerifierState:
    key = _verifier_state_key(workdir, tool_name)
    with _VERIFIER_GUARD_LOCK:
        state = _VERIFIER_GUARD.get(key)
        if state is None:
            state = {
                "last_fp": "",
                "consecutive_failures": 0,
                "calls_without_change": 0,
                "blocked": False,
            }
            _VERIFIER_GUARD[key] = state
        return state


def _workdir_fingerprint(workdir: Path) -> str:
    """Return a lightweight fingerprint of user-relevant workdir files.

    Excludes ephemeral caches so verifier loops only unblock when source
    artifacts actually change.
    """
    items: list[str] = []
    if not workdir.exists():
        return ""
    for path in sorted(p for p in workdir.rglob("*") if p.is_file()):
        rel = path.relative_to(workdir).as_posix()
        if rel.startswith((".pytest_cache/", ".mypy_cache/", "node_modules/")):
            continue
        stat = path.stat()
        items.append(f"{rel}:{stat.st_mtime_ns}:{stat.st_size}")
    return hashlib.sha1("\n".join(items).encode("utf-8")).hexdigest()


def _wrap_run_tests(workdir: Path) -> BaseTool:
    @_lc_tool
    def run_tests(path: str | None = None) -> str:
        """Run ``pytest -q`` (optionally scoped to ``path``) inside the workdir.

        If the suite has failed {max} times in a row without any file changes
        between calls, returns a stop-signal instead of running again — the
        agent must write or fix code before retrying.

        Args:
            path: Optional path relative to the workdir to scope the run.
        """
        _consume_tool_budget("run_tests")
        state = _get_verifier_state(workdir, "run_tests")
        fp = _workdir_fingerprint(workdir)
        if fp != state["last_fp"]:
            state["last_fp"] = fp
            state["consecutive_failures"] = 0
            state["calls_without_change"] = 0
            state["blocked"] = False

        if (
            state["blocked"]
            or state["consecutive_failures"] >= _MAX_CONSECUTIVE_FAILURES
            or state["calls_without_change"] >= _MAX_VERIFIER_CALLS_WITHOUT_CHANGE
        ):
            import json as _json
            state["blocked"] = True
            return _json.dumps({
                "returncode": -1,
                "stdout": "",
                "stderr": (
                    f"run_tests blocked after repeated checks with no workdir changes "
                    f"(max {_MAX_VERIFIER_CALLS_WITHOUT_CHANGE} calls) or "
                    f"{_MAX_CONSECUTIVE_FAILURES} consecutive failures. "
                    "Write or fix source/test files before calling run_tests again."
                ),
                "duration_ms": 0,
            })
        result = _ftools.run_tests(workdir=workdir, path=path)
        state["calls_without_change"] += 1
        if result.returncode != 0:
            state["consecutive_failures"] += 1
            if (
                state["consecutive_failures"] >= _MAX_CONSECUTIVE_FAILURES
                or state["calls_without_change"] >= _MAX_VERIFIER_CALLS_WITHOUT_CHANGE
            ):
                state["blocked"] = True
        else:
            state["consecutive_failures"] = 0
            if state["calls_without_change"] >= _MAX_VERIFIER_CALLS_WITHOUT_CHANGE:
                state["blocked"] = True
            else:
                state["blocked"] = False
        return result.model_dump_json()

    return run_tests


def _wrap_run_lint(workdir: Path) -> BaseTool:
    @_lc_tool
    def run_lint() -> str:
        """Run ``ruff check .`` inside the workdir and return the result JSON."""
        _consume_tool_budget("run_lint")
        state = _get_verifier_state(workdir, "run_lint")
        fp = _workdir_fingerprint(workdir)
        if fp != state["last_fp"]:
            state["last_fp"] = fp
            state["consecutive_failures"] = 0
            state["calls_without_change"] = 0
            state["blocked"] = False

        if (
            state["blocked"]
            or state["consecutive_failures"] >= _MAX_CONSECUTIVE_FAILURES
            or state["calls_without_change"] >= _MAX_VERIFIER_CALLS_WITHOUT_CHANGE
        ):
            import json as _json
            state["blocked"] = True
            return _json.dumps({
                "returncode": -1, "stdout": "",
                "stderr": (
                    f"run_lint blocked after repeated checks with no workdir changes "
                    f"(max {_MAX_VERIFIER_CALLS_WITHOUT_CHANGE} calls) or "
                    f"{_MAX_CONSECUTIVE_FAILURES} consecutive failures. "
                    "Fix files before calling run_lint again."
                ),
                "duration_ms": 0,
            })
        result = _ftools.run_lint(workdir=workdir)
        state["calls_without_change"] += 1
        if result.returncode != 0:
            state["consecutive_failures"] += 1
            if (
                state["consecutive_failures"] >= _MAX_CONSECUTIVE_FAILURES
                or state["calls_without_change"] >= _MAX_VERIFIER_CALLS_WITHOUT_CHANGE
            ):
                state["blocked"] = True
        else:
            state["consecutive_failures"] = 0
            if state["calls_without_change"] >= _MAX_VERIFIER_CALLS_WITHOUT_CHANGE:
                state["blocked"] = True
            else:
                state["blocked"] = False
        return result.model_dump_json()

    return run_lint


def _wrap_run_typecheck(workdir: Path) -> BaseTool:
    @_lc_tool
    def run_typecheck() -> str:
        """Run ``mypy .`` inside the workdir and return the result JSON."""
        _consume_tool_budget("run_typecheck")
        state = _get_verifier_state(workdir, "run_typecheck")
        fp = _workdir_fingerprint(workdir)
        if fp != state["last_fp"]:
            state["last_fp"] = fp
            state["consecutive_failures"] = 0
            state["calls_without_change"] = 0
            state["blocked"] = False

        if (
            state["blocked"]
            or state["consecutive_failures"] >= _MAX_CONSECUTIVE_FAILURES
            or state["calls_without_change"] >= _MAX_VERIFIER_CALLS_WITHOUT_CHANGE
        ):
            import json as _json
            state["blocked"] = True
            return _json.dumps({
                "returncode": -1, "stdout": "",
                "stderr": (
                    f"run_typecheck blocked after repeated checks with no workdir changes "
                    f"(max {_MAX_VERIFIER_CALLS_WITHOUT_CHANGE} calls) or "
                    f"{_MAX_CONSECUTIVE_FAILURES} consecutive failures. "
                    "Fix files before calling run_typecheck again."
                ),
                "duration_ms": 0,
            })
        result = _ftools.run_typecheck(workdir=workdir)
        state["calls_without_change"] += 1
        if result.returncode != 0:
            state["consecutive_failures"] += 1
            if (
                state["consecutive_failures"] >= _MAX_CONSECUTIVE_FAILURES
                or state["calls_without_change"] >= _MAX_VERIFIER_CALLS_WITHOUT_CHANGE
            ):
                state["blocked"] = True
        else:
            state["consecutive_failures"] = 0
            if state["calls_without_change"] >= _MAX_VERIFIER_CALLS_WITHOUT_CHANGE:
                state["blocked"] = True
            else:
                state["blocked"] = False
        return result.model_dump_json()

    return run_typecheck


def _wrap_git_status(workdir: Path) -> BaseTool:
    @_lc_tool
    def git_status() -> str:
        """Return ``git status --porcelain`` for the workdir (read-only)."""
        _consume_tool_budget("git_status")
        return _ftools.git_status(workdir=workdir).model_dump_json()

    return git_status


def _wrap_git_diff(workdir: Path) -> BaseTool:
    @_lc_tool
    def git_diff(rev: str = "HEAD") -> str:
        """Return ``git diff <rev>`` for the workdir (read-only).

        Args:
            rev: Git revision to diff against (defaults to ``HEAD``).
        """
        _consume_tool_budget("git_diff")
        return _ftools.git_diff(workdir=workdir, rev=rev).model_dump_json()

    return git_diff


def _wrap_gh_issue_create(workdir: Path) -> BaseTool:
    @_lc_tool
    def gh_issue_create(
        title: str,
        body: str = "",
        labels: list[str] | None = None,
    ) -> str:
        """Create a GitHub issue via ``gh issue create``.

        Args:
            title: Issue title.
            body: Issue body markdown.
            labels: Optional list of labels to attach.
        """
        _consume_tool_budget("gh_issue_create")
        return _ftools.gh_issue_create(
            workdir=workdir,
            title=title,
            body=body,
            labels=tuple(labels or ()),
        ).model_dump_json()

    return gh_issue_create


def _wrap_gh_label_ensure(workdir: Path) -> BaseTool:
    @_lc_tool
    def gh_label_ensure(
        name: str,
        color: str = "ededed",
        description: str = "",
    ) -> str:
        """Ensure a GitHub label exists (idempotent via ``gh label create --force``).

        Args:
            name: Label name.
            color: 6-character hex color (without ``#``).
            description: Optional human-readable description.
        """
        _consume_tool_budget("gh_label_ensure")
        return _ftools.gh_label_ensure(
            workdir=workdir,
            name=name,
            color=color,
            description=description,
        ).model_dump_json()

    return gh_label_ensure


def _wrap_web_search(workdir: Path) -> BaseTool:
    # workdir is unused for web_search but kept in signature for uniformity.
    del workdir

    @_lc_tool
    def web_search(query: str, max_results: int = 5) -> str:
        """Search the web (gated by ``FLOWFORGE_ALLOW_WEB=1``).

        Args:
            query: Free-text search query.
            max_results: Maximum number of results to return (1–25).
        """
        _consume_tool_budget("web_search")
        return _ftools.web_search(
            query=query, max_results=max_results,
        ).model_dump_json()

    return web_search


def _wrap_mcp_invoke(workdir: Path) -> BaseTool:
    del workdir

    @_lc_tool
    def mcp_invoke(tool: str, arguments: dict[str, object]) -> str:
        """Invoke an MCP tool via the registered transport.

        Args:
            tool: MCP tool identifier.
            arguments: Tool-specific arguments dict.
        """
        _consume_tool_budget("mcp_invoke")
        return _ftools.mcp_invoke(
            tool=tool, arguments=arguments,
        ).model_dump_json()

    return mcp_invoke


_ToolFactory = Callable[[Path], BaseTool]


_TOOL_FACTORIES: Final[dict[str, _ToolFactory]] = {
    "run_tests": _wrap_run_tests,
    "run_lint": _wrap_run_lint,
    "run_typecheck": _wrap_run_typecheck,
    "git_status": _wrap_git_status,
    "git_diff": _wrap_git_diff,
    "gh_issue_create": _wrap_gh_issue_create,
    "gh_label_ensure": _wrap_gh_label_ensure,
    "web_search": _wrap_web_search,
    "mcp_invoke": _wrap_mcp_invoke,
}


def tools_for_role(role: AgentRole, *, workdir: Path) -> tuple[BaseTool, ...]:
    """Return LangChain ``BaseTool`` wrappers for ``role`` bound to ``workdir``.

    Args:
        role: Parent agentic-node role.
        workdir: Workdir to bind into every tool's closure.

    Returns:
        Immutable tuple of bound tools, one per name in
        :data:`ROLE_TOOL_ALLOWLIST` for ``role``.

    Raises:
        TypeError: If ``workdir`` is not a :class:`pathlib.Path`.
    """

    if not isinstance(workdir, Path):
        raise TypeError(
            f"workdir must be a pathlib.Path (got {type(workdir).__name__})",
        )
    return tuple(
        _TOOL_FACTORIES[name](workdir) for name in ROLE_TOOL_ALLOWLIST[role]
    )


# ---------------------------------------------------------------------------
# Sub-agent translation
# ---------------------------------------------------------------------------


class _SubAgentDict(TypedDict, total=False):
    """Shape consumed by ``deepagents.create_deep_agent``.

    Mirrors :class:`deepagents.middleware.subagents.SubAgent` — declared
    here for documentation. The factory ``cast``s plain dicts to the
    framework's ``TypedDict`` for the ``subagents=`` argument.
    """

    name: str
    description: str
    system_prompt: str


def _subagent_dicts_for(role: AgentRole) -> list[_DeepSubAgent]:
    return [
        cast(
            "_DeepSubAgent",
            {
                "name": spec.name,
                "description": spec.description,
                "system_prompt": spec.prompt,
            },
        )
        for spec in subagents_for(role)
    ]


# ---------------------------------------------------------------------------
# Recursion limit resolution
# ---------------------------------------------------------------------------


def _resolve_recursion_limit() -> int:
    raw = os.environ.get(_RECURSION_ENV_VAR)
    if raw is None:
        return DEFAULT_RECURSION_LIMIT
    try:
        value = int(raw)
    except ValueError as exc:  # noqa: PERF203
        raise ValueError(
            f"{_RECURSION_ENV_VAR} must be a positive integer, got {raw!r}",
        ) from exc
    if value <= 0:
        raise ValueError(
            f"{_RECURSION_ENV_VAR} must be a positive integer, got {value}",
        )
    return value


# ---------------------------------------------------------------------------
# Instruction loading
# ---------------------------------------------------------------------------


def _load_instructions(role: AgentRole) -> str:
    path = _INSTRUCTIONS_DIR / f"{role.value}.md"
    if not path.is_file():
        raise FileNotFoundError(
            f"missing role instructions file: {path}",
        )
    return path.read_text(encoding="utf-8")


def _build_skill_kwargs(role: AgentRole) -> dict[str, object]:
    """Resolve per-role skill mounts for ``deepagents.create_deep_agent``.

    Returns a dict with ``backend`` and ``skills`` keys when the
    repository's ``.github/skills/`` bundle is present and the role has
    declared skills. Missing skill directories are skipped silently —
    this keeps the factory usable in stripped-down installs (sdist /
    wheel) where ``.github/`` is not packaged. If no skills resolve, an
    empty dict is returned and ``create_deep_agent`` falls back to its
    default ``StateBackend`` with no skills middleware.
    """

    if not _SKILLS_ROOT.is_dir():
        return {}

    declared = _SKILLS_BY_ROLE.get(role, ())
    sources: list[str] = []
    for name in declared:
        skill_md = _SKILLS_ROOT / name / "SKILL.md"
        if skill_md.is_file():
            sources.append(f"{_SKILL_MOUNT_PREFIX}{name}/")

    if not sources:
        return {}

    skills_fs: BackendProtocol = FilesystemBackend(
        root_dir=str(_SKILLS_ROOT),
        virtual_mode=True,
    )
    backend: BackendProtocol = CompositeBackend(
        default=StateBackend(),
        routes={_SKILL_MOUNT_PREFIX: skills_fs},
    )
    return {"backend": backend, "skills": sources}


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def build_deep_agent(
    role: AgentRole,
    llm: BaseChatModel,
    workdir: Path,
    todo_seed: list[str] | None = None,
    extra_tools: Sequence[BaseTool] | None = None,
) -> CompiledStateGraph:  # type: ignore[type-arg]
    """Build a Deep Agent graph for the given ``role``.

    Args:
        role: Which agentic node this graph backs.
        llm: Chat model wired through the existing FlowForge adapter layer.
        workdir: Generated-repo workdir; tools constrain writes here.
            ``str`` is accepted and normalized via ``Path(workdir)``.
            Passing ``None`` raises :class:`ValueError`.
        todo_seed: Optional initial plan; surfaced to the agent through
            graph configuration (``configurable.todo_seed``) for the
            node wrapper to seed into ``write_todos`` at invoke time.
        extra_tools: Additional ``BaseTool`` instances merged after the
            role's defaults.

    Returns:
        A compiled :class:`langgraph.graph.state.CompiledStateGraph` with
        the recursion limit applied via ``.with_config``.

    Raises:
        ValueError: If ``workdir`` is ``None`` or
            ``FLOWFORGE_DEEP_AGENT_RECURSION`` is set to a non-positive
            integer.
        FileNotFoundError: If the role's ``instructions/<role>.md`` is
            missing.
    """

    if workdir is None:
        raise ValueError("workdir is required and may not be None")
    workdir_path = workdir if isinstance(workdir, Path) else Path(workdir)

    system_prompt = _load_instructions(role)
    role_tools = tools_for_role(role, workdir=workdir_path)
    merged_tools: list[BaseTool] = [*role_tools, *(extra_tools or ())]
    sub_agents = _subagent_dicts_for(role)
    recursion_limit = _resolve_recursion_limit()
    skill_kwargs = _build_skill_kwargs(role)

    graph = _create_deep_agent(
        model=llm,
        tools=merged_tools,
        system_prompt=system_prompt,
        subagents=sub_agents,
        **skill_kwargs,
    )

    config: RunnableConfig = {"recursion_limit": recursion_limit}
    if todo_seed is not None:
        config["configurable"] = {"todo_seed": list(todo_seed)}
    return graph.with_config(config)
