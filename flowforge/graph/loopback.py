"""Quality loopback protocol — re-enters task fanout with proposed tasks.

When test_engineer_node proposes additional tasks, the loopback protocol
appends them to the DAG, increments the quality iteration counter, and
signals re-entry into task_fanout_router. Capped at MAX_QUALITY_ITERATIONS.
"""

from __future__ import annotations

from collections import deque
from enum import StrEnum
from typing import Any

from flowforge.scheduler.revision_lock import RevisionLock
from flowforge.state.models import (
    GraphState,
    ImplementationPlan,
    Task,
    TaskDAG,
    TaskStatus,
)

MAX_QUALITY_ITERATIONS: int = 3


class LoopbackDecision(StrEnum):
    """Decision from loopback evaluation."""

    CONTINUE = "continue"
    LOOPBACK = "loopback"


class LoopbackExceededError(Exception):
    """Raised when quality loopback iterations exceed the cap."""

    def __init__(self, *, iteration: int, cap: int) -> None:
        self.iteration = iteration
        self.cap = cap
        super().__init__(
            f"Quality loopback exceeded cap: iteration {iteration} >= {cap}. "
            f"Blocking run for human input.",
        )


def decide_loopback(state: GraphState) -> LoopbackDecision:
    """Decide whether to loop back for additional quality work.

    Returns:
        LoopbackDecision.LOOPBACK if proposed_tasks exist and cap not reached.
        LoopbackDecision.CONTINUE if no proposed tasks.

    Raises:
        LoopbackExceededError: If cap is reached but tasks still proposed.
    """
    if not state.proposed_tasks:
        return LoopbackDecision.CONTINUE

    if state.quality_iteration >= MAX_QUALITY_ITERATIONS:
        raise LoopbackExceededError(
            iteration=state.quality_iteration,
            cap=MAX_QUALITY_ITERATIONS,
        )

    return LoopbackDecision.LOOPBACK


def compute_delta_scope(dag: TaskDAG, changed_task_ids: list[str]) -> set[str]:
    """Compute the set of task IDs affected by changes.

    Includes the changed tasks themselves plus all transitive dependents
    (tasks that directly or indirectly depend on the changed ones).

    Args:
        dag: Current task DAG.
        changed_task_ids: IDs of tasks that were added or modified.

    Returns:
        Set of task IDs in the delta scope.
    """
    # Build adjacency list: from_task → list of to_tasks (dependents)
    dependents: dict[str, list[str]] = {td.task_id: [] for td in dag.tasks}
    for edge in dag.edges:
        if edge.from_task_id in dependents:
            dependents[edge.from_task_id].append(edge.to_task_id)

    # BFS from changed tasks to find all transitive dependents
    delta: set[str] = set()
    queue: deque[str] = deque(changed_task_ids)

    while queue:
        current = queue.popleft()
        if current in delta:
            continue
        delta.add(current)
        for dependent in dependents.get(current, []):
            if dependent not in delta:
                queue.append(dependent)

    return delta


def execute_loopback(state: GraphState) -> dict[str, Any]:
    """Execute the loopback: append proposed tasks, increment iteration, clear proposals.

    This produces a state update dict suitable for LangGraph node return.

    Args:
        state: Current graph state with proposed_tasks populated.

    Returns:
        Dict with updated tasks, implementation_plan, quality_iteration, proposed_tasks.
    """
    proposed = state.proposed_tasks
    plan = state.implementation_plan
    assert plan is not None  # noqa: S101

    dag = plan.dag

    # Create a revision lock for the mutation
    lock = RevisionLock()
    lock.acquire(revision=dag.plan_revision)

    try:
        lock.validate_write(revision=dag.plan_revision)

        # Append new task definitions and create Task entries
        new_task_defs = list(dag.tasks) + list(proposed)
        new_edges = list(dag.edges)
        new_revision = dag.plan_revision + 1

        lock.bump_revision()

        new_dag = TaskDAG(
            tasks=new_task_defs,
            edges=new_edges,
            plan_revision=new_revision,
        )

        # Create new Task entries for proposed definitions
        new_task_entries = [
            Task(task_id=td.task_id, definition=td, status=TaskStatus.PENDING) for td in proposed
        ]

        updated_tasks = list(state.tasks) + new_task_entries
        updated_plan = ImplementationPlan(
            phases=plan.phases,
            dag=new_dag,
            plan_revision=new_revision,
        )
    finally:
        lock.release()

    return {
        "tasks": updated_tasks,
        "implementation_plan": updated_plan,
        "quality_iteration": state.quality_iteration + 1,
        "proposed_tasks": [],
    }
