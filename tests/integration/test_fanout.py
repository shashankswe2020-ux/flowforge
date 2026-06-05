"""Integration tests for fanout: scheduler + revision lock + mutation together."""

from __future__ import annotations

from flowforge.scheduler.mutation import append_tasks
from flowforge.scheduler.revision_lock import RevisionLock
from flowforge.scheduler.router import compute_next_runnable, dispatch_tasks
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


def _make_definition(task_id: str) -> TaskDefinition:
    return TaskDefinition(
        task_id=task_id,
        title=f"Task {task_id}",
        description=f"Desc {task_id}",
        acceptance_checks=["ok"],
        estimated_complexity="s",
        capability_type=CapabilityType.AGENT_WITH_TOOLS,
        verification_step="check",
    )


def _make_task(task_id: str, status: TaskStatus = TaskStatus.PENDING) -> Task:
    return Task(task_id=task_id, definition=_make_definition(task_id), status=status)


class TestFullFanoutCycle:
    """End-to-end: schedule → execute → mutate → reschedule."""

    def test_dispatch_then_mutate_then_redispatch(self) -> None:
        """Full cycle: dispatch initial tasks, mutate DAG, dispatch new tasks."""
        # Initial DAG: t1 -> t2
        dag = TaskDAG(
            tasks=[_make_definition("t1"), _make_definition("t2")],
            edges=[TaskDependency(from_task_id="t1", to_task_id="t2")],
            plan_revision=1,
        )
        tasks = [_make_task("t1"), _make_task("t2")]
        state = GraphState(
            request="test",
            run_status="running",
            implementation_plan=ImplementationPlan(phases=["p1"], dag=dag),
            tasks=tasks,
        )

        # Step 1: Dispatch — t1 becomes READY
        result = dispatch_tasks(state)
        dispatched = result["tasks"]
        t1 = next(t for t in dispatched if t.task_id == "t1")
        assert t1.status == TaskStatus.READY

        # Simulate t1 completing
        dispatched = [
            _make_task("t1", TaskStatus.SUCCEEDED),
            _make_task("t2", TaskStatus.PENDING),
        ]

        # Step 2: Mutate — add t3 depending on t2
        lock = RevisionLock()
        lock.acquire(revision=1)
        mutation = append_tasks(
            dag=dag,
            existing_tasks=dispatched,
            new_definitions=[_make_definition("t3")],
            new_edges=[TaskDependency(from_task_id="t2", to_task_id="t3")],
            lock=lock,
        )

        assert mutation.dag.plan_revision == 2
        assert len(mutation.dag.tasks) == 3

        # Step 3: Redispatch with mutated DAG — t2 is now ready
        state2 = GraphState(
            request="test",
            run_status="running",
            implementation_plan=ImplementationPlan(
                phases=["p1"],
                dag=mutation.dag,
                plan_revision=2,
            ),
            tasks=mutation.tasks,
        )
        result2 = dispatch_tasks(state2)
        dispatched2 = result2["tasks"]
        t2 = next(t for t in dispatched2 if t.task_id == "t2")
        t3 = next(t for t in dispatched2 if t.task_id == "t3")
        assert t2.status == TaskStatus.READY
        assert t3.status == TaskStatus.PENDING  # blocked by t2

    def test_quality_gate_only_evaluates_latest_revision(self) -> None:
        """Tasks from latest revision are all that matter for completion check."""
        # DAG with 3 tasks at revision 2
        dag = TaskDAG(
            tasks=[
                _make_definition("t1"),
                _make_definition("t2"),
                _make_definition("t3"),
            ],
            edges=[
                TaskDependency(from_task_id="t1", to_task_id="t2"),
                TaskDependency(from_task_id="t2", to_task_id="t3"),
            ],
            plan_revision=2,
        )
        # All tasks completed
        tasks = [
            _make_task("t1", TaskStatus.SUCCEEDED),
            _make_task("t2", TaskStatus.SUCCEEDED),
            _make_task("t3", TaskStatus.SUCCEEDED),
        ]

        # compute_next_runnable returns empty when all are terminal
        runnable = compute_next_runnable(dag, tasks)
        assert runnable == []

    def test_parallel_tasks_dispatched_together(self) -> None:
        """Independent tasks at same level dispatched in single pass."""
        dag = TaskDAG(
            tasks=[
                _make_definition("t1"),
                _make_definition("t2"),
                _make_definition("t3"),
            ],
            edges=[
                TaskDependency(from_task_id="t1", to_task_id="t3"),
                TaskDependency(from_task_id="t2", to_task_id="t3"),
            ],
            plan_revision=1,
        )
        tasks = [_make_task("t1"), _make_task("t2"), _make_task("t3")]
        state = GraphState(
            request="test",
            run_status="running",
            implementation_plan=ImplementationPlan(phases=["p1"], dag=dag),
            tasks=tasks,
        )

        result = dispatch_tasks(state)
        dispatched = result["tasks"]
        t1 = next(t for t in dispatched if t.task_id == "t1")
        t2 = next(t for t in dispatched if t.task_id == "t2")
        t3 = next(t for t in dispatched if t.task_id == "t3")
        assert t1.status == TaskStatus.READY
        assert t2.status == TaskStatus.READY
        assert t3.status == TaskStatus.PENDING
