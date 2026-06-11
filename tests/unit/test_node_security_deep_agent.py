"""Deep-agent path tests for ``security_audit_node`` (T7)."""

from __future__ import annotations

import json
from pathlib import Path  # noqa: TC003
from typing import Any

import pytest

from flowforge.deep_agents import AgentRole
from flowforge.deep_agents.subagents import subagents_for
from flowforge.nodes import security_audit as sa_module
from flowforge.nodes.security_audit import security_audit_node
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


def _canned_audit_result() -> dict[str, object]:
    findings = [
        {
            "finding_id": "sec-1",
            "source_node": "security_audit_node",
            "severity": "high",
            "confidence": 0.9,
            "title": "Plaintext token logged",
            "description": "Token written to stdout",
            "file_path": "src/auth.py",
            "suggestion": "Redact secrets before logging",
        },
    ]
    return {
        "messages": [{"role": "user", "content": "audit"}],
        "files": {
            "vfs:/findings/security.json": json.dumps(findings),
            "vfs:/docs/security-audits/security-audit.md": "# Audit\n",
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
        return _canned_audit_result()

    monkeypatch.setattr(sa_module, "build_deep_agent", fake_build)
    monkeypatch.setattr(sa_module, "run_deep_agent_bounded", fake_run)
    monkeypatch.setattr(sa_module, "_commit_audit_to_repo", lambda *a, **k: None)
    monkeypatch.setattr(sa_module, "_create_github_issues", lambda *a, **k: None)
    return captured


class TestDeepAgentDispatch:
    def test_flag_on_dispatches_with_auditor_role(
        self,
        tmp_path: Path,
        deep_flag_on: None,
        patched_deep_agent: dict[str, Any],
    ) -> None:
        del deep_flag_on
        llm = MockLLM(responses=["unused"])
        state = _state(str(tmp_path))

        result = security_audit_node(state, llm=llm)

        assert len(patched_deep_agent["build_calls"]) == 1
        kwargs = patched_deep_agent["build_calls"][0]["kwargs"]
        args = patched_deep_agent["build_calls"][0]["args"]
        role_value = kwargs.get("role") or (args[0] if args else None)
        assert role_value is AgentRole.AUDITOR
        assert "security_findings" in result

    def test_flag_on_returns_findings_from_vfs(
        self,
        tmp_path: Path,
        deep_flag_on: None,
        patched_deep_agent: dict[str, Any],
    ) -> None:
        del deep_flag_on, patched_deep_agent
        llm = MockLLM(responses=["unused"])
        state = _state(str(tmp_path))

        result = security_audit_node(state, llm=llm)

        findings = result["security_findings"]
        assert len(findings) == 1
        assert findings[0].source_node == "security_audit_node"

    def test_flag_on_populates_trace(
        self,
        tmp_path: Path,
        deep_flag_on: None,
        patched_deep_agent: dict[str, Any],
    ) -> None:
        del deep_flag_on, patched_deep_agent
        llm = MockLLM(responses=["unused"])
        state = _state(str(tmp_path))

        result = security_audit_node(state, llm=llm)

        trace = result["deep_agent_traces"]["security_audit_node"]
        assert isinstance(trace, DeepAgentTrace)
        assert trace.role is AgentRole.AUDITOR

    def test_flag_off_uses_legacy_path(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FLOWFORGE_DEEP_AGENTS", "0")
        called: dict[str, bool] = {"build": False}

        def boom(*_a: object, **_k: object) -> None:
            called["build"] = True

        monkeypatch.setattr(sa_module, "build_deep_agent", boom)
        monkeypatch.setattr(sa_module, "_commit_audit_to_repo", lambda *a, **k: None)
        monkeypatch.setattr(sa_module, "_create_github_issues", lambda *a, **k: None)

        llm = MockLLM(responses=[json.dumps({"findings": []})])
        state = _state(str(tmp_path))

        result = security_audit_node(state, llm=llm)

        assert called["build"] is False
        assert "security_findings" in result
        assert "deep_agent_traces" not in result


class TestSubAgentRegistry:
    def test_auditor_role_includes_dep_and_secret_scanners(self) -> None:
        names = {sa.name for sa in subagents_for(AgentRole.AUDITOR)}
        assert {"dep_scanner", "secret_scanner"}.issubset(names)
