"""Tests for ``flowforge.deep_agents.factory.build_deep_agent`` (T4).

Covers the spec §5.3 contract:

* returns a ``CompiledStateGraph`` for every :class:`AgentRole`;
* rejects ``workdir=None`` with ``ValueError``;
* normalizes ``str`` workdirs to :class:`pathlib.Path`;
* applies a recursion limit via ``.with_config`` (default 50, overridable
  through ``FLOWFORGE_DEEP_AGENT_RECURSION``);
* attaches the role-specific tool allowlist from spec §6;
* attaches the role-specific sub-agents from
  :data:`flowforge.deep_agents.subagents.SUBAGENT_REGISTRY`;
* loads the system prompt from ``instructions/<role>.md``;
* merges ``extra_tools`` after the role's defaults;
* exposes a strictly typed signature (no ``Any``).
"""

from __future__ import annotations

import inspect
import typing
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.tools import tool as lc_tool
from langgraph.graph.state import CompiledStateGraph

from flowforge.deep_agents import AgentRole
from flowforge.deep_agents import factory as factory_module
from flowforge.deep_agents.factory import (
    DEFAULT_RECURSION_LIMIT,
    ROLE_TOOL_ALLOWLIST,
    build_deep_agent,
    tools_for_role,
)
from flowforge.deep_agents.subagents import subagents_for

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_llm() -> FakeListChatModel:
    return FakeListChatModel(responses=["ok"])


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    return tmp_path


# ---------------------------------------------------------------------------
# Spec §6 — per-role tool allowlist (canonical mapping)
# ---------------------------------------------------------------------------

EXPECTED_ROLE_TOOLS: dict[AgentRole, frozenset[str]] = {
    AgentRole.CLARIFIER: frozenset({"mcp_invoke"}),
    AgentRole.SPEC_AUTHOR: frozenset({"web_search", "mcp_invoke"}),
    AgentRole.PLANNER: frozenset({"web_search", "mcp_invoke"}),
    AgentRole.IMPLEMENTER: frozenset(
        {"run_tests", "run_lint", "run_typecheck", "git_status", "mcp_invoke"},
    ),
    AgentRole.REVIEWER: frozenset(
        {"run_lint", "run_typecheck", "git_status", "git_diff", "mcp_invoke"},
    ),
    AgentRole.AUDITOR: frozenset({"git_diff", "web_search", "mcp_invoke"}),
    AgentRole.TESTER: frozenset({"run_tests", "mcp_invoke"}),
    AgentRole.TRIAGER: frozenset(
        {"gh_issue_create", "gh_label_ensure", "mcp_invoke"},
    ),
}


def test_role_tool_allowlist_matches_spec() -> None:
    assert {role: frozenset(names) for role, names in ROLE_TOOL_ALLOWLIST.items()} == (
        EXPECTED_ROLE_TOOLS
    )


# ---------------------------------------------------------------------------
# Capture helper — intercepts ``deepagents.create_deep_agent`` calls
# ---------------------------------------------------------------------------


class _Capture(dict[str, Any]):
    """Records the kwargs the factory passes to ``create_deep_agent``."""


@pytest.fixture
def capture(monkeypatch: pytest.MonkeyPatch) -> _Capture:
    """Replace ``create_deep_agent`` with a stub returning a real graph.

    The stub records its kwargs so tests can assert on tool/sub-agent
    selection without depending on the framework's internal layout.
    """
    capture = _Capture()
    real = factory_module._create_deep_agent

    def stub(**kwargs: object) -> CompiledStateGraph[Any, Any, Any, Any]:
        capture.clear()
        capture.update(kwargs)
        # Strip our (richer) sub-agents to keep the framework happy with
        # the FakeListChatModel and avoid network/tooling for tests.
        return real(
            model=kwargs["model"],  # type: ignore[arg-type]
            tools=[],
            system_prompt="stub",
            subagents=[],
        )

    monkeypatch.setattr(factory_module, "_create_deep_agent", stub)
    return capture


# ---------------------------------------------------------------------------
# Core contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(AgentRole))
def test_returns_compiled_state_graph(
    role: AgentRole,
    fake_llm: FakeListChatModel,
    workdir: Path,
    capture: _Capture,
) -> None:
    graph = build_deep_agent(role=role, llm=fake_llm, workdir=workdir)
    assert isinstance(graph, CompiledStateGraph)
    # Recorded kwargs must mention the role's expected tool names
    actual_tools = {t.name for t in capture["tools"]}
    assert actual_tools == EXPECTED_ROLE_TOOLS[role]


