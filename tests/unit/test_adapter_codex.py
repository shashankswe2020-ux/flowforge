"""Unit tests for Codex adapter."""

from __future__ import annotations

from src.adapters.codex import CodexAdapter
from src.adapters.copilot import CopilotAdapter
from src.adapters.schemas import CanonicalRequest, CanonicalResponse


def _codex_input(
    *,
    prompt: str = "Build an auth module",
    repo: str = "github.com/org/project",
    task_id: str = "task-456",
) -> dict[str, object]:
    """Simulated Codex CLI input."""
    return {
        "taskId": task_id,
        "instruction": prompt,
        "repo": repo,
        "branch": "main",
        "constraints": ["no-external-deps"],
        "config": {"model": "codex-1", "sandboxed": True},
    }


def _graph_output(
    *,
    request_id: str = "task-456",
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
    """CodexAdapter normalizes Codex input to canonical_request."""

    def test_produces_canonical_request(self) -> None:
        adapter = CodexAdapter()
        result = adapter.normalize_request(_codex_input())
        assert isinstance(result, CanonicalRequest)

    def test_maps_task_id_to_request_id(self) -> None:
        adapter = CodexAdapter()
        result = adapter.normalize_request(_codex_input(task_id="t-99"))
        assert result.request_id == "t-99"

    def test_maps_instruction_to_prompt(self) -> None:
        adapter = CodexAdapter()
        result = adapter.normalize_request(_codex_input(prompt="Deploy"))
        assert result.user_prompt == "Deploy"

    def test_maps_repo(self) -> None:
        adapter = CodexAdapter()
        result = adapter.normalize_request(_codex_input(repo="github.com/x/y"))
        assert result.repository_context == "github.com/x/y"

    def test_sets_assistant_provider(self) -> None:
        adapter = CodexAdapter()
        result = adapter.normalize_request(_codex_input())
        assert result.assistant_provider == "codex"

    def test_passes_constraints(self) -> None:
        adapter = CodexAdapter()
        result = adapter.normalize_request(_codex_input())
        assert result.constraints == ["no-external-deps"]


class TestNormalizeResponse:
    """CodexAdapter normalizes graph output to Codex response format."""

    def test_produces_canonical_response(self) -> None:
        adapter = CodexAdapter()
        result = adapter.normalize_response(_graph_output())
        assert isinstance(result, CanonicalResponse)

    def test_maps_status(self) -> None:
        adapter = CodexAdapter()
        result = adapter.normalize_response(_graph_output(status="blocked"))
        assert result.terminal_status == "blocked"

    def test_maps_artifacts(self) -> None:
        adapter = CodexAdapter()
        result = adapter.normalize_response(_graph_output())
        assert result.produced_artifacts == ["src/auth.py"]


class TestErrorMapping:
    """CodexAdapter maps errors to IntegrationError."""

    def test_maps_exception(self) -> None:
        adapter = CodexAdapter()
        err = adapter.map_error(RuntimeError("sandbox timeout"))
        assert err.adapter_id == "codex"
        assert "sandbox timeout" in err.message


class TestCrossAdapterEquivalence:
    """Equivalent inputs produce equivalent canonical outputs across adapters."""

    def test_equivalent_requests(self) -> None:
        """Same semantic input through Copilot and Codex produces same canonical fields."""
        copilot = CopilotAdapter()
        codex = CodexAdapter()

        copilot_req = copilot.normalize_request(
            {
                "conversationId": "req-1",
                "prompt": "Build API",
                "repository": {"fullName": "org/repo"},
                "constraints": ["fast"],
                "metadata": {},
            },
        )
        codex_req = codex.normalize_request(
            {
                "taskId": "req-1",
                "instruction": "Build API",
                "repo": "org/repo",
                "constraints": ["fast"],
                "config": {},
            },
        )

        assert copilot_req.request_id == codex_req.request_id
        assert copilot_req.user_prompt == codex_req.user_prompt
        assert copilot_req.repository_context == codex_req.repository_context
        assert copilot_req.constraints == codex_req.constraints

    def test_equivalent_responses(self) -> None:
        """Same graph output produces same canonical response across adapters."""
        copilot = CopilotAdapter()
        codex = CodexAdapter()

        output = _graph_output()
        r1 = copilot.normalize_response(output)
        r2 = codex.normalize_response(output)

        assert r1.request_id == r2.request_id
        assert r1.terminal_status == r2.terminal_status
        assert r1.produced_artifacts == r2.produced_artifacts
