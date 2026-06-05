"""Idempotency key tracking and compensation for retried task execution.

Ensures side-effecting operations are not double-applied on retry,
and provides documented compensation strategies per side-effect type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class SideEffectType(StrEnum):
    """Classification of side effects for compensation planning."""

    NONE = "none"
    FILE_WRITE = "file_write"
    API_CALL = "api_call"
    GIT_OPERATION = "git_operation"


@dataclass
class OperationRecord:
    """Record of an executed side-effecting operation."""

    idempotency_key: str
    task_id: str
    side_effect_type: SideEffectType
    description: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=UTC))


class DuplicateExecutionError(Exception):
    """Raised when an operation with an already-executed key is attempted."""

    def __init__(self, *, idempotency_key: str, original_record: OperationRecord) -> None:
        self.idempotency_key = idempotency_key
        self.original_record = original_record
        super().__init__(
            f"Duplicate execution detected for key '{idempotency_key}'. "
            f"Original execution at {original_record.timestamp} "
            f"for task '{original_record.task_id}'.",
        )


class IdempotencyStore:
    """Tracks executed idempotency keys to prevent duplicate side effects.

    Usage:
        store = IdempotencyStore()
        if not store.should_skip(key):
            perform_side_effect()
            store.record(key, task_id, side_effect_type)
    """

    def __init__(self) -> None:
        self._records: dict[str, OperationRecord] = {}

    def has_executed(self, idempotency_key: str) -> bool:
        """Check if an operation with this key has been recorded."""
        return idempotency_key in self._records

    def should_skip(self, idempotency_key: str) -> bool:
        """Check if this operation should be skipped (already executed)."""
        return self.has_executed(idempotency_key)

    def get_record(self, idempotency_key: str) -> OperationRecord | None:
        """Get the record for a previously executed key."""
        return self._records.get(idempotency_key)

    def record(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        side_effect_type: SideEffectType,
        description: str = "",
    ) -> None:
        """Record that an operation was executed."""
        self._records[idempotency_key] = OperationRecord(
            idempotency_key=idempotency_key,
            task_id=task_id,
            side_effect_type=side_effect_type,
            description=description,
        )

    def check_and_record(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        side_effect_type: SideEffectType,
        description: str = "",
    ) -> None:
        """Atomically check for duplicate and record if new.

        Raises:
            DuplicateExecutionError: If key was already executed.
        """
        existing = self._records.get(idempotency_key)
        if existing is not None:
            raise DuplicateExecutionError(
                idempotency_key=idempotency_key,
                original_record=existing,
            )
        self.record(
            idempotency_key=idempotency_key,
            task_id=task_id,
            side_effect_type=side_effect_type,
            description=description,
        )

    def keys_for_task(self, task_id: str) -> list[str]:
        """Get all idempotency keys for a specific task."""
        return [key for key, record in self._records.items() if record.task_id == task_id]


def compensation_strategy(side_effect_type: SideEffectType) -> str:
    """Return the documented compensation/rollback strategy for a side-effect type.

    Each side-effect type has a defined reversal approach for retry safety.
    """
    strategies: dict[SideEffectType, str] = {
        SideEffectType.NONE: "No compensation needed — operation has no side effects.",
        SideEffectType.FILE_WRITE: (
            "Delete or restore the file to its pre-operation state. "
            "Use the artifact fingerprint to verify current state before rollback."
        ),
        SideEffectType.API_CALL: (
            "Issue a reversal API call if the service supports it. "
            "Otherwise, mark the operation as requiring manual reconciliation."
        ),
        SideEffectType.GIT_OPERATION: (
            "Revert the commit or reset the branch to the pre-operation ref. "
            "Use git reflog to identify the correct restore point."
        ),
    }
    return strategies.get(side_effect_type, "Unknown side-effect type — manual review required.")
