"""Unit tests for adapter base contract and schemas."""

from __future__ import annotations

import pytest

from src.adapters.base import (
    AdapterBase,
    IntegrationError,
    RateLimitExceededError,
    RateLimitPolicy,
)
from src.adapters.schemas import (
    CanonicalRequest,
    CanonicalResponse,
    ExecutionPolicy,
)


class _TestAdapter(AdapterBase):
    """Concrete adapter for testing the base contract."""

    adapter_id: str = "test_adapter"
    auth_mode: str = "token"

    def normalize_request(self, raw_input: dict[str, object]) -> CanonicalRequest:
        return CanonicalRequest(
            request_id=str(raw_input.get("id", "req-1")),
            assistant_provider="test",
            user_prompt=str(raw_input.get("prompt", "")),
            repository_context=str(raw_input.get("repo", "")),
        )

    def normalize_response(self, state: dict[str, object]) -> CanonicalResponse:
        return CanonicalResponse(
            request_id=str(state.get("request_id", "req-1")),
            run_id=str(state.get("run_id", "run-1")),
            terminal_status=str(state.get("run_status", "succeeded")),
        )

    def map_error(self, error: Exception) -> IntegrationError:
        return IntegrationError(
            adapter_id=self.adapter_id,
            original_error=error,
            message=str(error),
        )


class TestAdapterContract:
    """AdapterBase enforces the required contract fields."""

    def test_has_adapter_id(self) -> None:
        adapter = _TestAdapter()
        assert adapter.adapter_id == "test_adapter"

    def test_has_auth_mode(self) -> None:
        adapter = _TestAdapter()
        assert adapter.auth_mode == "token"

    def test_normalize_request_returns_canonical(self) -> None:
        adapter = _TestAdapter()
        result = adapter.normalize_request({"id": "r1", "prompt": "Build API"})
        assert isinstance(result, CanonicalRequest)
        assert result.request_id == "r1"
        assert result.user_prompt == "Build API"

    def test_normalize_response_returns_canonical(self) -> None:
        adapter = _TestAdapter()
        result = adapter.normalize_response({"request_id": "r1", "run_status": "succeeded"})
        assert isinstance(result, CanonicalResponse)
        assert result.terminal_status == "succeeded"

    def test_map_error_returns_integration_error(self) -> None:
        adapter = _TestAdapter()
        err = adapter.map_error(ValueError("something broke"))
        assert isinstance(err, IntegrationError)
        assert err.adapter_id == "test_adapter"
        assert "something broke" in err.message


class TestIntegrationError:
    """IntegrationError preserves context without corrupting state."""

    def test_preserves_original_error(self) -> None:
        original = RuntimeError("connection timeout")
        err = IntegrationError(
            adapter_id="copilot",
            original_error=original,
            message="connection timeout",
        )
        assert err.original_error is original
        assert err.adapter_id == "copilot"

    def test_is_exception(self) -> None:
        err = IntegrationError(
            adapter_id="copilot",
            original_error=Exception("x"),
            message="x",
        )
        assert isinstance(err, Exception)


class TestCanonicalSchemas:
    """canonical_request and canonical_response schemas match spec."""

    def test_canonical_request_fields(self) -> None:
        req = CanonicalRequest(
            request_id="r1",
            assistant_provider="copilot",
            user_prompt="Build an API",
            repository_context="github.com/org/repo",
            constraints=["no-external-deps"],
            execution_policy=ExecutionPolicy(max_tokens=8192),
            metadata={"session": "abc"},
        )
        assert req.request_id == "r1"
        assert req.assistant_provider == "copilot"
        assert req.constraints == ["no-external-deps"]
        assert req.execution_policy.max_tokens == 8192

    def test_canonical_response_fields(self) -> None:
        resp = CanonicalResponse(
            request_id="r1",
            run_id="run-1",
            terminal_status="succeeded",
            produced_artifacts=["artifact-1"],
            triaged_issues=["issue-1"],
            shipping_readiness={"is_ready": True},
            diagnostics={"duration_ms": 1200},
        )
        assert resp.run_id == "run-1"
        assert resp.produced_artifacts == ["artifact-1"]

    def test_canonical_request_defaults(self) -> None:
        req = CanonicalRequest(
            request_id="r1",
            assistant_provider="codex",
            user_prompt="Hello",
        )
        assert req.constraints == []
        assert req.metadata == {}
        assert req.repository_context == ""


class TestRateLimitPolicy:
    """Rate limit policy enforced per adapter."""

    def test_within_limit_allowed(self) -> None:
        policy = RateLimitPolicy(max_requests_per_minute=10)
        for _ in range(9):
            policy.record_request()
        assert policy.is_allowed()

    def test_exceeding_limit_blocked(self) -> None:
        policy = RateLimitPolicy(max_requests_per_minute=2)
        policy.record_request()
        policy.record_request()
        assert not policy.is_allowed()

    def test_check_raises_on_exceeded(self) -> None:
        policy = RateLimitPolicy(max_requests_per_minute=1)
        policy.record_request()
        with pytest.raises(RateLimitExceededError):
            policy.check_or_raise()

    def test_reset_clears_window(self) -> None:
        policy = RateLimitPolicy(max_requests_per_minute=1)
        policy.record_request()
        assert not policy.is_allowed()
        policy.reset()
        assert policy.is_allowed()
