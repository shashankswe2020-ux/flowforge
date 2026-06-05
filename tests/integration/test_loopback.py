"""Integration tests for quality loopback with iteration cap."""

from __future__ import annotations

import pytest

from src.graph.loopback import (
    MAX_QUALITY_ITERATIONS,
    LoopbackDecision,
    LoopbackExceededError,
    compute_delta_scope,
    decide_loopback,
    execute_loopback,
)
from src.state.models import (
    CapabilityType,
    GraphState,
    ImplementationPlan,
    RunStatus,
    Task,
    TaskDAG,
    TaskDefinition,
    TaskDependency,
    TaskStatus,
)


def _make_dag(
    tasks: list[TaskDefinition] | None = None,
    edges: list[TaskDependency] | None = None,
) -> TaskDAG:
    default_tasks = [
        TaskDefinition(
            task_id="t1",
            title="Task 1",
            description="Do thing 1",
            acceptance_checks=["check"],
            estimated_complexity="s",
            capability_type=CapabilityType.AGENT_WITH_TOOLS,
            verification_step="pytest",
        ),
        TaskDefinition(
            task_id="t2",
            title="Task 2",
            description="Do thing 2",
            acceptance_checks=["check"],
            estimated_complexity="s",
            capability_type=CapabilityType.AGENT_WITH_TOOLS,
            verification_step="pytest",
        ),
    ]
    return TaskDAG(
        tasks=tasks or default_tasks,
        edges=edges or [TaskDependency(from_task_id="t1", to_task_id="t2")],
        plan_revision=1,
    )


def _make_state(
    *,
    quality_iteration: int = 0,
    proposed_tasks: list[TaskDefinition] | None = None,
    dag: TaskDAG | None = None,
) -> GraphState:
    d = dag or _make_dag()
    return GraphState(
        request="Build something",
        run_status=RunStatus.RUNNING,
        implementation_plan=ImplementationPlan(phases=["p1"], dag=d, plan_revision=d.plan_revision),
        tasks=[
            Task(
                task_id=td.task_id,
                definition=td,
                status=TaskStatus.SUCCEEDED,
            )
            for td in d.tasks
        ],
        quality_iteration=quality_iteration,
        proposed_tasks=proposed_tasks or [],
    )


class TestDecideLoopback:
    """decide_loopback determines whether to re-enter the task fanout."""

    def test_no_proposed_tasks_means_no_loopback(self) -> None:
        state = _make_state(proposed_tasks=[])
        decision = decide_loopback(state)
        assert decision == LoopbackDecision.CONTINUE

    def test_proposed_tasks_triggers_loopback(self) -> None:
        proposed = [
            TaskDefinition(
                task_id="t-new-1",
                title="New test",
                description="Add tests",
                acceptance_checks=["passes"],
                estimated_complexity="s",
                capability_type=CapabilityType.AGENT_WITH_TOOLS,
                verification_step="pytest",
            ),
        ]
        state = _make_state(proposed_tasks=proposed)
        decision = decide_loopback(state)
        assert decision == LoopbackDecision.LOOPBACK

    def test_at_cap_raises_exceeded(self) -> None:
        proposed = [
            TaskDefinition(
                task_id="t-new-1",
                title="New test",
                description="Add tests",
                acceptance_checks=["passes"],
                estimated_complexity="s",
                capability_type=CapabilityType.AGENT_WITH_TOOLS,
                verification_step="pytest",
            ),
        ]
        state = _make_state(quality_iteration=MAX_QUALITY_ITERATIONS, proposed_tasks=proposed)
        with pytest.raises(LoopbackExceededError):
            decide_loopback(state)

    def test_cap_is_three(self) -> None:
        assert MAX_QUALITY_ITERATIONS == 3


