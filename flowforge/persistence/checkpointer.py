"""Checkpointer wrapper with fail-closed semantics over LangGraph backends."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from flowforge.persistence.errors import (
    CheckpointerMidRunUnavailableError,
    CheckpointerUnavailableError,
)

if TYPE_CHECKING:
    from flowforge.state.models import GraphState


class CheckpointerBackend(StrEnum):
    """Supported persistence backends."""

    POSTGRES = "postgres"
    SQLITE = "sqlite"


class CheckpointerWrapper:
    """Wraps LangGraph checkpointer with fail-closed availability semantics.

    - If unavailable at run start: raises CheckpointerUnavailableError before side effects.
    - If becomes unavailable mid-run: raises CheckpointerMidRunUnavailableError
      signaling the run should transition to blocked.
    """

    def __init__(
        self,
        backend: CheckpointerBackend = CheckpointerBackend.SQLITE,
        connection_string: str | None = None,
    ) -> None:
        self._backend = backend
        self._connection_string = connection_string
        self._connected = False
        self._store: dict[str, str] = {}

    @property
    def backend(self) -> CheckpointerBackend:
        """Return configured backend type."""
        return self._backend

    @property
    def is_connected(self) -> bool:
        """Return current connection status."""
        return self._connected

    def connect(self) -> None:
        """Establish connection to the persistence backend.

        Raises:
            CheckpointerUnavailableError: If backend cannot be reached.
        """
        try:
            self._do_connect()
            self._connected = True
        except Exception as e:
            self._connected = False
            raise CheckpointerUnavailableError(
                backend=self._backend,
                reason=str(e),
            ) from e

    def _do_connect(self) -> None:
        """Internal connection logic. Override for real backends."""
        # For SQLite/in-memory: always succeeds in base implementation.
        # Real implementations would connect to Postgres/SQLite file here.
        pass

    def save(self, run_id: str, state: GraphState) -> None:
        """Persist a state checkpoint.

        Raises:
            CheckpointerUnavailableError: If not connected at start.
            CheckpointerMidRunUnavailableError: If connection lost mid-run.
        """
        if not self._connected:
            raise CheckpointerUnavailableError(
                backend=self._backend,
                reason="Not connected. Call connect() first.",
            )
        try:
            self._do_save(run_id, state)
        except CheckpointerUnavailableError:
            raise
        except Exception as e:
            self._connected = False
            raise CheckpointerMidRunUnavailableError(
                backend=self._backend,
                run_id=run_id,
                reason=str(e),
            ) from e

    def _do_save(self, run_id: str, state: GraphState) -> None:
        """Internal save logic. Override for real backends."""
        self._store[run_id] = state.model_dump_json()

    def load(self, run_id: str) -> GraphState | None:
        """Load the last checkpoint for a run.

        Returns:
            The restored GraphState, or None if no checkpoint exists.

        Raises:
            CheckpointerUnavailableError: If not connected.
            CheckpointerMidRunUnavailableError: If connection lost mid-operation.
        """
        if not self._connected:
            raise CheckpointerUnavailableError(
                backend=self._backend,
                reason="Not connected. Call connect() first.",
            )
        try:
            return self._do_load(run_id)
        except CheckpointerUnavailableError:
            raise
        except Exception as e:
            self._connected = False
            raise CheckpointerMidRunUnavailableError(
                backend=self._backend,
                run_id=run_id,
                reason=str(e),
            ) from e

    def _do_load(self, run_id: str) -> GraphState | None:
        """Internal load logic. Override for real backends."""
        from flowforge.state.models import GraphState

        raw = self._store.get(run_id)
        if raw is None:
            return None
        return GraphState.model_validate_json(raw)

    def is_available(self) -> bool:
        """Check if the checkpointer is currently available."""
        return self._connected

    def disconnect(self) -> None:
        """Disconnect from backend."""
        self._connected = False
