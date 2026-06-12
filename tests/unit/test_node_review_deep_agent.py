"""Deep-agent path tests for ``code_review_node`` (T7).

Asserts that when the ``FLOWFORGE_DEEP_AGENTS`` flag is enabled the
node dispatches through :func:`build_deep_agent` and
:func:`run_deep_agent_bounded`, returns findings parsed from the
agent's VFS, and populates ``deep_agent_traces`` keyed by node name.
"""

from __future__ import annotations

import json
from pathlib import Path  # noqa: TC003
from typing import Any

import pytest

from flowforge.deep_agents import AgentRole
from flowforge.deep_agents.errors import RecursionLimitExceededError
from flowforge.deep_agents.subagents import subagents_for
from flowforge.nodes import code_review as cr_module
from flowforge.nodes.code_review import code_review_node
from flowforge.state.models import (
    CapabilityType,
    DeepAgentTrace,
    GraphState,
    RunStatus,
    Task,
    TaskArtifact,
    TaskDefinition,
    TaskStatus,
)
from tests.mocks import MockLLM


def _state(workdir: str) -> GraphState:
    task_def = TaskDefinition(
        task_id="t1",
        title="Implement auth",
        description="Add auth module",
        acceptance_checks=["login works"],
        estimated_complexity="m",
        capability_type=CapabilityType.AGENT_WITH_TOOLS,
        verification_step="pytest",
    )
    task = Task(
        task_id="t1",
        definition=task_def,
        status=TaskStatus.SUCCEEDED,
        artifacts=[
            TaskArtifact(
                artifact_id="a1",
                artifact_type="code",
                path="src/auth.py",
                fingerprint="sha256:abc",
                content="def login(): ...\n",
            ),
        ],
    )
    return GraphState(
        request="Build API",
        run_status=RunStatus.RUNNING,
        tasks=[task],
        workdir=workdir,
    )


def _canned_review_result() -> dict[str, object]:
    findings = [
        {
            "finding_id": "cr-001",
            "source_node": "code_review_node",
            "severity": "medium",
            "confidence": 0.85,
            "title": "Missing error handling",
            "description": "login() lacks try/except",
            "file_path": "src/auth.py",
            "line_range": [1, 1],
            "suggestion": "Wrap network calls",
        },
    ]
    return {
        "messages": [
            {"role": "user", "content": "review the workdir"},
            {"role": "assistant", "content": "done"},
        ],
        "files": {
            "vfs:/findings/review.json": json.dumps(findings),
            "vfs:/docs/reviews/code-review.md": "# Review\n",
        },
    }


@pytest.fixture
def deep_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWFORGE_DEEP_AGENTS", "1")


