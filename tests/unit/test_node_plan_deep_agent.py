"""Deep-agent path tests for ``plan_node`` (T8)."""

from __future__ import annotations

import json
from pathlib import Path  # noqa: TC003
from typing import Any

import pytest

from flowforge.deep_agents import AgentRole
from flowforge.nodes import plan as plan_module
from flowforge.nodes.plan import plan_node
from flowforge.state.models import (
    DeepAgentTrace,
    GraphState,
    ImplementationPlan,
    RunStatus,
    SpecOutput,
)
from tests.mocks import MockLLM


def _state(workdir: str) -> GraphState:
    spec = SpecOutput(
        artifact_path="docs/spec/whoop-mcp.md",
        summary="MCP server for WHOOP API.",
        objective="Provide tools.",
        target_users="AI developers",
        acceptance_criteria=["All tools work"],
        tech_stack=["TypeScript"],
        commands={"build": "npm run build"},
    )
    return GraphState(
        request="Build a TypeScript MCP server for the WHOOP API.",
        run_status=RunStatus.RUNNING,
        spec=spec,
        workdir=workdir,
    )


def _canned_plan_payload() -> dict[str, Any]:
    return {
        "phases": ["scaffold", "auth", "tools"],
        "tasks": [
            {
                "task_id": "t1",
                "title": "Scaffold project",
                "description": "Initialize TypeScript project.",
                "acceptance_checks": ["package.json exists"],
                "estimated_complexity": "s",
                "capability_type": "agent_only",
                "verification_step": "test -f package.json",
            },
            {
                "task_id": "t2",
                "title": "Implement OAuth",
                "description": "Add authorization-code flow.",
                "acceptance_checks": ["Tokens persisted at 0600"],
                "estimated_complexity": "m",
                "capability_type": "agent_with_tools",
                "verification_step": "npm test -- auth",
            },
        ],
        "edges": [{"from_task_id": "t1", "to_task_id": "t2"}],
        "plan_revision": 1,
    }


def _canned_result(payload: dict[str, Any] | None = None) -> dict[str, object]:
    body = json.dumps(payload if payload is not None else _canned_plan_payload())
    return {
        "messages": [{"role": "user", "content": "plan"}],
        "files": {"vfs:/context/plan_output.json": body},
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
        return _canned_result()

    monkeypatch.setattr(plan_module, "build_deep_agent", fake_build)
    monkeypatch.setattr(plan_module, "run_deep_agent_bounded", fake_run)
    monkeypatch.setattr(plan_module, "_commit_plan_to_repo", lambda *a, **k: None)
    return captured


class TestDeepAgentDispatch:
    def test_flag_on_dispatches_with_planner_role(
        self,
        tmp_path: Path,
        deep_flag_on: None,
        patched_deep_agent: dict[str, Any],
    ) -> None:
        del deep_flag_on
        llm = MockLLM(responses=["unused"])
        state = _state(str(tmp_path))

        result = plan_node(state, llm=llm)

        assert len(patched_deep_agent["build_calls"]) == 1
        kwargs = patched_deep_agent["build_calls"][0]["kwargs"]
        assert kwargs.get("role") is AgentRole.PLANNER
        assert "implementation_plan" in result

    def test_flag_on_builds_valid_dag(
        self,
        tmp_path: Path,
        deep_flag_on: None,
        patched_deep_agent: dict[str, Any],
    ) -> None:
        del deep_flag_on, patched_deep_agent
        llm = MockLLM(responses=["unused"])
        state = _state(str(tmp_path))

        result = plan_node(state, llm=llm)

        plan = result["implementation_plan"]
        assert isinstance(plan, ImplementationPlan)
        assert len(plan.dag.tasks) == 2
        assert {t.task_id for t in plan.dag.tasks} == {"t1", "t2"}
        assert len(plan.dag.edges) == 1
        assert result["run_status"] is RunStatus.RUNNING

    def test_flag_on_calls_commit_helper(
        self,
        tmp_path: Path,
        deep_flag_on: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        del deep_flag_on
        commit_calls: list[Any] = []
        monkeypatch.setattr(plan_module, "build_deep_agent", lambda *a, **k: object())
        monkeypatch.setattr(
            plan_module,
            "run_deep_agent_bounded",
            lambda *a, **k: _canned_result(),
        )
        monkeypatch.setattr(
            plan_module,
            "_commit_plan_to_repo",
            lambda parsed, plan, state: commit_calls.append((parsed, plan, state)),
        )

        llm = MockLLM(responses=["unused"])
        state = _state(str(tmp_path))

        plan_node(state, llm=llm)

        assert len(commit_calls) == 1
        _, plan, _ = commit_calls[0]
        assert isinstance(plan, ImplementationPlan)

    def test_flag_on_populates_trace(
        self,
        tmp_path: Path,
        deep_flag_on: None,
        patched_deep_agent: dict[str, Any],
    ) -> None:
        del deep_flag_on, patched_deep_agent
        llm = MockLLM(responses=["unused"])
        state = _state(str(tmp_path))

        result = plan_node(state, llm=llm)

        trace = result["deep_agent_traces"]["plan_node"]
        assert isinstance(trace, DeepAgentTrace)
        assert trace.role is AgentRole.PLANNER
        assert trace.tool_invocations == []
        assert "vfs:/context/plan_output.json" in trace.vfs_keys

    def test_flag_off_uses_legacy_path(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("FLOWFORGE_DEEP_AGENTS", raising=False)
        called: dict[str, bool] = {"build": False}

        def boom(*_a: object, **_k: object) -> None:
            called["build"] = True

        monkeypatch.setattr(plan_module, "build_deep_agent", boom)
        monkeypatch.setattr(plan_module, "_commit_plan_to_repo", lambda *a, **k: None)

        legacy = json.dumps(_canned_plan_payload())
        llm = MockLLM(responses=[legacy])
        state = _state(str(tmp_path))

        result = plan_node(state, llm=llm)

        assert called["build"] is False
        assert "implementation_plan" in result
        assert "deep_agent_traces" not in result
