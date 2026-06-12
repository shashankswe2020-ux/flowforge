"""Per-task (Send fan-out) execution path for ``task_node``."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from flowforge.deep_agents import AgentRole
from flowforge.deep_agents.errors import RecursionLimitExceededError
from flowforge.nodes import task_runner as task_module
from flowforge.nodes.task_runner import task_node
from flowforge.state.models import (
    CapabilityType,
    DeepAgentTrace,
    GraphState,
    ImplementationPlan,
    Task,
    TaskDAG,
    TaskDefinition,
    TaskDependency,
    TaskStatus,
)


def _defn(task_id: str, *, title: str = "") -> TaskDefinition:
    return TaskDefinition(
        task_id=task_id,
        title=title or task_id,
        description="d",
        acceptance_checks=["a"],
        estimated_complexity="xs",
        capability_type=CapabilityType.AGENT_ONLY,
        verification_step="pytest",
    )


def _plan(*ids: str, edges: list[tuple[str, str]] | None = None) -> ImplementationPlan:
    return ImplementationPlan(
        phases=["p"],
        dag=TaskDAG(
            tasks=[_defn(i) for i in ids],
            edges=[
                TaskDependency(from_task_id=a, to_task_id=b)
                for a, b in (edges or [])
            ],
        ),
    )


def _llm_returning(content: dict) -> MagicMock:
    llm = MagicMock()
    resp = MagicMock()
    resp.content = json.dumps(content)
    llm.invoke.return_value = resp
    return llm


def test_per_task_executes_only_named_task(tmp_path: Path) -> None:
    plan = _plan("t1", "t2")
    state = GraphState(
        request="x",
        workdir=str(tmp_path),
        implementation_plan=plan,
        current_task_id="t2",
    )
    llm = _llm_returning({
        "status": "succeeded",
        "artifacts": [{
            "artifact_id": "a",
            "artifact_type": "source",
            "path": "out.py",
            "fingerprint": "f",
            "content": "x = 1\n",
        }],
        "verification_evidence": ["ok"],
    })

    result = task_node(state, llm=llm)

    assert len(result["tasks"]) == 1
    assert result["tasks"][0].task_id == "t2"
    assert result["tasks"][0].status == TaskStatus.SUCCEEDED


def test_per_task_skips_when_predecessor_failed(tmp_path: Path) -> None:
    plan = _plan("t1", "t2", edges=[("t1", "t2")])
    failed_t1 = Task(
        task_id="t1",
        definition=plan.dag.tasks[0],
        status=TaskStatus.FAILED,
        error_message="boom",
    )
    state = GraphState(
        request="x",
        workdir=str(tmp_path),
        implementation_plan=plan,
        tasks=[failed_t1],
        current_task_id="t2",
    )
    llm = MagicMock()

    result = task_node(state, llm=llm)

    assert len(result["tasks"]) == 1
    skipped = result["tasks"][0]
    assert skipped.task_id == "t2"
    assert skipped.status == TaskStatus.SKIPPED
    assert "t1" in (skipped.error_message or "")
    llm.invoke.assert_not_called()


def test_per_task_unknown_id_returns_empty(tmp_path: Path) -> None:
    plan = _plan("t1")
    state = GraphState(
        request="x",
        workdir=str(tmp_path),
        implementation_plan=plan,
        current_task_id="ghost",
    )
    assert task_node(state, llm=MagicMock()) == {"tasks": []}


def test_per_task_deep_recursion_is_reported_as_failed_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan("t1")
    state = GraphState(
        request="x",
        workdir=str(tmp_path),
        implementation_plan=plan,
        current_task_id="t1",
    )

    monkeypatch.setattr(task_module, "resolve_deep_agents_enabled", lambda: True)
    monkeypatch.setattr(task_module, "build_deep_agent", lambda **_: object())

    def _raise_limit(*_: object, **__: object) -> dict[str, object]:
        raise RecursionLimitExceededError(
            "deep agent run for 'task_node' hit recursion limit",
            role=AgentRole.IMPLEMENTER,
            node_name="task_node",
            partial_trace=DeepAgentTrace(
                role=AgentRole.IMPLEMENTER,
                messages_digest="sha256:test",
                tool_invocations=[],
            ),
        )

    monkeypatch.setattr(task_module, "run_deep_agent_bounded", _raise_limit)

    result = task_node(state, llm=MagicMock())

    assert len(result["tasks"]) == 1
    failed = result["tasks"][0]
    assert failed.task_id == "t1"
    assert failed.status == TaskStatus.FAILED
    assert "recursion limit" in (failed.error_message or "").lower()
    assert "task_node:t1" in result["deep_agent_traces"]
