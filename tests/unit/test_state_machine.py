"""Tests for state machine transition validation."""

from __future__ import annotations

import pytest

from flowforge.state.errors import IllegalTransitionError
from flowforge.state.machine import validate_run_transition, validate_task_transition
from flowforge.state.models import RunStatus, TaskStatus


class TestRunTransitions:
    """Verify all legal run transitions are accepted and illegal ones rejected."""

    def test_pending_to_running(self) -> None:
        validate_run_transition(RunStatus.PENDING, RunStatus.RUNNING)

    def test_pending_to_cancelled(self) -> None:
        validate_run_transition(RunStatus.PENDING, RunStatus.CANCELLED)

    def test_running_to_waiting_for_input(self) -> None:
        validate_run_transition(RunStatus.RUNNING, RunStatus.WAITING_FOR_INPUT)

    def test_running_to_blocked(self) -> None:
        validate_run_transition(RunStatus.RUNNING, RunStatus.BLOCKED)

    def test_running_to_failed(self) -> None:
        validate_run_transition(RunStatus.RUNNING, RunStatus.FAILED)

    def test_running_to_succeeded(self) -> None:
        validate_run_transition(RunStatus.RUNNING, RunStatus.SUCCEEDED)

    def test_running_to_cancelled(self) -> None:
        validate_run_transition(RunStatus.RUNNING, RunStatus.CANCELLED)

    def test_waiting_for_input_to_running(self) -> None:
        validate_run_transition(RunStatus.WAITING_FOR_INPUT, RunStatus.RUNNING)

    def test_waiting_for_input_to_cancelled(self) -> None:
        validate_run_transition(RunStatus.WAITING_FOR_INPUT, RunStatus.CANCELLED)

    def test_blocked_to_running(self) -> None:
        validate_run_transition(RunStatus.BLOCKED, RunStatus.RUNNING)

    def test_blocked_to_failed(self) -> None:
        validate_run_transition(RunStatus.BLOCKED, RunStatus.FAILED)

    def test_blocked_to_cancelled(self) -> None:
        validate_run_transition(RunStatus.BLOCKED, RunStatus.CANCELLED)

    def test_same_status_noop(self) -> None:
        """Same-status transition is a no-op, not an error."""
        validate_run_transition(RunStatus.RUNNING, RunStatus.RUNNING)
        validate_run_transition(RunStatus.PENDING, RunStatus.PENDING)

    def test_terminal_states_reject_all(self) -> None:
        """Terminal states (failed, succeeded, cancelled) reject all transitions."""
        terminals = [RunStatus.FAILED, RunStatus.SUCCEEDED, RunStatus.CANCELLED]
        non_terminals = [
            RunStatus.PENDING,
            RunStatus.RUNNING,
            RunStatus.WAITING_FOR_INPUT,
            RunStatus.BLOCKED,
        ]
        for terminal in terminals:
            for target in non_terminals:
                with pytest.raises(IllegalTransitionError) as exc_info:
                    validate_run_transition(terminal, target)
                assert exc_info.value.entity_type == "run"
                assert exc_info.value.current_status == terminal
                assert exc_info.value.target_status == target

    def test_illegal_pending_to_succeeded(self) -> None:
        with pytest.raises(IllegalTransitionError) as exc_info:
            validate_run_transition(RunStatus.PENDING, RunStatus.SUCCEEDED)
        err = exc_info.value
        assert err.entity_type == "run"
        assert err.current_status == "pending"
        assert err.target_status == "succeeded"
        assert "cancelled" in err.allowed
        assert "running" in err.allowed

    def test_illegal_pending_to_blocked(self) -> None:
        with pytest.raises(IllegalTransitionError):
            validate_run_transition(RunStatus.PENDING, RunStatus.BLOCKED)

    def test_illegal_waiting_to_failed(self) -> None:
        with pytest.raises(IllegalTransitionError):
            validate_run_transition(RunStatus.WAITING_FOR_INPUT, RunStatus.FAILED)


