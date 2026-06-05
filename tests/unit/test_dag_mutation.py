"""Unit tests for append-only DAG mutation."""

from __future__ import annotations

import pytest

from src.scheduler.mutation import (
    ImmutableTaskError,
    MutationResult,
    append_tasks,
    reconcile_inflight,
)
from src.scheduler.revision_lock import RevisionLock
from src.state.models import (
    CapabilityType,
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


def _base_dag() -> TaskDAG:
    """t1 -> t2 base DAG at revision 1."""
    return TaskDAG(
        tasks=[_make_definition("t1"), _make_definition("t2")],
        edges=[TaskDependency(from_task_id="t1", to_task_id="t2")],
        plan_revision=1,
    )


class TestAppendOnly:
    """Mutations are append-only; succeeded tasks are immutable."""

    def test_append_new_tasks(self) -> None:
        """New tasks and edges are added to the DAG."""
        dag = _base_dag()
        tasks = [_make_task("t1", TaskStatus.SUCCEEDED), _make_task("t2", TaskStatus.RUNNING)]
        lock = RevisionLock()
        lock.acquire(revision=1)

        new_defs = [_make_definition("t3")]
        new_edges = [TaskDependency(from_task_id="t2", to_task_id="t3")]

        result = append_tasks(
            dag=dag,
            existing_tasks=tasks,
            new_definitions=new_defs,
            new_edges=new_edges,
            lock=lock,
        )

        assert len(result.dag.tasks) == 3
        assert any(t.task_id == "t3" for t in result.dag.tasks)
        assert len(result.dag.edges) == 2

    def test_revision_bumped_on_mutation(self) -> None:
        """Plan revision is incremented after append."""
        dag = _base_dag()
        tasks = [_make_task("t1", TaskStatus.SUCCEEDED), _make_task("t2")]
        lock = RevisionLock()
        lock.acquire(revision=1)

        result = append_tasks(
            dag=dag,
            existing_tasks=tasks,
            new_definitions=[_make_definition("t3")],
            new_edges=[],
            lock=lock,
        )

        assert result.dag.plan_revision == 2
        assert lock.held_revision == 2

    def test_succeeded_tasks_immutable(self) -> None:
        """Cannot add edges that modify succeeded task dependencies."""
        dag = _base_dag()
        tasks = [_make_task("t1", TaskStatus.SUCCEEDED), _make_task("t2")]
        lock = RevisionLock()
        lock.acquire(revision=1)

        # Try to add an edge INTO a succeeded task
        bad_edge = TaskDependency(from_task_id="t3", to_task_id="t1")
        with pytest.raises(ImmutableTaskError) as exc_info:
            append_tasks(
                dag=dag,
                existing_tasks=tasks,
                new_definitions=[_make_definition("t3")],
                new_edges=[bad_edge],
                lock=lock,
            )
        assert exc_info.value.task_id == "t1"

    def test_existing_task_definitions_preserved(self) -> None:
        """Original task definitions are not modified."""
        dag = _base_dag()
        tasks = [_make_task("t1"), _make_task("t2")]
        lock = RevisionLock()
        lock.acquire(revision=1)

        result = append_tasks(
            dag=dag,
            existing_tasks=tasks,
            new_definitions=[_make_definition("t3")],
            new_edges=[],
            lock=lock,
        )

        # Original tasks still present and unchanged
        orig_ids = {t.task_id for t in dag.tasks}
        result_ids = {t.task_id for t in result.dag.tasks}
        assert orig_ids.issubset(result_ids)

    def test_new_tasks_start_as_pending(self) -> None:
        """Appended tasks are added to task list as PENDING."""
        dag = _base_dag()
        tasks = [_make_task("t1", TaskStatus.SUCCEEDED), _make_task("t2")]
        lock = RevisionLock()
        lock.acquire(revision=1)

        result = append_tasks(
            dag=dag,
            existing_tasks=tasks,
            new_definitions=[_make_definition("t3")],
            new_edges=[],
            lock=lock,
        )

        t3 = next(t for t in result.tasks if t.task_id == "t3")
        assert t3.status == TaskStatus.PENDING


class TestRevisionLockIntegration:
    """Router acquires new revision lock before scheduling mutated tasks."""

    def test_lock_must_be_held(self) -> None:
        """append_tasks fails if lock is not held."""
        dag = _base_dag()
        tasks = [_make_task("t1"), _make_task("t2")]
        lock = RevisionLock()  # not acquired

        from src.scheduler.revision_lock import StaleRevisionError

        with pytest.raises(StaleRevisionError):
            append_tasks(
                dag=dag,
                existing_tasks=tasks,
                new_definitions=[_make_definition("t3")],
                new_edges=[],
                lock=lock,
            )

    def test_stale_lock_rejected(self) -> None:
        """Cannot mutate with a lock holding wrong revision."""
        dag = TaskDAG(
            tasks=[_make_definition("t1")],
            edges=[],
            plan_revision=3,
        )
        tasks = [_make_task("t1")]
        lock = RevisionLock()
        lock.acquire(revision=2)  # stale — DAG is at rev 3

        from src.scheduler.revision_lock import StaleRevisionError

        with pytest.raises(StaleRevisionError):
            append_tasks(
                dag=dag,
                existing_tasks=tasks,
                new_definitions=[_make_definition("t2")],
                new_edges=[],
                lock=lock,
            )


class TestReconcileInflight:
    """In-flight prior-revision outputs are reconciled before join."""

    def test_reconcile_keeps_succeeded_from_prior_revision(self) -> None:
        """Tasks that succeeded under prior revision remain succeeded."""
        tasks = [
            _make_task("t1", TaskStatus.SUCCEEDED),
            _make_task("t2", TaskStatus.SUCCEEDED),
            _make_task("t3", TaskStatus.PENDING),  # new in revision 2
        ]
        reconciled = reconcile_inflight(tasks, current_revision=2)
        t1 = next(t for t in reconciled if t.task_id == "t1")
        t2 = next(t for t in reconciled if t.task_id == "t2")
        assert t1.status == TaskStatus.SUCCEEDED
        assert t2.status == TaskStatus.SUCCEEDED

    def test_reconcile_keeps_running_tasks_as_running(self) -> None:
        """Running tasks from prior revision continue running."""
        tasks = [
            _make_task("t1", TaskStatus.RUNNING),
            _make_task("t2", TaskStatus.PENDING),
        ]
        reconciled = reconcile_inflight(tasks, current_revision=2)
        t1 = next(t for t in reconciled if t.task_id == "t1")
        assert t1.status == TaskStatus.RUNNING

    def test_reconcile_preserves_all_tasks(self) -> None:
        """Reconciliation doesn't drop any tasks."""
        tasks = [
            _make_task("t1", TaskStatus.SUCCEEDED),
            _make_task("t2", TaskStatus.FAILED),
            _make_task("t3", TaskStatus.PENDING),
        ]
        reconciled = reconcile_inflight(tasks, current_revision=2)
        assert len(reconciled) == 3

    def test_reconcile_returns_new_list(self) -> None:
        """Reconciliation produces a new list, not mutating input."""
        tasks = [_make_task("t1", TaskStatus.RUNNING)]
        reconciled = reconcile_inflight(tasks, current_revision=2)
        assert reconciled is not tasks


class TestMutationResult:
    """MutationResult has correct shape."""

    def test_result_has_dag_and_tasks(self) -> None:
        """MutationResult contains updated DAG and tasks."""
        dag = _base_dag()
        tasks = [_make_task("t1"), _make_task("t2")]
        lock = RevisionLock()
        lock.acquire(revision=1)

        result = append_tasks(
            dag=dag,
            existing_tasks=tasks,
            new_definitions=[_make_definition("t3")],
            new_edges=[],
            lock=lock,
        )

        assert hasattr(result, "dag")
        assert hasattr(result, "tasks")
        assert isinstance(result, MutationResult)
