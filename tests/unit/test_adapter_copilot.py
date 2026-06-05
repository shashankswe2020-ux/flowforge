"""Unit tests for GitHub Copilot adapter."""

from __future__ import annotations

from src.adapters.copilot import CopilotAdapter
from src.adapters.schemas import CanonicalRequest, CanonicalResponse


def _copilot_input(
    *,
    prompt: str = "Build an auth module",
    repo: str = "github.com/org/project",
    conversation_id: str = "conv-123",
) -> dict[str, object]:
    """Simulated Copilot chat input."""
    return {
        "conversationId": conversation_id,
        "prompt": prompt,
        "repository": {"fullName": repo, "ref": "main"},
        "constraints": ["no-external-deps"],
        "metadata": {"vscodeVersion": "1.90.0", "extensionVersion": "0.50.0"},
    }


def _graph_output(
    *,
    request_id: str = "conv-123",
    run_id: str = "run-abc",
    status: str = "succeeded",
) -> dict[str, object]:
    """Simulated graph state output."""
    return {
        "request_id": request_id,
        "run_id": run_id,
        "run_status": status,
        "artifacts": ["src/auth.py"],
        "triaged_issues": ["issue-1"],
        "shipping_readiness": {"is_ready": True},
        "shipping_result": {"shipped": True},
    }


class TestNormalizeRequest:
    """CopilotAdapter normalizes Copilot input to canonical_request."""

    def test_produces_canonical_request(self) -> None:
        adapter = CopilotAdapter()
        result = adapter.normalize_request(_copilot_input())
        assert isinstance(result, CanonicalRequest)

    def test_maps_conversation_id_to_request_id(self) -> None:
        adapter = CopilotAdapter()
        result = adapter.normalize_request(_copilot_input(conversation_id="c-99"))
        assert result.request_id == "c-99"

    def test_maps_prompt(self) -> None:
        adapter = CopilotAdapter()
        result = adapter.normalize_request(_copilot_input(prompt="Deploy service"))
        assert result.user_prompt == "Deploy service"

    def test_maps_repository_context(self) -> None:
        adapter = CopilotAdapter()
        result = adapter.normalize_request(_copilot_input(repo="github.com/x/y"))
        assert result.repository_context == "github.com/x/y"

    def test_sets_assistant_provider(self) -> None:
        adapter = CopilotAdapter()
        result = adapter.normalize_request(_copilot_input())
        assert result.assistant_provider == "copilot"

    def test_passes_constraints(self) -> None:
        adapter = CopilotAdapter()
        result = adapter.normalize_request(_copilot_input())
        assert result.constraints == ["no-external-deps"]

    def test_passes_metadata(self) -> None:
        adapter = CopilotAdapter()
        result = adapter.normalize_request(_copilot_input())
        assert "vscodeVersion" in result.metadata


class TestNormalizeResponse:
    """CopilotAdapter normalizes graph output to Copilot response format."""

    def test_produces_canonical_response(self) -> None:
        adapter = CopilotAdapter()
        result = adapter.normalize_response(_graph_output())
        assert isinstance(result, CanonicalResponse)

    def test_maps_run_status(self) -> None:
        adapter = CopilotAdapter()
        result = adapter.normalize_response(_graph_output(status="blocked"))
        assert result.terminal_status == "blocked"

    def test_maps_artifacts(self) -> None:
        adapter = CopilotAdapter()
        result = adapter.normalize_response(_graph_output())
        assert result.produced_artifacts == ["src/auth.py"]

    def test_maps_issues(self) -> None:
        adapter = CopilotAdapter()
        result = adapter.normalize_response(_graph_output())
        assert result.triaged_issues == ["issue-1"]


class TestErrorMapping:
    """CopilotAdapter maps errors to IntegrationError."""

    def test_maps_exception(self) -> None:
        adapter = CopilotAdapter()
        err = adapter.map_error(ConnectionError("timeout"))
        assert err.adapter_id == "copilot"
        assert "timeout" in err.message

    def test_preserves_original(self) -> None:
        adapter = CopilotAdapter()
        original = ValueError("bad input")
        err = adapter.map_error(original)
        assert err.original_error is original


class TestEquivalence:
    """Equivalent inputs produce equivalent canonical outputs."""

    def test_same_input_same_output(self) -> None:
        adapter = CopilotAdapter()
        input1 = _copilot_input(prompt="Build API", repo="org/repo")
        input2 = _copilot_input(prompt="Build API", repo="org/repo")
        r1 = adapter.normalize_request(input1)
        r2 = adapter.normalize_request(input2)
        assert r1 == r2