class TestTaskTransitions:
    """Verify all legal task transitions are accepted and illegal ones rejected."""

    def test_pending_to_ready(self) -> None:
        validate_task_transition(TaskStatus.PENDING, TaskStatus.READY)

    def test_pending_to_skipped(self) -> None:
        validate_task_transition(TaskStatus.PENDING, TaskStatus.SKIPPED)

    def test_pending_to_cancelled(self) -> None:
        validate_task_transition(TaskStatus.PENDING, TaskStatus.CANCELLED)

    def test_ready_to_running(self) -> None:
        validate_task_transition(TaskStatus.READY, TaskStatus.RUNNING)

    def test_ready_to_skipped(self) -> None:
        validate_task_transition(TaskStatus.READY, TaskStatus.SKIPPED)

    def test_ready_to_cancelled(self) -> None:
        validate_task_transition(TaskStatus.READY, TaskStatus.CANCELLED)

    def test_running_to_retrying(self) -> None:
        validate_task_transition(TaskStatus.RUNNING, TaskStatus.RETRYING)

    def test_running_to_succeeded(self) -> None:
        validate_task_transition(TaskStatus.RUNNING, TaskStatus.SUCCEEDED)

    def test_running_to_failed(self) -> None:
        validate_task_transition(TaskStatus.RUNNING, TaskStatus.FAILED)

    def test_running_to_blocked(self) -> None:
        validate_task_transition(TaskStatus.RUNNING, TaskStatus.BLOCKED)

    def test_running_to_cancelled(self) -> None:
        validate_task_transition(TaskStatus.RUNNING, TaskStatus.CANCELLED)

    def test_retrying_to_running(self) -> None:
        validate_task_transition(TaskStatus.RETRYING, TaskStatus.RUNNING)

    def test_retrying_to_failed(self) -> None:
        validate_task_transition(TaskStatus.RETRYING, TaskStatus.FAILED)

    def test_retrying_to_blocked(self) -> None:
        validate_task_transition(TaskStatus.RETRYING, TaskStatus.BLOCKED)

    def test_retrying_to_cancelled(self) -> None:
        validate_task_transition(TaskStatus.RETRYING, TaskStatus.CANCELLED)

    def test_same_status_noop(self) -> None:
        validate_task_transition(TaskStatus.RUNNING, TaskStatus.RUNNING)
        validate_task_transition(TaskStatus.PENDING, TaskStatus.PENDING)

    def test_terminal_states_reject_all(self) -> None:
        """Terminal task states reject all transitions."""
        terminals = [
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
            TaskStatus.SKIPPED,
            TaskStatus.CANCELLED,
        ]
        non_terminals = [
            TaskStatus.PENDING,
            TaskStatus.READY,
            TaskStatus.RUNNING,
            TaskStatus.RETRYING,
        ]
        for terminal in terminals:
            for target in non_terminals:
                with pytest.raises(IllegalTransitionError) as exc_info:
                    validate_task_transition(terminal, target)
                assert exc_info.value.entity_type == "task"

    def test_illegal_pending_to_running(self) -> None:
        """Cannot go directly from pending to running (must go through ready)."""
        with pytest.raises(IllegalTransitionError) as exc_info:
            validate_task_transition(TaskStatus.PENDING, TaskStatus.RUNNING)
        err = exc_info.value
        assert err.current_status == "pending"
        assert err.target_status == "running"
        assert "ready" in err.allowed

    def test_illegal_ready_to_retrying(self) -> None:
        with pytest.raises(IllegalTransitionError):
            validate_task_transition(TaskStatus.READY, TaskStatus.RETRYING)

    def test_illegal_pending_to_succeeded(self) -> None:
        with pytest.raises(IllegalTransitionError):
            validate_task_transition(TaskStatus.PENDING, TaskStatus.SUCCEEDED)


class TestErrorAttributes:
    """Verify IllegalTransitionError carries correct metadata."""

    def test_error_message_format(self) -> None:
        with pytest.raises(IllegalTransitionError, match=r"Illegal run transition"):
            validate_run_transition(RunStatus.PENDING, RunStatus.FAILED)

    def test_error_allowed_list(self) -> None:
        with pytest.raises(IllegalTransitionError) as exc_info:
            validate_run_transition(RunStatus.PENDING, RunStatus.BLOCKED)
        err = exc_info.value
        assert sorted(err.allowed) == ["cancelled", "running"]

    def test_terminal_allowed_is_empty(self) -> None:
        with pytest.raises(IllegalTransitionError) as exc_info:
            validate_run_transition(RunStatus.SUCCEEDED, RunStatus.RUNNING)
        assert exc_info.value.allowed == []
        assert "none (terminal state)" in str(exc_info.value)
