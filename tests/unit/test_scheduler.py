"""Unit tests for DAG scheduler — parallel task dispatch."""

from __future__ import annotations

from src.scheduler.router import compute_next_runnable, dispatch_tasks
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


def _make_definition(task_id: str) -> TaskDefinition:
    """Create a minimal TaskDefinition."""
    return TaskDefinition(
        task_id=task_id,
        title=f"Task {task_id}",
        description=f"Description for {task_id}",
        acceptance_checks=["check passes"],
        estimated_complexity="s",
        capability_type=CapabilityType.AGENT_WITH_TOOLS,
        verification_step=f"pytest tests/test_{task_id}.py",
    )


def _make_task(task_id: str, status: TaskStatus = TaskStatus.PENDING) -> Task:
    """Create a minimal Task with given status."""
    return Task(task_id=task_id, definition=_make_definition(task_id), status=status)


def _linear_dag() -> TaskDAG:
    """t1 -> t2 -> t3 (linear chain)."""
    return TaskDAG(
        tasks=[_make_definition("t1"), _make_definition("t2"), _make_definition("t3")],
        edges=[
            TaskDependency(from_task_id="t1", to_task_id="t2"),
            TaskDependency(from_task_id="t2", to_task_id="t3"),
        ],
    )


def _parallel_dag() -> TaskDAG:
    """t1 and t2 are independent, t3 depends on both."""
    return TaskDAG(
        tasks=[
            _make_definition("t1"),
            _make_definition("t2"),
            _make_definition("t3"),
        ],
        edges=[
            TaskDependency(from_task_id="t1", to_task_id="t3"),
            TaskDependency(from_task_id="t2", to_task_id="t3"),
        ],
    )


def _diamond_dag() -> TaskDAG:
    """t1 -> t2, t1 -> t3, t2 -> t4, t3 -> t4."""
    return TaskDAG(
        tasks=[_make_definition(f"t{i}") for i in range(1, 5)],
        edges=[
            TaskDependency(from_task_id="t1", to_task_id="t2"),
            TaskDependency(from_task_id="t1", to_task_id="t3"),
            TaskDependency(from_task_id="t2", to_task_id="t4"),
            TaskDependency(from_task_id="t3", to_task_id="t4"),
        ],
    )


class TestComputeNextRunnable:
    """compute_next_runnable identifies tasks ready for execution."""

    def test_no_predecessors_are_immediately_runnable(self) -> None:
        """Tasks with no incoming edges are runnable from the start."""
        dag = _parallel_dag()
        tasks = [_make_task("t1"), _make_task("t2"), _make_task("t3")]
        runnable = compute_next_runnable(dag, tasks)
        assert set(runnable) == {"t1", "t2"}

    def test_linear_chain_only_first_is_runnable(self) -> None:
        """In a linear chain, only the first task is runnable initially."""
        dag = _linear_dag()
        tasks = [_make_task("t1"), _make_task("t2"), _make_task("t3")]
        runnable = compute_next_runnable(dag, tasks)
        assert runnable == ["t1"]

    def test_predecessor_succeeded_unlocks_next(self) -> None:
        """When predecessor succeeds, next task becomes runnable."""
        dag = _linear_dag()
        tasks = [
            _make_task("t1", TaskStatus.SUCCEEDED),
            _make_task("t2"),
            _make_task("t3"),
        ]
        runnable = compute_next_runnable(dag, tasks)
        assert runnable == ["t2"]

    def test_predecessor_failed_unlocks_next(self) -> None:
        """Failed is a terminal state — unlocks dependents."""
        dag = _linear_dag()
        tasks = [
            _make_task("t1", TaskStatus.FAILED),
            _make_task("t2"),
            _make_task("t3"),
        ]
        runnable = compute_next_runnable(dag, tasks)
        assert runnable == ["t2"]

    def test_predecessor_running_blocks_next(self) -> None:
        """Non-terminal predecessor blocks dependents."""
        dag = _linear_dag()
        tasks = [
            _make_task("t1", TaskStatus.RUNNING),
            _make_task("t2"),
            _make_task("t3"),
        ]
        runnable = compute_next_runnable(dag, tasks)
        assert runnable == []

    def test_diamond_both_paths_must_complete(self) -> None:
        """Diamond join: t4 only runnable when both t2 and t3 are terminal."""
        dag = _diamond_dag()
        tasks = [
            _make_task("t1", TaskStatus.SUCCEEDED),
            _make_task("t2", TaskStatus.SUCCEEDED),
            _make_task("t3", TaskStatus.RUNNING),
            _make_task("t4"),
        ]
        runnable = compute_next_runnable(dag, tasks)
        assert runnable == []

    def test_diamond_all_complete_unlocks_join(self) -> None:
        """Diamond join: t4 runnable when both t2 and t3 are terminal."""
        dag = _diamond_dag()
        tasks = [
            _make_task("t1", TaskStatus.SUCCEEDED),
            _make_task("t2", TaskStatus.SUCCEEDED),
            _make_task("t3", TaskStatus.SUCCEEDED),
            _make_task("t4"),
        ]
        runnable = compute_next_runnable(dag, tasks)
        assert runnable == ["t4"]

    def test_already_running_tasks_excluded(self) -> None:
        """Tasks already in RUNNING/READY state are not re-dispatched."""
        dag = _parallel_dag()
        tasks = [
            _make_task("t1", TaskStatus.RUNNING),
            _make_task("t2"),
            _make_task("t3"),
        ]
        runnable = compute_next_runnable(dag, tasks)
        assert runnable == ["t2"]

    def test_already_succeeded_tasks_excluded(self) -> None:
        """Completed tasks are not returned."""
        dag = _parallel_dag()
        tasks = [
            _make_task("t1", TaskStatus.SUCCEEDED),
            _make_task("t2", TaskStatus.SUCCEEDED),
            _make_task("t3"),
        ]
        runnable = compute_next_runnable(dag, tasks)
        assert runnable == ["t3"]

    def test_deterministic_output_order(self) -> None:
        """Same input always produces same output order (sorted by task_id)."""
        dag = _parallel_dag()
        tasks = [_make_task("t2"), _make_task("t1"), _make_task("t3")]
        r1 = compute_next_runnable(dag, tasks)
        r2 = compute_next_runnable(dag, tasks)
        assert r1 == r2
        assert r1 == sorted(r1)


