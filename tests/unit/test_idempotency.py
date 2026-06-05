"""Unit tests for idempotency key tracking and retry safety."""

from __future__ import annotations

import pytest

from flowforge.nodes.idempotency import (
    DuplicateExecutionError,
    IdempotencyStore,
    SideEffectType,
    compensation_strategy,
)


class TestIdempotencyStore:
    """IdempotencyStore tracks executed keys."""

    def test_new_key_not_seen(self) -> None:
        """Fresh store has not seen any keys."""
        store = IdempotencyStore()
        assert store.has_executed("key-001") is False

    def test_record_marks_key_as_seen(self) -> None:
        """After recording, key is marked as executed."""
        store = IdempotencyStore()
        store.record(
            idempotency_key="key-001",
            task_id="task-001",
            side_effect_type=SideEffectType.FILE_WRITE,
        )
        assert store.has_executed("key-001") is True

    def test_multiple_keys_tracked_independently(self) -> None:
        """Different keys are tracked independently."""
        store = IdempotencyStore()
        store.record(
            idempotency_key="key-001",
            task_id="task-001",
            side_effect_type=SideEffectType.FILE_WRITE,
        )
        assert store.has_executed("key-001") is True
        assert store.has_executed("key-002") is False

    def test_get_record_returns_details(self) -> None:
        """Recorded operation can be retrieved with full details."""
        store = IdempotencyStore()
        store.record(
            idempotency_key="key-001",
            task_id="task-001",
            side_effect_type=SideEffectType.API_CALL,
            description="Created PR #42",
        )
        record = store.get_record("key-001")
        assert record is not None
        assert record.task_id == "task-001"
        assert record.side_effect_type == SideEffectType.API_CALL
        assert record.description == "Created PR #42"

    def test_get_record_returns_none_for_unknown(self) -> None:
        """Unknown key returns None."""
        store = IdempotencyStore()
        assert store.get_record("unknown") is None


class TestDuplicateDetection:
    """Duplicate execution is detected and prevented."""

    def test_check_and_record_succeeds_for_new_key(self) -> None:
        """check_and_record passes for never-seen key."""
        store = IdempotencyStore()
        # Should not raise
        store.check_and_record(
            idempotency_key="key-001",
            task_id="task-001",
            side_effect_type=SideEffectType.FILE_WRITE,
        )

    def test_check_and_record_raises_for_duplicate(self) -> None:
        """check_and_record raises DuplicateExecutionError for seen key."""
        store = IdempotencyStore()
        store.check_and_record(
            idempotency_key="key-001",
            task_id="task-001",
            side_effect_type=SideEffectType.FILE_WRITE,
        )
        with pytest.raises(DuplicateExecutionError) as exc_info:
            store.check_and_record(
                idempotency_key="key-001",
                task_id="task-001",
                side_effect_type=SideEffectType.FILE_WRITE,
            )
        assert exc_info.value.idempotency_key == "key-001"

    def test_duplicate_error_includes_original_record(self) -> None:
        """DuplicateExecutionError references the original execution."""
        store = IdempotencyStore()
        store.check_and_record(
            idempotency_key="key-001",
            task_id="task-001",
            side_effect_type=SideEffectType.API_CALL,
            description="original",
        )
        with pytest.raises(DuplicateExecutionError) as exc_info:
            store.check_and_record(
                idempotency_key="key-001",
                task_id="task-001",
                side_effect_type=SideEffectType.API_CALL,
            )
        assert exc_info.value.original_record.description == "original"


class TestRetryChecking:
    """Retried execution checks key before re-applying."""

    def test_should_skip_returns_true_for_executed_key(self) -> None:
        """Already executed key → should skip."""
        store = IdempotencyStore()
        store.record(
            idempotency_key="key-001",
            task_id="task-001",
            side_effect_type=SideEffectType.FILE_WRITE,
        )
        assert store.should_skip("key-001") is True

    def test_should_skip_returns_false_for_new_key(self) -> None:
        """Never-seen key → should not skip."""
        store = IdempotencyStore()
        assert store.should_skip("key-new") is False

    def test_all_keys_for_task(self) -> None:
        """Can retrieve all keys for a specific task."""
        store = IdempotencyStore()
        store.record(
            idempotency_key="k1",
            task_id="task-A",
            side_effect_type=SideEffectType.FILE_WRITE,
        )
        store.record(
            idempotency_key="k2",
            task_id="task-A",
            side_effect_type=SideEffectType.API_CALL,
        )
        store.record(
            idempotency_key="k3",
            task_id="task-B",
            side_effect_type=SideEffectType.FILE_WRITE,
        )
        keys = store.keys_for_task("task-A")
        assert set(keys) == {"k1", "k2"}


class TestCompensationStrategy:
    """Compensation/rollback documented per side-effect type."""

    def test_file_write_compensation(self) -> None:
        """FILE_WRITE has delete/restore compensation."""
        strategy = compensation_strategy(SideEffectType.FILE_WRITE)
        assert "delete" in strategy.lower() or "restore" in strategy.lower()

    def test_api_call_compensation(self) -> None:
        """API_CALL has reversal compensation."""
        strategy = compensation_strategy(SideEffectType.API_CALL)
        assert len(strategy) > 0

    def test_git_operation_compensation(self) -> None:
        """GIT_OPERATION has revert compensation."""
        strategy = compensation_strategy(SideEffectType.GIT_OPERATION)
        assert "revert" in strategy.lower() or "reset" in strategy.lower()

    def test_no_side_effect_compensation(self) -> None:
        """NONE type needs no compensation."""
        strategy = compensation_strategy(SideEffectType.NONE)
        assert "no" in strategy.lower() or "none" in strategy.lower()
