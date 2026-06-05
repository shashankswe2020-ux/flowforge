"""Typed error classes for state machine violations."""

from __future__ import annotations


class IllegalTransitionError(Exception):
    """Raised when an illegal state transition is attempted."""

    def __init__(
        self,
        *,
        entity_type: str,
        current_status: str,
        target_status: str,
        allowed: list[str],
    ) -> None:
        self.entity_type = entity_type
        self.current_status = current_status
        self.target_status = target_status
        self.allowed = allowed
        allowed_str = ", ".join(allowed) if allowed else "none (terminal state)"
        super().__init__(
            f"Illegal {entity_type} transition: {current_status} -> {target_status}. "
            f"Allowed from {current_status}: {allowed_str}.",
        )