class TestDispatchTasks:
    """dispatch_tasks transitions tasks and updates state."""

    def test_dispatches_pending_to_ready(self) -> None:
        """Pending tasks are transitioned to READY."""
        dag = _parallel_dag()
        tasks = [_make_task("t1"), _make_task("t2"), _make_task("t3")]
        state = GraphState(
            request="test",
            run_status=RunStatus.RUNNING,
            implementation_plan=ImplementationPlan(
                phases=["p1"],
                dag=dag,
            ),
            tasks=tasks,
        )
        result = dispatch_tasks(state)
        updated_tasks = result["tasks"]
        t1 = next(t for t in updated_tasks if t.task_id == "t1")
        t2 = next(t for t in updated_tasks if t.task_id == "t2")
        t3 = next(t for t in updated_tasks if t.task_id == "t3")
        assert t1.status == TaskStatus.READY
        assert t2.status == TaskStatus.READY
        assert t3.status == TaskStatus.PENDING  # blocked by t1, t2

    def test_retry_increments_counter(self) -> None:
        """Failed tasks eligible for retry get retry_count incremented."""
        dag = _linear_dag()
        tasks = [
            _make_task("t1", TaskStatus.FAILED),
            _make_task("t2"),
            _make_task("t3"),
        ]
        # t1 failed but has retries left
        tasks[0] = Task(
            task_id="t1",
            definition=_make_definition("t1"),
            status=TaskStatus.FAILED,
            retry_count=1,
            max_retries=3,
        )
        state = GraphState(
            request="test",
            run_status=RunStatus.RUNNING,
            implementation_plan=ImplementationPlan(phases=["p1"], dag=dag),
            tasks=tasks,
        )
        result = dispatch_tasks(state)
        updated_tasks = result["tasks"]
        t1 = next(t for t in updated_tasks if t.task_id == "t1")
        assert t1.status == TaskStatus.RETRYING
        assert t1.retry_count == 2

    def test_retry_exhausted_stays_failed(self) -> None:
        """Tasks at max retries remain FAILED."""
        dag = _linear_dag()
        tasks = [
            Task(
                task_id="t1",
                definition=_make_definition("t1"),
                status=TaskStatus.FAILED,
                retry_count=3,
                max_retries=3,
            ),
            _make_task("t2"),
            _make_task("t3"),
        ]
        state = GraphState(
            request="test",
            run_status=RunStatus.RUNNING,
            implementation_plan=ImplementationPlan(phases=["p1"], dag=dag),
            tasks=tasks,
        )
        result = dispatch_tasks(state)
        updated_tasks = result["tasks"]
        t1 = next(t for t in updated_tasks if t.task_id == "t1")
        assert t1.status == TaskStatus.FAILED
        assert t1.retry_count == 3

    def test_no_plan_returns_empty_tasks(self) -> None:
        """If no implementation_plan, returns tasks unchanged."""
        state = GraphState(
            request="test",
            run_status=RunStatus.RUNNING,
        )
        result = dispatch_tasks(state)
        assert result["tasks"] == []

    def test_all_terminal_no_changes(self) -> None:
        """When all tasks are terminal, no dispatching occurs."""
        dag = _linear_dag()
        tasks = [
            _make_task("t1", TaskStatus.SUCCEEDED),
            _make_task("t2", TaskStatus.SUCCEEDED),
            _make_task("t3", TaskStatus.SUCCEEDED),
        ]
        state = GraphState(
            request="test",
            run_status=RunStatus.RUNNING,
            implementation_plan=ImplementationPlan(phases=["p1"], dag=dag),
            tasks=tasks,
        )
        result = dispatch_tasks(state)
        updated_tasks = result["tasks"]
        assert all(t.status == TaskStatus.SUCCEEDED for t in updated_tasks)
