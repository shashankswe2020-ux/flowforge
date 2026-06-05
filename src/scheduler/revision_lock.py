"""Revision locking with optimistic concurrency for DAG scheduler.

Ensures the router only schedules tasks for the active plan revision
and rejects stale writes deterministically.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator


class StaleRevisionError(Exception):
    """Raised when a write targets a revision that doesn't match the held lock."""

    def __init__(self, *, held_revision: int | None, attempted_revision: int) -> None:
        self.held_revision = held_revision
        self.attempted_revision = attempted_revision
        super().__init__(
            f"Stale revision write rejected: attempted revision {attempted_revision} "
            f"but lock holds revision {held_revision}. "
            f"Retry with the latest revision snapshot.",
        )


class RevisionLock:
    """Optimistic concurrency lock for plan revision scheduling.

    The router must acquire the lock before scheduling tasks and validate
    that writes match the held revision. Mismatches raise StaleRevisionError
    to trigger retry with fresh state.
    """

    def __init__(self) -> None:
        self._held_revision: int | None = None
        self._is_held: bool = False

    @property
    def is_held(self) -> bool:
        """Whether the lock is currently held."""
        return self._is_held

    @property
    def held_revision(self) -> int | None:
        """The revision currently held by the lock, or None if not held."""
        return self._held_revision

    def acquire(self, *, revision: int) -> None:
        """Acquire the lock for a specific plan revision.

        Raises:
            StaleRevisionError: If lock is already held (must release first).
        """
        if self._is_held:
            raise StaleRevisionError(
                held_revision=self._held_revision,
                attempted_revision=revision,
            )
        self._held_revision = revision
        self._is_held = True

    def release(self) -> None:
        """Release the lock."""
        self._is_held = False

    def validate_write(self, *, revision: int) -> None:
        """Validate that a write targets the currently held revision.

        Raises:
            StaleRevisionError: If revision doesn't match held revision or lock not held.
        """
        if not self._is_held or revision != self._held_revision:
            raise StaleRevisionError(
                held_revision=self._held_revision,
                attempted_revision=revision,
            )

    def commit_write(self, *, revision: int) -> int:
        """Commit a write at the given revision.

        Validates the revision matches, then returns the current revision.

        Raises:
            StaleRevisionError: If revision doesn't match.
        """
        self.validate_write(revision=revision)
        assert self._held_revision is not None
        return self._held_revision

    def bump_revision(self) -> None:
        """Advance the held revision by 1.

        Used when a DAG mutation creates a new revision.
        """
        if self._held_revision is not None:
            self._held_revision += 1

    @contextmanager
    def hold(self, *, revision: int) -> Generator[int, None, None]:
        """Context manager that acquires and releases the lock.

        Yields:
            The held revision number.
        """
        self.acquire(revision=revision)
        try:
            yield revision
        finally:
            self.release()
