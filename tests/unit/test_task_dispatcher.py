"""Tests for the dynamic task DAG dispatcher (Option A — Send fan-out)."""

from __future__ import annotations

from langgraph.types import Send

from flowforge.graph.builder import _route_runnable_tasks
from flowforge.state.models import (
    CapabilityType,
    GraphState,
    ImplementationPlan,
    Task,
    TaskDAG,
    TaskDefinition,
    TaskDependency,
    TaskStatus,
)


def _defn(task_id: str) -> TaskDefinition:
    return TaskDefinition(
        task_id=task_id,
        title=task_id,
        description="d",
        acceptance_checks=["a"],
        estimated_complexity="xs",
        capability_type=CapabilityType.AGENT_ONLY,
        verification_step="pytest",
    )


def _state(
    *task_ids: str,
    edges: list[tuple[str, str]] | None = None,
    completed: list[Task] | None = None,
) -> GraphState:
    plan = ImplementationPlan(
        phases=["p"],
        dag=TaskDAG(
            tasks=[_defn(i) for i in task_ids],
            edges=[
                TaskDependency(from_task_id=a, to_task_id=b)
                for a, b in (edges or [])
            ],
        ),
    )
    return GraphState(
        request="x",
        workdir="/tmp",
        implementation_plan=plan,
        tasks=completed or [],
    )


def test_router_emits_send_per_root_task() -> None:
    state = _state("t1", "t2", edges=[])
    out = _route_runnable_tasks(state)

    assert isinstance(out, list)
    assert len(out) == 2
    assert all(isinstance(s, Send) for s in out)
    sent_ids = {s.arg["current_task_id"] for s in out}
    assert sent_ids == {"t1", "t2"}
    assert all(s.node == "task_node" for s in out)


def test_router_holds_back_dependents_until_predecessor_succeeds() -> None:
    state = _state("t1", "t2", edges=[("t1", "t2")])
    out = _route_runnable_tasks(state)
    assert isinstance(out, list)
    sent_ids = {s.arg["current_task_id"] for s in out}
    assert sent_ids == {"t1"}


def test_router_releases_dependent_after_predecessor_succeeds() -> None:
    plan = ImplementationPlan(
        phases=["p"],
        dag=TaskDAG(
            tasks=[_defn("t1"), _defn("t2")],
            edges=[TaskDependency(from_task_id="t1", to_task_id="t2")],
        ),
    )
    completed = [
        Task(task_id="t1", definition=plan.dag.tasks[0], status=TaskStatus.SUCCEEDED),
    ]
    state = GraphState(
        request="x",
        workdir="/tmp",
        implementation_plan=plan,
        tasks=completed,
    )
    out = _route_runnable_tasks(state)
    assert isinstance(out, list)
    sent_ids = {s.arg["current_task_id"] for s in out}
    assert sent_ids == {"t2"}


def test_router_proceeds_to_quality_gate_when_all_done() -> None:
    plan = ImplementationPlan(
        phases=["p"],
        dag=TaskDAG(tasks=[_defn("t1")], edges=[]),
    )
    completed = [
        Task(task_id="t1", definition=plan.dag.tasks[0], status=TaskStatus.SUCCEEDED),
    ]
    state = GraphState(
        request="x",
        workdir="/tmp",
        implementation_plan=plan,
        tasks=completed,
    )
    assert _route_runnable_tasks(state) == "quality_gate_join"


def test_router_proceeds_to_quality_gate_when_no_plan() -> None:
    state = GraphState(request="x", workdir="/tmp")
    assert _route_runnable_tasks(state) == "quality_gate_join"
