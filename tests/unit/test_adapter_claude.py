"""Unit tests for Claude Code adapter."""

from __future__ import annotations

from flowforge.adapters.claude_code import ClaudeCodeAdapter
from flowforge.adapters.copilot import CopilotAdapter
from flowforge.adapters.schemas import CanonicalRequest, CanonicalResponse


def _claude_input(
    *,
    prompt: str = "Build an auth module",
    repo: str = "github.com/org/project",
    session_id: str = "sess-789",
) -> dict[str, object]:
    """Simulated Claude Code input."""
    return {
        "sessionId": session_id,
        "message": prompt,
        "project": {"path": repo, "branch": "main"},
        "constraints": ["no-external-deps"],
        "context": {"toolVersion": "1.2.0", "platform": "macos"},
    }


def _graph_output(
    *,
    request_id: str = "sess-789",
    run_id: str = "run-abc",
    status: str = "succeeded",
) -> dict[str, object]:
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
    """ClaudeCodeAdapter normalizes Claude Code input to canonical_request."""

    def test_produces_canonical_request(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.normalize_request(_claude_input())
        assert isinstance(result, CanonicalRequest)

    def test_maps_session_id_to_request_id(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.normalize_request(_claude_input(session_id="s-42"))
        assert result.request_id == "s-42"

    def test_maps_message_to_prompt(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.normalize_request(_claude_input(prompt="Deploy"))
        assert result.user_prompt == "Deploy"

    def test_maps_project_path_to_repo(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.normalize_request(_claude_input(repo="github.com/x/y"))
        assert result.repository_context == "github.com/x/y"

    def test_sets_assistant_provider(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.normalize_request(_claude_input())
        assert result.assistant_provider == "claude_code"

    def test_passes_constraints(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.normalize_request(_claude_input())
        assert result.constraints == ["no-external-deps"]

    def test_passes_metadata(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.normalize_request(_claude_input())
        assert "toolVersion" in result.metadata


class TestNormalizeResponse:
    """ClaudeCodeAdapter normalizes graph output."""

    def test_produces_canonical_response(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.normalize_response(_graph_output())
        assert isinstance(result, CanonicalResponse)

    def test_maps_status(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.normalize_response(_graph_output(status="blocked"))
        assert result.terminal_status == "blocked"

    def test_maps_artifacts(self) -> None:
        adapter = ClaudeCodeAdapter()
        result = adapter.normalize_response(_graph_output())
        assert result.produced_artifacts == ["src/auth.py"]


class TestErrorMapping:
    """ClaudeCodeAdapter maps errors to IntegrationError."""

    def test_maps_exception(self) -> None:
        adapter = ClaudeCodeAdapter()
        err = adapter.map_error(TimeoutError("API timeout"))
        assert err.adapter_id == "claude_code"
        assert "API timeout" in err.message


class TestCrossAdapterEquivalence:
    """Equivalent inputs produce equivalent canonical outputs across adapters."""

    def test_equivalent_requests(self) -> None:
        copilot = CopilotAdapter()
        claude = ClaudeCodeAdapter()

        copilot_req = copilot.normalize_request(
            {
                "conversationId": "req-1",
                "prompt": "Build API",
                "repository": {"fullName": "org/repo"},
                "constraints": ["fast"],
                "metadata": {},
            },
        )
        claude_req = claude.normalize_request(
            {
                "sessionId": "req-1",
                "message": "Build API",
                "project": {"path": "org/repo"},
                "constraints": ["fast"],
                "context": {},
            },
        )

        assert copilot_req.request_id == claude_req.request_id
        assert copilot_req.user_prompt == claude_req.user_prompt
        assert copilot_req.repository_context == claude_req.repository_context
        assert copilot_req.constraints == claude_req.constraints

    def test_equivalent_responses(self) -> None:
        copilot = CopilotAdapter()
        claude = ClaudeCodeAdapter()

        output = _graph_output()
        r1 = copilot.normalize_response(output)
        r2 = claude.normalize_response(output)

        assert r1.request_id == r2.request_id
        assert r1.terminal_status == r2.terminal_status
        assert r1.produced_artifacts == r2.produced_artifacts