def test_workdir_none_raises_value_error(fake_llm: FakeListChatModel) -> None:
    with pytest.raises(ValueError, match="workdir"):
        build_deep_agent(
            role=AgentRole.CLARIFIER,
            llm=fake_llm,
            workdir=None,  # type: ignore[arg-type]
        )


def test_workdir_string_is_normalized(
    fake_llm: FakeListChatModel,
    tmp_path: Path,
    capture: _Capture,
) -> None:
    # Passing a string is accepted and normalized to Path internally.
    graph = build_deep_agent(
        role=AgentRole.IMPLEMENTER,
        llm=fake_llm,
        workdir=str(tmp_path),  # type: ignore[arg-type]
    )
    assert isinstance(graph, CompiledStateGraph)


def test_recursion_limit_applied(
    fake_llm: FakeListChatModel,
    workdir: Path,
    capture: _Capture,
) -> None:
    graph = build_deep_agent(
        role=AgentRole.CLARIFIER, llm=fake_llm, workdir=workdir,
    )
    assert graph.config["recursion_limit"] == DEFAULT_RECURSION_LIMIT


def test_recursion_limit_env_override(
    fake_llm: FakeListChatModel,
    workdir: Path,
    capture: _Capture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FLOWFORGE_DEEP_AGENT_RECURSION", "17")
    graph = build_deep_agent(
        role=AgentRole.CLARIFIER, llm=fake_llm, workdir=workdir,
    )
    assert graph.config["recursion_limit"] == 17


def test_recursion_limit_env_invalid_falls_back(
    fake_llm: FakeListChatModel,
    workdir: Path,
    capture: _Capture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FLOWFORGE_DEEP_AGENT_RECURSION", "not-an-int")
    with pytest.raises(ValueError, match="FLOWFORGE_DEEP_AGENT_RECURSION"):
        build_deep_agent(
            role=AgentRole.CLARIFIER, llm=fake_llm, workdir=workdir,
        )


# ---------------------------------------------------------------------------
# Sub-agent attachment (spec §7.1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(AgentRole))
def test_subagents_match_role_registry(
    role: AgentRole,
    fake_llm: FakeListChatModel,
    workdir: Path,
    capture: _Capture,
) -> None:
    build_deep_agent(role=role, llm=fake_llm, workdir=workdir)

    expected = {spec.name for spec in subagents_for(role)}
    attached = {sa["name"] for sa in capture["subagents"]}
    assert attached == expected

    # Each attached sub-agent carries its registry prompt verbatim.
    by_name = {spec.name: spec for spec in subagents_for(role)}
    for sa in capture["subagents"]:
        assert sa["system_prompt"] == by_name[sa["name"]].prompt
        assert sa["description"] == by_name[sa["name"]].description


# ---------------------------------------------------------------------------
# System prompt loading
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(AgentRole))
def test_system_prompt_loaded_from_instructions(
    role: AgentRole,
    fake_llm: FakeListChatModel,
    workdir: Path,
    capture: _Capture,
) -> None:
    build_deep_agent(role=role, llm=fake_llm, workdir=workdir)
    expected_path = (
        Path(__file__).resolve().parents[2]
        / "flowforge"
        / "deep_agents"
        / "instructions"
        / f"{role.value}.md"
    )
    expected = expected_path.read_text(encoding="utf-8")
    assert capture["system_prompt"] == expected


def test_missing_instructions_file_raises(
    fake_llm: FakeListChatModel,
    workdir: Path,
    capture: _Capture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        factory_module,
        "_INSTRUCTIONS_DIR",
        Path("/nonexistent-instructions-dir-flowforge"),
    )
    with pytest.raises(FileNotFoundError):
        build_deep_agent(
            role=AgentRole.CLARIFIER, llm=fake_llm, workdir=workdir,
        )


# ---------------------------------------------------------------------------
# Extra tools merging
# ---------------------------------------------------------------------------


def test_extra_tools_merged(
    fake_llm: FakeListChatModel,
    workdir: Path,
    capture: _Capture,
) -> None:
    @lc_tool
    def my_extra(x: int) -> int:
        """Toy extra tool."""
        return x

    build_deep_agent(
        role=AgentRole.CLARIFIER,
        llm=fake_llm,
        workdir=workdir,
        extra_tools=[my_extra],
    )
    names = {t.name for t in capture["tools"]}
    assert "my_extra" in names
    # role defaults are still present
    assert "mcp_invoke" in names


# ---------------------------------------------------------------------------
# Tool wrappers expose the right shape
# ---------------------------------------------------------------------------


def test_tools_for_role_returns_basetool_instances(workdir: Path) -> None:
    from langchain_core.tools.base import BaseTool

    bound = tools_for_role(AgentRole.IMPLEMENTER, workdir=workdir)
    assert {t.name for t in bound} == EXPECTED_ROLE_TOOLS[AgentRole.IMPLEMENTER]
    for t in bound:
        assert isinstance(t, BaseTool)


# ---------------------------------------------------------------------------
# Consecutive-failure stop-signal (anti-loop guard)
# ---------------------------------------------------------------------------


def test_run_tests_stop_signal_after_consecutive_failures(
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Block persists until workdir fingerprint changes."""
    import json
    from flowforge.deep_agents import factory as _fmod

    # Patch the underlying tool to always fail.
    class _FailResult:
        returncode = 1
        stdout = "FAIL"
        stderr = "1 failed"
        duration_ms = 10
        def model_dump_json(self) -> str:
            return json.dumps({"returncode": 1, "stdout": self.stdout,
                               "stderr": self.stderr, "duration_ms": self.duration_ms})

    monkeypatch.setattr(_fmod._ftools, "run_tests", lambda **_: _FailResult())

    bound_tools = tools_for_role(AgentRole.IMPLEMENTER, workdir=workdir)
    rt = next(t for t in bound_tools if t.name == "run_tests")

    # First N calls: real failure result.
    for _ in range(_fmod._MAX_CONSECUTIVE_FAILURES):
        result = json.loads(rt.invoke({}))
        assert result["returncode"] == 1

    # Next call: stop-signal.
    stop = json.loads(rt.invoke({}))
    assert stop["returncode"] == -1
    assert "blocked" in stop["stderr"].lower()
    assert "before calling run_tests" in stop["stderr"]

    # Without a file change, it remains blocked.
    still_blocked = json.loads(rt.invoke({}))
    assert still_blocked["returncode"] == -1

    # Simulate a file change by advancing the fingerprint.
    fps = iter(["a", "a", "a", "a", "b", "b"])
    monkeypatch.setattr(_fmod, "_workdir_fingerprint", lambda _: next(fps))
    unblocked = json.loads(rt.invoke({}))
    assert unblocked["returncode"] == 1  # still failing, but actually ran


def test_run_tests_resets_counter_on_success(
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Counter resets when tests pass and workdir fingerprint changes."""
    import json
    from flowforge.deep_agents import factory as _fmod

    call_count = [0]
    fps = iter(["a", "a", "b", "b", "c", "c"])

    class _Result:
        def __init__(self, rc: int) -> None:
            self.returncode = rc
            self.stdout = ""
            self.stderr = ""
            self.duration_ms = 10
        def model_dump_json(self) -> str:
            return json.dumps({"returncode": self.returncode, "stdout": "",
                               "stderr": "", "duration_ms": 10})

    def _alternating(**_: object) -> _Result:
        call_count[0] += 1
        # fail, fail, pass, fail, fail → should never hit block of 3
        return _Result(0 if call_count[0] == 3 else 1)

    monkeypatch.setattr(_fmod._ftools, "run_tests", _alternating)
    monkeypatch.setattr(_fmod, "_workdir_fingerprint", lambda _: next(fps))

    bound_tools = tools_for_role(AgentRole.IMPLEMENTER, workdir=workdir)
    rt = next(t for t in bound_tools if t.name == "run_tests")

    results = [json.loads(rt.invoke({})) for _ in range(5)]
    # None of the 5 calls should have triggered the stop-signal
    assert all(r["returncode"] != -1 for r in results)


def test_run_tests_stop_signal_after_repeated_success_without_change(
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated successful verifier runs with no file changes are blocked."""
    import json
    from flowforge.deep_agents import factory as _fmod

    class _OkResult:
        returncode = 0
        stdout = "ok"
        stderr = ""
        duration_ms = 10

        def model_dump_json(self) -> str:
            return json.dumps({"returncode": 0, "stdout": self.stdout,
                               "stderr": self.stderr, "duration_ms": self.duration_ms})

    monkeypatch.setattr(_fmod._ftools, "run_tests", lambda **_: _OkResult())
    monkeypatch.setattr(_fmod, "_workdir_fingerprint", lambda _: "same")

    bound_tools = tools_for_role(AgentRole.IMPLEMENTER, workdir=workdir)
    rt = next(t for t in bound_tools if t.name == "run_tests")

    for _ in range(_fmod._MAX_VERIFIER_CALLS_WITHOUT_CHANGE):
        result = json.loads(rt.invoke({}))
        assert result["returncode"] == 0

    stop = json.loads(rt.invoke({}))
    assert stop["returncode"] == -1
    assert "blocked" in stop["stderr"].lower()


def test_run_tests_guard_persists_across_wrapper_rebuild(
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard state survives tools_for_role rebuilds for same workdir."""
    import json
    from flowforge.deep_agents import factory as _fmod

    # Isolate global guard for this test.
    _fmod._VERIFIER_GUARD.clear()

    class _OkResult:
        returncode = 0
        stdout = "ok"
        stderr = ""
        duration_ms = 10

        def model_dump_json(self) -> str:
            return json.dumps({"returncode": 0, "stdout": self.stdout,
                               "stderr": self.stderr, "duration_ms": self.duration_ms})

    monkeypatch.setattr(_fmod._ftools, "run_tests", lambda **_: _OkResult())
    monkeypatch.setattr(_fmod, "_workdir_fingerprint", lambda _: "same")

    # First wrapper instance consumes nearly all no-change budget.
    bound1 = tools_for_role(AgentRole.IMPLEMENTER, workdir=workdir)
    rt1 = next(t for t in bound1 if t.name == "run_tests")
    for _ in range(_fmod._MAX_VERIFIER_CALLS_WITHOUT_CHANGE - 1):
        result = json.loads(rt1.invoke({}))
        assert result["returncode"] == 0

    # New wrapper instance should continue from existing guard state.
    bound2 = tools_for_role(AgentRole.IMPLEMENTER, workdir=workdir)
    rt2 = next(t for t in bound2 if t.name == "run_tests")
    result = json.loads(rt2.invoke({}))
    assert result["returncode"] == 0
    stop = json.loads(rt2.invoke({}))
    assert stop["returncode"] == -1


def test_tools_for_role_workdir_must_be_path() -> None:
    with pytest.raises(TypeError):
        tools_for_role(AgentRole.IMPLEMENTER, workdir="not-a-path")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Strict typing — signature has no ``Any``
# ---------------------------------------------------------------------------


def test_no_any_in_factory_signature() -> None:
    hints = typing.get_type_hints(build_deep_agent)
    for name, hint in hints.items():
        assert hint is not Any, f"parameter {name!r} typed as Any"


def test_factory_signature_shape() -> None:
    sig = inspect.signature(build_deep_agent)
    assert list(sig.parameters) == [
        "role",
        "llm",
        "workdir",
        "todo_seed",
        "extra_tools",
    ]
    # Defaults
    assert sig.parameters["todo_seed"].default is None
    assert sig.parameters["extra_tools"].default is None


def test_default_recursion_limit_is_fifty() -> None:
    # Spec §10 default is 50. Local constant is the source of truth.
    assert DEFAULT_RECURSION_LIMIT == 50  # noqa: PLR2004 - canonical spec value


# ---------------------------------------------------------------------------
# Skills wiring — `.github/skills/` exposed via deepagents `skills=` kwarg
# ---------------------------------------------------------------------------


def test_skills_are_passed_per_role(
    fake_llm: FakeListChatModel,
    workdir: Path,
    capture: _Capture,
) -> None:
    """Each role must surface its declared skill bundle to ``create_deep_agent``.

    The factory mounts ``.github/skills/`` under ``/skills/`` via a
    :class:`CompositeBackend` and forwards a per-role ``skills=[...]``
    list. This is what gives each node access to the matching SKILL.md
    knowledge base (e.g. REVIEWER → code-review-and-quality).
    """
    build_deep_agent(role=AgentRole.REVIEWER, llm=fake_llm, workdir=workdir)
    skills = capture.get("skills")
    backend = capture.get("backend")
    assert skills is not None, "skills= must be passed when bundle is present"
    assert backend is not None, "backend= must be passed alongside skills="
    assert "/skills/code-review-and-quality/" in skills


def test_skills_omitted_when_bundle_missing(
    fake_llm: FakeListChatModel,
    workdir: Path,
    capture: _Capture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """If ``.github/skills/`` is absent, fall back to default backend."""
    monkeypatch.setattr(factory_module, "_SKILLS_ROOT", tmp_path / "missing")
    build_deep_agent(role=AgentRole.REVIEWER, llm=fake_llm, workdir=workdir)
    assert "skills" not in capture
    assert "backend" not in capture