class TestComputeDeltaScope:
    """compute_delta_scope identifies tasks affected by changes."""

    def test_new_tasks_in_delta(self) -> None:
        """Newly proposed tasks are in the delta scope."""
        dag = _make_dag()
        new_task_ids = ["t-new-1"]
        delta = compute_delta_scope(dag, new_task_ids)
        assert "t-new-1" in delta

    def test_transitive_dependents_included(self) -> None:
        """Tasks depending on changed tasks are in scope."""
        tasks = [
            TaskDefinition(
                task_id=f"t{i}",
                title=f"Task {i}",
                description=f"Desc {i}",
                acceptance_checks=["check"],
                estimated_complexity="s",
                capability_type=CapabilityType.AGENT_WITH_TOOLS,
                verification_step="pytest",
            )
            for i in range(1, 4)
        ]
        edges = [
            TaskDependency(from_task_id="t1", to_task_id="t2"),
            TaskDependency(from_task_id="t2", to_task_id="t3"),
        ]
        dag = TaskDAG(tasks=tasks, edges=edges, plan_revision=1)
        # t1 changed → t2 and t3 should be in delta
        delta = compute_delta_scope(dag, ["t1"])
        assert "t1" in delta
        assert "t2" in delta
        assert "t3" in delta

    def test_unrelated_tasks_excluded(self) -> None:
        """Tasks not depending on changes are excluded."""
        tasks = [
            TaskDefinition(
                task_id=f"t{i}",
                title=f"Task {i}",
                description=f"Desc {i}",
                acceptance_checks=["check"],
                estimated_complexity="s",
                capability_type=CapabilityType.AGENT_WITH_TOOLS,
                verification_step="pytest",
            )
            for i in range(1, 4)
        ]
        edges = [TaskDependency(from_task_id="t1", to_task_id="t2")]
        dag = TaskDAG(tasks=tasks, edges=edges, plan_revision=1)
        # t1 changed → t3 (no dependency) should NOT be in delta
        delta = compute_delta_scope(dag, ["t1"])
        assert "t3" not in delta


class TestExecuteLoopback:
    """execute_loopback mutates state for re-entry into task_fanout_router."""

    def test_increments_quality_iteration(self) -> None:
        proposed = [
            TaskDefinition(
                task_id="t-new-1",
                title="New test",
                description="Add tests",
                acceptance_checks=["passes"],
                estimated_complexity="s",
                capability_type=CapabilityType.AGENT_WITH_TOOLS,
                verification_step="pytest",
            ),
        ]
        state = _make_state(proposed_tasks=proposed, quality_iteration=0)
        result = execute_loopback(state)
        assert result["quality_iteration"] == 1

    def test_clears_proposed_tasks(self) -> None:
        proposed = [
            TaskDefinition(
                task_id="t-new-1",
                title="New test",
                description="Add tests",
                acceptance_checks=["passes"],
                estimated_complexity="s",
                capability_type=CapabilityType.AGENT_WITH_TOOLS,
                verification_step="pytest",
            ),
        ]
        state = _make_state(proposed_tasks=proposed)
        result = execute_loopback(state)
        assert result["proposed_tasks"] == []

    def test_appends_new_tasks_to_state(self) -> None:
        proposed = [
            TaskDefinition(
                task_id="t-new-1",
                title="New test",
                description="Add tests",
                acceptance_checks=["passes"],
                estimated_complexity="s",
                capability_type=CapabilityType.AGENT_WITH_TOOLS,
                verification_step="pytest",
            ),
        ]
        state = _make_state(proposed_tasks=proposed)
        result = execute_loopback(state)
        task_ids = [t.task_id for t in result["tasks"]]
        assert "t-new-1" in task_ids

    def test_bumps_plan_revision(self) -> None:
        proposed = [
            TaskDefinition(
                task_id="t-new-1",
                title="New test",
                description="Add tests",
                acceptance_checks=["passes"],
                estimated_complexity="s",
                capability_type=CapabilityType.AGENT_WITH_TOOLS,
                verification_step="pytest",
            ),
        ]
        state = _make_state(proposed_tasks=proposed)
        result = execute_loopback(state)
        assert result["implementation_plan"].dag.plan_revision == 2
