"""DAG scheduler router — computes next runnable tasks and dispatches them.

Reads the task DAG and current task statuses to determine which tasks
are ready for execution, respecting dependency ordering.
"""

from __future__ import annotations

from typing import Any

from src.state.models import (
    GraphState,
    Task,
    TaskDAG,
    TaskStatus,
)

# Terminal statuses — predecessors in these states unblock dependents
_TERMINAL_STATUSES: frozenset[TaskStatus] = frozenset(
    {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.BLOCKED,
        TaskStatus.SKIPPED,
        TaskStatus.CANCELLED,
    },
)

# Statuses that mean a task is already dispatched or complete
_NON_DISPATCHABLE: frozenset[TaskStatus] = frozenset(
    {
        TaskStatus.READY,
        TaskStatus.RUNNING,
        TaskStatus.RETRYING,
        TaskStatus.FAILED,
        TaskStatus.SUCCEEDED,
        TaskStatus.BLOCKED,
        TaskStatus.SKIPPED,
        TaskStatus.CANCELLED,
    },
)


def compute_next_runnable(dag: TaskDAG, tasks: list[Task]) -> list[str]:
    """Compute task IDs that are ready for dispatch.

    A task is runnable when:
    1. It is in PENDING status (not already dispatched/running/complete)
    2. All its predecessors are in a terminal status

    Returns:
        Sorted list of task IDs ready for dispatch (deterministic order).
    """
    # Build status lookup
    status_map: dict[str, TaskStatus] = {t.task_id: t.status for t in tasks}

    # Build predecessor map from edges
    predecessors: dict[str, list[str]] = {td.task_id: [] for td in dag.tasks}
    for edge in dag.edges:
        predecessors[edge.to_task_id].append(edge.from_task_id)

    runnable: list[str] = []
    for task_def in dag.tasks:
        tid = task_def.task_id
        current_status = status_map.get(tid, TaskStatus.PENDING)

        # Skip tasks that are already dispatched or complete
        if current_status in _NON_DISPATCHABLE:
            continue

        # Check if all predecessors are in terminal state
        preds = predecessors.get(tid, [])
        all_preds_terminal = all(
            status_map.get(p, TaskStatus.PENDING) in _TERMINAL_STATUSES for p in preds
        )

        if all_preds_terminal:
            runnable.append(tid)

    return sorted(runnable)


def dispatch_tasks(state: GraphState) -> dict[str, Any]:
    """Dispatch runnable tasks and handle retries.

    - Transitions PENDING tasks with satisfied dependencies to READY.
    - Transitions FAILED tasks with retries remaining to RETRYING (increments counter).
    - Returns updated tasks list.
    """
    if state.implementation_plan is None:
        return {"tasks": list(state.tasks)}

    dag = state.implementation_plan.dag
    tasks = list(state.tasks)

    # Handle retries for failed tasks with retries remaining
    updated_tasks: list[Task] = []
    for task in tasks:
        if task.status == TaskStatus.FAILED and task.retry_count < task.max_retries:
            # Transition to RETRYING with incremented counter
            updated_tasks.append(
                task.model_copy(
                    update={
                        "status": TaskStatus.RETRYING,
                        "retry_count": task.retry_count + 1,
                    },
                ),
            )
        else:
            updated_tasks.append(task)

    # Compute next runnable from the updated task list
    runnable_ids = compute_next_runnable(dag, updated_tasks)

    # Dispatch: transition PENDING → READY
    final_tasks: list[Task] = []
    for task in updated_tasks:
        if task.task_id in runnable_ids and task.status == TaskStatus.PENDING:
            final_tasks.append(task.model_copy(update={"status": TaskStatus.READY}))
        else:
            final_tasks.append(task)

    return {"tasks": final_tasks}
