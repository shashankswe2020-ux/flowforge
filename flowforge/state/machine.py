"""State machine transition validator for run and task statuses."""

from __future__ import annotations

from flowforge.state.errors import IllegalTransitionError
from flowforge.state.models import RunStatus, TaskStatus

# Legal transitions per the spec state machine
_RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.PENDING: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.WAITING_FOR_INPUT,
            RunStatus.BLOCKED,
            RunStatus.FAILED,
            RunStatus.SUCCEEDED,
            RunStatus.CANCELLED,
        },
    ),
    RunStatus.WAITING_FOR_INPUT: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED}),
    RunStatus.BLOCKED: frozenset({RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED}),
    RunStatus.FAILED: frozenset(),
    RunStatus.SUCCEEDED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}

_TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset({TaskStatus.READY, TaskStatus.SKIPPED, TaskStatus.CANCELLED}),
    TaskStatus.READY: frozenset({TaskStatus.RUNNING, TaskStatus.SKIPPED, TaskStatus.CANCELLED}),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.RETRYING,
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
        },
    ),
    TaskStatus.RETRYING: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
        },
    ),
    TaskStatus.SUCCEEDED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.BLOCKED: frozenset(),
    TaskStatus.SKIPPED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}


def validate_run_transition(current: RunStatus, target: RunStatus) -> None:
    """Validate a run status transition. Raises IllegalTransitionError if invalid."""
    if current == target:
        return
    allowed = _RUN_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise IllegalTransitionError(
            entity_type="run",
            current_status=current,
            target_status=target,
            allowed=sorted(allowed, key=str),
        )


def validate_task_transition(current: TaskStatus, target: TaskStatus) -> None:
    """Validate a task status transition. Raises IllegalTransitionError if invalid."""
    if current == target:
        return
    allowed = _TASK_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise IllegalTransitionError(
            entity_type="task",
            current_status=current,
            target_status=target,
            allowed=sorted(allowed, key=str),
        )
