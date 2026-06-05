"""Typed errors for persistence layer."""

from __future__ import annotations


class CheckpointerUnavailableError(Exception):
    """Raised when checkpointer is unavailable at run start (fail-closed)."""

    def __init__(self, *, backend: str, reason: str) -> None:
        self.backend = backend
        self.reason = reason
        super().__init__(
            f"Checkpointer unavailable (backend={backend}): {reason}. "
            f"Run cannot start — fail-closed before side effects.",
        )


class CheckpointerMidRunUnavailableError(Exception):
    """Raised when checkpointer becomes unavailable mid-run.

    Signals the run should transition to blocked and persist best-effort diagnostics.
    """

    def __init__(self, *, backend: str, run_id: str, reason: str) -> None:
        self.backend = backend
        self.run_id = run_id
        self.reason = reason
        super().__init__(
            f"Checkpointer lost mid-run (backend={backend}, run={run_id}): {reason}. "
            f"Run should transition to blocked. Resume from last durable checkpoint.",
        )
