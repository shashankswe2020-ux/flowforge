"""Tests for CheckpointerWrapper — fail-closed and mid-run semantics."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from flowforge.persistence.checkpointer import CheckpointerBackend, CheckpointerWrapper
from flowforge.persistence.errors import (
    CheckpointerMidRunUnavailableError,
    CheckpointerUnavailableError,
)
from flowforge.state.models import RunStatus
from tests.factories import make_state


class TestCheckpointerConnect:
    """Tests for connection lifecycle."""

    def test_connect_succeeds(self) -> None:
        cp = CheckpointerWrapper(backend=CheckpointerBackend.SQLITE)
        cp.connect()
        assert cp.is_connected is True
        assert cp.is_available() is True

    def test_backend_property(self) -> None:
        cp = CheckpointerWrapper(backend=CheckpointerBackend.POSTGRES)
        assert cp.backend == CheckpointerBackend.POSTGRES

    def test_connect_failure_raises_unavailable(self) -> None:
        cp = CheckpointerWrapper(backend=CheckpointerBackend.SQLITE)
        with patch.object(
            cp,
            "_do_connect",
            side_effect=ConnectionError("refused"),
        ):
            with pytest.raises(CheckpointerUnavailableError) as exc_info:
                cp.connect()
            assert exc_info.value.backend == "sqlite"
            assert "refused" in exc_info.value.reason
            assert cp.is_connected is False

    def test_disconnect(self) -> None:
        cp = CheckpointerWrapper()
        cp.connect()
        assert cp.is_connected is True
        cp.disconnect()
        assert cp.is_connected is False


class TestCheckpointerSave:
    """Tests for save semantics."""

    def test_save_succeeds_when_connected(self) -> None:
        cp = CheckpointerWrapper()
        cp.connect()
        state = make_state("start")
        cp.save("run-001", state)
        # No exception means success

    def test_save_fails_when_not_connected(self) -> None:
        cp = CheckpointerWrapper()
        state = make_state("start")
        with pytest.raises(CheckpointerUnavailableError) as exc_info:
            cp.save("run-001", state)
        assert "Not connected" in exc_info.value.reason

    def test_save_mid_run_failure(self) -> None:
        cp = CheckpointerWrapper()
        cp.connect()
        state = make_state("start")

        with patch.object(
            cp,
            "_do_save",
            side_effect=OSError("disk full"),
        ):
            with pytest.raises(CheckpointerMidRunUnavailableError) as exc_info:
                cp.save("run-001", state)
            assert exc_info.value.run_id == "run-001"
            assert "disk full" in exc_info.value.reason
            assert cp.is_connected is False


class TestCheckpointerLoad:
    """Tests for load semantics."""

    def test_load_returns_none_for_unknown_run(self) -> None:
        cp = CheckpointerWrapper()
        cp.connect()
        result = cp.load("nonexistent-run")
        assert result is None

    def test_load_returns_saved_state(self) -> None:
        cp = CheckpointerWrapper()
        cp.connect()
        state = make_state("plan")
        cp.save("run-002", state)
        restored = cp.load("run-002")
        assert restored is not None
        assert restored.run_status == RunStatus.RUNNING
        assert restored.implementation_plan is not None

    def test_load_fails_when_not_connected(self) -> None:
        cp = CheckpointerWrapper()
        with pytest.raises(CheckpointerUnavailableError):
            cp.load("run-001")

    def test_load_mid_run_failure(self) -> None:
        cp = CheckpointerWrapper()
        cp.connect()
        cp.save("run-001", make_state("start"))

        with patch.object(
            cp,
            "_do_load",
            side_effect=OSError("connection reset"),
        ):
            with pytest.raises(CheckpointerMidRunUnavailableError) as exc_info:
                cp.load("run-001")
            assert exc_info.value.run_id == "run-001"
            assert cp.is_connected is False


class TestCheckpointerRoundTrip:
    """Tests for full state round-trip through checkpointer."""

    def test_full_state_round_trip(self) -> None:
        cp = CheckpointerWrapper()
        cp.connect()

        state = make_state("complete")
        cp.save("run-full", state)
        restored = cp.load("run-full")

        assert restored is not None
        assert restored.run_status == RunStatus.SUCCEEDED
        assert restored.shipping_result.shipped is True
        assert restored.clarified_request is not None
        assert restored.clarified_request.solution_type == "web_app"

    def test_multiple_runs_isolated(self) -> None:
        cp = CheckpointerWrapper()
        cp.connect()

        state_a = make_state("start", overrides={"request": "project A"})
        state_b = make_state("plan", overrides={"request": "project B"})
        cp.save("run-a", state_a)
        cp.save("run-b", state_b)

        loaded_a = cp.load("run-a")
        loaded_b = cp.load("run-b")
        assert loaded_a is not None
        assert loaded_b is not None
        assert loaded_a.request == "project A"
        assert loaded_b.request == "project B"

    def test_save_overwrites_previous_checkpoint(self) -> None:
        cp = CheckpointerWrapper()
        cp.connect()

        state_v1 = make_state("start")
        cp.save("run-x", state_v1)

        state_v2 = make_state(
            "complete",
            overrides={"run_status": RunStatus.SUCCEEDED},
        )
        cp.save("run-x", state_v2)

        restored = cp.load("run-x")
        assert restored is not None
        assert restored.run_status == RunStatus.SUCCEEDED


class TestFailClosedBehavior:
    """Tests that verify fail-closed semantics per spec."""

    def test_unavailable_at_start_prevents_side_effects(self) -> None:
        """If checkpointer can't connect, no state is ever written."""
        cp = CheckpointerWrapper()
        with (
            patch.object(
                cp,
                "_do_connect",
                side_effect=ConnectionError("unreachable"),
            ),
            pytest.raises(CheckpointerUnavailableError),
        ):
            cp.connect()

        # Verify: cannot save or load (fail-closed)
        with pytest.raises(CheckpointerUnavailableError):
            cp.save("run-001", make_state("start"))
        with pytest.raises(CheckpointerUnavailableError):
            cp.load("run-001")

    def test_mid_run_loss_blocks_further_operations(self) -> None:
        """After mid-run failure, subsequent operations also fail."""
        cp = CheckpointerWrapper()
        cp.connect()
        cp.save("run-001", make_state("start"))

        # Simulate mid-run loss
        with (
            patch.object(
                cp,
                "_do_save",
                side_effect=OSError("connection lost"),
            ),
            pytest.raises(CheckpointerMidRunUnavailableError),
        ):
            cp.save("run-001", make_state("plan"))

        # After failure, connected=False, so further ops fail
        with pytest.raises(CheckpointerUnavailableError):
            cp.save("run-001", make_state("plan"))
        with pytest.raises(CheckpointerUnavailableError):
            cp.load("run-001")
