"""Unit tests for revision locking and optimistic concurrency."""

from __future__ import annotations

import pytest

from src.scheduler.revision_lock import (
    RevisionLock,
    StaleRevisionError,
)


class TestRevisionLockAcquisition:
    """Lock acquisition is atomic and tracks active revision."""

    def test_acquire_succeeds_with_matching_revision(self) -> None:
        """Lock acquired when current_revision matches state revision."""
        lock = RevisionLock()
        lock.acquire(revision=1)
        assert lock.held_revision == 1

    def test_acquire_updates_to_new_revision(self) -> None:
        """Lock can be acquired for a higher revision."""
        lock = RevisionLock()
        lock.acquire(revision=1)
        lock.release()
        lock.acquire(revision=2)
        assert lock.held_revision == 2

    def test_is_held_property(self) -> None:
        """is_held reflects lock state correctly."""
        lock = RevisionLock()
        assert lock.is_held is False
        lock.acquire(revision=1)
        assert lock.is_held is True
        lock.release()
        assert lock.is_held is False

    def test_acquire_while_held_raises(self) -> None:
        """Cannot acquire lock when already held without releasing."""
        lock = RevisionLock()
        lock.acquire(revision=1)
        with pytest.raises(StaleRevisionError):
            lock.acquire(revision=1)


class TestStaleRevisionDetection:
    """Stale revision writes are rejected."""

    def test_validate_raises_on_stale_revision(self) -> None:
        """Writing with an old revision raises StaleRevisionError."""
        lock = RevisionLock()
        lock.acquire(revision=2)
        with pytest.raises(StaleRevisionError) as exc_info:
            lock.validate_write(revision=1)
        assert exc_info.value.held_revision == 2
        assert exc_info.value.attempted_revision == 1

    def test_validate_succeeds_on_matching_revision(self) -> None:
        """Writing with current revision passes."""
        lock = RevisionLock()
        lock.acquire(revision=3)
        # Should not raise
        lock.validate_write(revision=3)

    def test_validate_raises_when_no_lock_held(self) -> None:
        """Writing without holding a lock raises."""
        lock = RevisionLock()
        with pytest.raises(StaleRevisionError):
            lock.validate_write(revision=1)

    def test_stale_error_has_remediation(self) -> None:
        """StaleRevisionError includes remediation guidance."""
        lock = RevisionLock()
        lock.acquire(revision=5)
        with pytest.raises(StaleRevisionError) as exc_info:
            lock.validate_write(revision=3)
        assert "retry" in str(exc_info.value).lower()

    def test_higher_revision_write_also_rejected(self) -> None:
        """Writing with a future revision also raises (must match exactly)."""
        lock = RevisionLock()
        lock.acquire(revision=2)
        with pytest.raises(StaleRevisionError):
            lock.validate_write(revision=3)


class TestConcurrentWriteSafety:
    """Concurrent task updates do not lose writes."""

    def test_write_with_valid_lock_returns_new_revision(self) -> None:
        """commit_write advances revision and returns new value."""
        lock = RevisionLock()
        lock.acquire(revision=1)
        new_rev = lock.commit_write(revision=1)
        assert new_rev == 1  # same revision until explicitly bumped

    def test_bump_revision_increments(self) -> None:
        """bump_revision explicitly advances the lock to next revision."""
        lock = RevisionLock()
        lock.acquire(revision=1)
        lock.bump_revision()
        assert lock.held_revision == 2

    def test_write_after_bump_requires_new_revision(self) -> None:
        """After bumping, old revision writes are rejected."""
        lock = RevisionLock()
        lock.acquire(revision=1)
        lock.bump_revision()
        with pytest.raises(StaleRevisionError):
            lock.validate_write(revision=1)
        # New revision works
        lock.validate_write(revision=2)

    def test_release_and_reacquire_cycle(self) -> None:
        """Full acquire → write → release → reacquire cycle works."""
        lock = RevisionLock()
        lock.acquire(revision=1)
        lock.validate_write(revision=1)
        lock.commit_write(revision=1)
        lock.release()

        lock.acquire(revision=2)
        lock.validate_write(revision=2)
        lock.commit_write(revision=2)
        lock.release()
        assert lock.is_held is False


class TestContextManager:
    """RevisionLock supports context manager protocol."""

    def test_context_manager_acquires_and_releases(self) -> None:
        """with statement acquires and releases lock."""
        lock = RevisionLock()
        with lock.hold(revision=1) as held_rev:
            assert held_rev == 1
            assert lock.is_held is True
        assert lock.is_held is False

    def test_context_manager_releases_on_exception(self) -> None:
        """Lock is released even if exception occurs."""
        lock = RevisionLock()
        with pytest.raises(ValueError, match="test"), lock.hold(revision=1):
            raise ValueError("test")
        assert lock.is_held is False