@pytest.fixture
def patched_deep_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Patch ``build_deep_agent`` + ``run_deep_agent_bounded`` in node module."""
    captured: dict[str, Any] = {"build_calls": [], "run_calls": []}

    def fake_build(*args: object, **kwargs: object) -> object:
        captured["build_calls"].append({"args": args, "kwargs": kwargs})
        return object()  # opaque graph stand-in

    def fake_run(graph: object, payload: dict[str, object], **kwargs: object) -> dict[str, object]:
        captured["run_calls"].append(
            {"graph": graph, "payload": payload, "kwargs": kwargs},
        )
        return _canned_review_result()

    monkeypatch.setattr(cr_module, "build_deep_agent", fake_build)
    monkeypatch.setattr(cr_module, "run_deep_agent_bounded", fake_run)
    # Side-effects (git commit + gh issue) are no-ops in tests.
    monkeypatch.setattr(cr_module, "_commit_review_to_repo", lambda *a, **k: None)
    monkeypatch.setattr(cr_module, "_create_github_issues", lambda *a, **k: None)
    return captured


class TestDeepAgentDispatch:
    def test_flag_on_dispatches_to_deep_agent(
        self,
        tmp_path: Path,
        deep_flag_on: None,
        patched_deep_agent: dict[str, Any],
    ) -> None:
        del deep_flag_on  # marker fixture
        llm = MockLLM(responses=["unused"])
        state = _state(str(tmp_path))

        result = code_review_node(state, llm=llm)

        assert len(patched_deep_agent["build_calls"]) == 1
        build_kwargs = patched_deep_agent["build_calls"][0]["kwargs"]
        build_args = patched_deep_agent["build_calls"][0]["args"]
        # role must be REVIEWER (positional or keyword).
        role_value = build_kwargs.get("role")
        if role_value is None and build_args:
            role_value = build_args[0]
        assert role_value is AgentRole.REVIEWER
        # llm must be threaded through.
        assert build_kwargs.get("llm") is llm or (len(build_args) >= 2 and build_args[1] is llm)
        # workdir must be the state's workdir.
        wd = build_kwargs.get("workdir")
        if wd is None and len(build_args) >= 3:
            wd = build_args[2]
        assert str(wd) == str(tmp_path)

        assert "review_findings" in result

    def test_flag_on_returns_findings_from_vfs(
        self,
        tmp_path: Path,
        deep_flag_on: None,
        patched_deep_agent: dict[str, Any],
    ) -> None:
        del deep_flag_on, patched_deep_agent
        llm = MockLLM(responses=["unused"])
        state = _state(str(tmp_path))

        result = code_review_node(state, llm=llm)

        findings = result["review_findings"]
        assert len(findings) == 1
        assert findings[0].finding_id == "cr-001"
        assert findings[0].source_node == "code_review_node"

    def test_flag_on_populates_deep_agent_trace(
        self,
        tmp_path: Path,
        deep_flag_on: None,
        patched_deep_agent: dict[str, Any],
    ) -> None:
        del deep_flag_on, patched_deep_agent
        llm = MockLLM(responses=["unused"])
        state = _state(str(tmp_path))

        result = code_review_node(state, llm=llm)

        traces = result["deep_agent_traces"]
        assert "code_review_node" in traces
        trace = traces["code_review_node"]
        assert isinstance(trace, DeepAgentTrace)
        assert trace.role is AgentRole.REVIEWER
        # Digest must match the canned messages.
        expected_digest = DeepAgentTrace.digest_messages(
            _canned_review_result()["messages"],  # type: ignore[arg-type]
        )
        assert trace.messages_digest == expected_digest

    def test_flag_off_uses_legacy_path(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Ensure flag is unset.
        monkeypatch.setenv("FLOWFORGE_DEEP_AGENTS", "0")
        # Forbid deep-agent dispatch.
        sentinel_called: dict[str, bool] = {"build": False}

        def boom(*_a: object, **_k: object) -> None:
            sentinel_called["build"] = True

        monkeypatch.setattr(cr_module, "build_deep_agent", boom)
        monkeypatch.setattr(cr_module, "_commit_review_to_repo", lambda *a, **k: None)
        monkeypatch.setattr(cr_module, "_create_github_issues", lambda *a, **k: None)

        llm = MockLLM(responses=[json.dumps({"findings": []})])
        state = _state(str(tmp_path))

        result = code_review_node(state, llm=llm)

        assert sentinel_called["build"] is False
        assert "review_findings" in result
        assert "deep_agent_traces" not in result


class TestSubAgentRegistry:
    """Sanity: the role advertises the sub-agents the spec requires."""

    def test_reviewer_role_includes_arch_and_perf(self) -> None:
        names = {sa.name for sa in subagents_for(AgentRole.REVIEWER)}
        assert {"arch_reviewer", "perf_reviewer"}.issubset(names)


class TestDeepPathRendersMarkdown:
    """Regression: deep wrapper must hand `_commit_review_to_repo` a complete metadata dict.

    Earlier the wrapper passed ``{\"summary\": \"\", \"categorized\": {}}``,
    which crashed ``_render_review_markdown`` on ``metadata['verdict']``.
    """

    def test_commit_helper_runs_without_keyerror(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FLOWFORGE_DEEP_AGENTS", "1")

        def fake_build(*_a: object, **_k: object) -> object:
            return object()

        def fake_run(*_a: object, **_k: object) -> dict[str, object]:
            return _canned_review_result()

        monkeypatch.setattr(cr_module, "build_deep_agent", fake_build)
        monkeypatch.setattr(cr_module, "run_deep_agent_bounded", fake_run)
        # Only stub the network-touching gh issue path; let the markdown +
        # git commit helper run for real (git failures are caught internally).
        monkeypatch.setattr(cr_module, "_create_github_issues", lambda *a, **k: None)

        llm = MockLLM(responses=["unused"])
        state = _state(str(tmp_path))

        result = code_review_node(state, llm=llm)

        # Markdown must have been written to tmp_path/docs/reviews/.
        review_files = list((tmp_path / "docs" / "reviews").glob("*.md"))
        assert len(review_files) == 1
        text = review_files[0].read_text(encoding="utf-8")
        assert "Verdict" in text
        assert result["review_findings"][0].source_node == "code_review_node"


class TestDeepPathLimitFallback:
    def test_recursion_limit_returns_trace_without_raising(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FLOWFORGE_DEEP_AGENTS", "1")

        monkeypatch.setattr(cr_module, "build_deep_agent", lambda *a, **k: object())

        def _raise_limit(*_a: object, **_k: object) -> dict[str, object]:
            raise RecursionLimitExceededError(
                "deep agent run for 'code_review_node' hit recursion limit",
                role=AgentRole.REVIEWER,
                node_name="code_review_node",
                partial_trace=DeepAgentTrace(
                    role=AgentRole.REVIEWER,
                    messages_digest="sha256:test",
                    tool_invocations=[],
                ),
            )

        monkeypatch.setattr(cr_module, "run_deep_agent_bounded", _raise_limit)
        monkeypatch.setattr(cr_module, "_create_github_issues", lambda *a, **k: None)

        llm = MockLLM(responses=["unused"])
        state = _state(str(tmp_path))

        result = code_review_node(state, llm=llm)

        assert result["review_findings"] == []
        assert "code_review_node" in result["deep_agent_traces"]

        review_files = list((tmp_path / "docs" / "reviews").glob("*.md"))
        assert len(review_files) == 1
        text = review_files[0].read_text(encoding="utf-8")
        assert "recursion limit" in text.lower()
