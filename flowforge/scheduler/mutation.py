"""Append-only DAG mutation for mid-run quality loopback.

Supports adding new tasks/edges while preserving immutability of
succeeded tasks and enforcing revision lock semantics.
"""

from __future__ import annotations

from dataclasses import dataclass

from flowforge.scheduler.revision_lock import RevisionLock
from flowforge.state.models import (
    Task,
    TaskDAG,
    TaskDefinition,
    TaskDependency,
    TaskStatus,
)


class ImmutableTaskError(Exception):
    """Raised when mutation attempts to modify an immutable (succeeded) task."""

    def __init__(self, *, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(
            f"Cannot modify task '{task_id}' — it has already succeeded "
            f"and is immutable. Mutations are append-only.",
        )


@dataclass(frozen=True)
class MutationResult:
    """Result of a DAG append operation."""

    dag: TaskDAG
    tasks: list[Task]


def append_tasks(
    *,
    dag: TaskDAG,
    existing_tasks: list[Task],
    new_definitions: list[TaskDefinition],
    new_edges: list[TaskDependency],
    lock: RevisionLock,
) -> MutationResult:
    """Append new tasks and edges to the DAG.

    - Validates the revision lock is held at the current DAG revision.
    - Rejects edges that point INTO succeeded tasks (immutability).
    - Bumps the plan revision after mutation.
    - Creates new Task entries for appended definitions.

    Args:
        dag: Current task DAG.
        existing_tasks: Current runtime task list.
        new_definitions: Task definitions to append.
        new_edges: Dependency edges to append.
        lock: Revision lock (must be held at dag.plan_revision).

    Returns:
        MutationResult with updated DAG and tasks.

    Raises:
        StaleRevisionError: If lock not held or revision mismatch.
        ImmutableTaskError: If an edge targets a succeeded task.
    """
    # Validate lock is held at the correct revision
    lock.validate_write(revision=dag.plan_revision)

    # Identify succeeded tasks (immutable)
    succeeded_ids = {t.task_id for t in existing_tasks if t.status == TaskStatus.SUCCEEDED}

    # Validate no edges point INTO succeeded tasks
    for edge in new_edges:
        if edge.to_task_id in succeeded_ids:
            raise ImmutableTaskError(task_id=edge.to_task_id)

    # Build new DAG with appended tasks and edges
    updated_tasks_defs = list(dag.tasks) + list(new_definitions)
    updated_edges = list(dag.edges) + list(new_edges)

    # Bump revision
    new_revision = dag.plan_revision + 1
    lock.bump_revision()

    new_dag = TaskDAG(
        tasks=updated_tasks_defs,
        edges=updated_edges,
        plan_revision=new_revision,
    )

    # Create Task entries for new definitions
    new_task_entries = [
        Task(task_id=td.task_id, definition=td, status=TaskStatus.PENDING) for td in new_definitions
    ]
    updated_task_list = list(existing_tasks) + new_task_entries

    return MutationResult(dag=new_dag, tasks=updated_task_list)


def reconcile_inflight(
    tasks: list[Task],
    *,
    current_revision: int,  # noqa: ARG001
) -> list[Task]:
    """Reconcile in-flight prior-revision task outputs.

    Preserves all task states — running tasks continue, succeeded tasks
    remain, pending tasks await scheduling under the new revision.

    This is a passthrough reconciliation: tasks from any revision are
    preserved as-is. The scheduler will only dispatch PENDING tasks
    that belong to the current DAG.

    Args:
        tasks: Current task list (may span multiple revisions).
        current_revision: The active plan revision (for future use).

    Returns:
        New list with reconciled task states.
    """
    return [task.model_copy() for task in tasks]
