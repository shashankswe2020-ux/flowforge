"""Deep-agent path tests for ``test_engineer_node`` (T7)."""

from __future__ import annotations

import json
from pathlib import Path  # noqa: TC003
from typing import Any

import pytest

from flowforge.deep_agents import AgentRole
from flowforge.deep_agents.subagents import subagents_for
from flowforge.nodes import test_engineer as te_module
from flowforge.nodes.test_engineer import test_engineer_node
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


def _canned_test_result() -> dict[str, object]:
    findings = [
        {
            "finding_id": "te-1",
            "source_node": "test_engineer_node",
            "severity": "medium",
            "confidence": 0.7,
            "title": "Missing failure-path test",
            "description": "login() error path uncovered",
            "file_path": "tests/test_auth.py",
            "suggestion": "Add test_login_invalid_credentials",
        },
    ]
    proposed = [
        {
            "task_id": "test-task-1",
            "title": "Add login failure test",
            "description": "Cover invalid-credentials path",
            "acceptance_checks": ["pytest passes"],
            "estimated_complexity": "s",
            "capability_type": "agent_only",
            "verification_step": "pytest",
        },
    ]
    return {
        "messages": [{"role": "user", "content": "review tests"}],
        "files": {
            "vfs:/findings/test.json": json.dumps(findings),
            "vfs:/context/proposed_tasks.json": json.dumps(proposed),
            "vfs:/docs/test-reports/test-report.md": "# Tests\n",
        },
    }


@pytest.fixture
def deep_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWFORGE_DEEP_AGENTS", "1")


@pytest.fixture
def patched_deep_agent(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {"build_calls": [], "run_calls": []}

    def fake_build(*args: object, **kwargs: object) -> object:
        captured["build_calls"].append({"args": args, "kwargs": kwargs})
        return object()

    def fake_run(graph: object, payload: dict[str, object], **kwargs: object) -> dict[str, object]:
        captured["run_calls"].append({"graph": graph, "payload": payload, "kwargs": kwargs})
        return _canned_test_result()

    monkeypatch.setattr(te_module, "build_deep_agent", fake_build)
    monkeypatch.setattr(te_module, "run_deep_agent_bounded", fake_run)
    monkeypatch.setattr(te_module, "_commit_report_to_repo", lambda *a, **k: None)
    monkeypatch.setattr(te_module, "_create_github_issues", lambda *a, **k: None)
    return captured


class TestDeepAgentDispatch:
    def test_flag_on_dispatches_with_tester_role(
        self,
        tmp_path: Path,
        deep_flag_on: None,
        patched_deep_agent: dict[str, Any],
    ) -> None:
        del deep_flag_on
        llm = MockLLM(responses=["unused"])
        state = _state(str(tmp_path))

        result = test_engineer_node(state, llm=llm)

        assert len(patched_deep_agent["build_calls"]) == 1
        kwargs = patched_deep_agent["build_calls"][0]["kwargs"]
        args = patched_deep_agent["build_calls"][0]["args"]
        role_value = kwargs.get("role") or (args[0] if args else None)
        assert role_value is AgentRole.TESTER
        assert "test_findings" in result
        assert "proposed_tasks" in result

    def test_flag_on_returns_findings_and_proposed_tasks(
        self,
        tmp_path: Path,
        deep_flag_on: None,
        patched_deep_agent: dict[str, Any],
    ) -> None:
        del deep_flag_on, patched_deep_agent
        llm = MockLLM(responses=["unused"])
        state = _state(str(tmp_path))

        result = test_engineer_node(state, llm=llm)

        findings = result["test_findings"]
        assert len(findings) == 1
        assert findings[0].source_node == "test_engineer_node"

        proposed = result["proposed_tasks"]
        assert len(proposed) == 1
        assert proposed[0].task_id == "test-task-1"
        assert proposed[0].estimated_complexity == "s"

    def test_flag_on_populates_trace(
        self,
        tmp_path: Path,
        deep_flag_on: None,
        patched_deep_agent: dict[str, Any],
    ) -> None:
        del deep_flag_on, patched_deep_agent
        llm = MockLLM(responses=["unused"])
        state = _state(str(tmp_path))

        result = test_engineer_node(state, llm=llm)

        trace = result["deep_agent_traces"]["test_engineer_node"]
        assert isinstance(trace, DeepAgentTrace)
        assert trace.role is AgentRole.TESTER

    def test_flag_off_uses_legacy_path(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FLOWFORGE_DEEP_AGENTS", "0")
        called: dict[str, bool] = {"build": False}

        def boom(*_a: object, **_k: object) -> None:
            called["build"] = True

        monkeypatch.setattr(te_module, "build_deep_agent", boom)
        monkeypatch.setattr(te_module, "_commit_report_to_repo", lambda *a, **k: None)
        monkeypatch.setattr(te_module, "_create_github_issues", lambda *a, **k: None)

        llm = MockLLM(
            responses=[json.dumps({"findings": [], "proposed_tasks": []})],
        )
        state = _state(str(tmp_path))

        result = test_engineer_node(state, llm=llm)

        assert called["build"] is False
        assert "test_findings" in result
        assert "proposed_tasks" in result
        assert "deep_agent_traces" not in result


class TestSubAgentRegistry:
    def test_tester_role_includes_coverage_analyst(self) -> None:
        names = {sa.name for sa in subagents_for(AgentRole.TESTER)}
        assert "coverage_analyst" in names
