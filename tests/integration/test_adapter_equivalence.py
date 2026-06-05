"""Cross-adapter equivalence integration test.

Feeds equivalent inputs through all 3 adapters and asserts identical
canonical state transitions and terminal outcomes.
"""

from __future__ import annotations

from flowforge.adapters.claude_code import ClaudeCodeAdapter
from flowforge.adapters.codex import CodexAdapter
from flowforge.adapters.copilot import CopilotAdapter


def _copilot_input() -> dict[str, object]:
    return {
        "conversationId": "req-001",
        "prompt": "Build a REST API with authentication",
        "repository": {"fullName": "org/my-service"},
        "constraints": ["python-only", "no-orm"],
        "metadata": {},
    }


def _codex_input() -> dict[str, object]:
    return {
        "taskId": "req-001",
        "instruction": "Build a REST API with authentication",
        "repo": "org/my-service",
        "constraints": ["python-only", "no-orm"],
        "config": {},
    }


def _claude_input() -> dict[str, object]:
    return {
        "sessionId": "req-001",
        "message": "Build a REST API with authentication",
        "project": {"path": "org/my-service"},
        "constraints": ["python-only", "no-orm"],
        "context": {},
    }


def _graph_output() -> dict[str, object]:
    return {
        "request_id": "req-001",
        "run_id": "run-xyz",
        "run_status": "succeeded",
        "artifacts": ["src/api.py", "src/auth.py", "tests/test_api.py"],
        "triaged_issues": ["issue-abc", "issue-def"],
        "shipping_readiness": {"is_ready": True, "blockers": []},
        "shipping_result": {"shipped": True, "commit_sha": "abc123"},
    }


class TestCanonicalRequestEquivalence:
    """Same user prompt produces same canonical_request across all adapters."""

    def test_all_adapters_produce_same_request_id(self) -> None:
        copilot = CopilotAdapter().normalize_request(_copilot_input())
        codex = CodexAdapter().normalize_request(_codex_input())
        claude = ClaudeCodeAdapter().normalize_request(_claude_input())

        assert copilot.request_id == codex.request_id == claude.request_id == "req-001"

    def test_all_adapters_produce_same_prompt(self) -> None:
        copilot = CopilotAdapter().normalize_request(_copilot_input())
        codex = CodexAdapter().normalize_request(_codex_input())
        claude = ClaudeCodeAdapter().normalize_request(_claude_input())

        expected = "Build a REST API with authentication"
        assert copilot.user_prompt == expected
        assert codex.user_prompt == expected
        assert claude.user_prompt == expected

    def test_all_adapters_produce_same_repo_context(self) -> None:
        copilot = CopilotAdapter().normalize_request(_copilot_input())
        codex = CodexAdapter().normalize_request(_codex_input())
        claude = ClaudeCodeAdapter().normalize_request(_claude_input())

        assert copilot.repository_context == "org/my-service"
        assert codex.repository_context == "org/my-service"
        assert claude.repository_context == "org/my-service"

    def test_all_adapters_produce_same_constraints(self) -> None:
        copilot = CopilotAdapter().normalize_request(_copilot_input())
        codex = CodexAdapter().normalize_request(_codex_input())
        claude = ClaudeCodeAdapter().normalize_request(_claude_input())

        expected = ["python-only", "no-orm"]
        assert copilot.constraints == expected
        assert codex.constraints == expected
        assert claude.constraints == expected

    def test_deterministic_fields_match(self) -> None:
        """All deterministic fields are equal (excluding provider and metadata)."""
        copilot = CopilotAdapter().normalize_request(_copilot_input())
        codex = CodexAdapter().normalize_request(_codex_input())
        claude = ClaudeCodeAdapter().normalize_request(_claude_input())

        # Deterministic fields
        assert copilot.request_id == codex.request_id == claude.request_id
        assert copilot.user_prompt == codex.user_prompt == claude.user_prompt
        assert copilot.repository_context == codex.repository_context == claude.repository_context
        assert copilot.constraints == codex.constraints == claude.constraints

    def test_provider_field_differs(self) -> None:
        """assistant_provider is the only allowed variance in request."""
        copilot = CopilotAdapter().normalize_request(_copilot_input())
        codex = CodexAdapter().normalize_request(_codex_input())
        claude = ClaudeCodeAdapter().normalize_request(_claude_input())

        assert copilot.assistant_provider == "copilot"
        assert codex.assistant_provider == "codex"
        assert claude.assistant_provider == "claude_code"


class TestCanonicalResponseEquivalence:
    """Same graph output produces equivalent canonical_response across adapters."""

    def test_all_adapters_produce_same_terminal_status(self) -> None:
        output = _graph_output()
        copilot = CopilotAdapter().normalize_response(output)
        codex = CodexAdapter().normalize_response(output)
        claude = ClaudeCodeAdapter().normalize_response(output)

        assert copilot.terminal_status == "succeeded"
        assert codex.terminal_status == "succeeded"
        assert claude.terminal_status == "succeeded"

    def test_all_adapters_produce_same_artifacts(self) -> None:
        output = _graph_output()
        copilot = CopilotAdapter().normalize_response(output)
        codex = CodexAdapter().normalize_response(output)
        claude = ClaudeCodeAdapter().normalize_response(output)

        expected = ["src/api.py", "src/auth.py", "tests/test_api.py"]
        assert copilot.produced_artifacts == expected
        assert codex.produced_artifacts == expected
        assert claude.produced_artifacts == expected

    def test_all_adapters_produce_same_issues(self) -> None:
        output = _graph_output()
        copilot = CopilotAdapter().normalize_response(output)
        codex = CodexAdapter().normalize_response(output)
        claude = ClaudeCodeAdapter().normalize_response(output)

        expected = ["issue-abc", "issue-def"]
        assert copilot.triaged_issues == expected
        assert codex.triaged_issues == expected
        assert claude.triaged_issues == expected

    def test_all_adapters_produce_same_shipping(self) -> None:
        output = _graph_output()
        copilot = CopilotAdapter().normalize_response(output)
        codex = CodexAdapter().normalize_response(output)
        claude = ClaudeCodeAdapter().normalize_response(output)

        assert copilot.shipping_readiness == codex.shipping_readiness == claude.shipping_readiness
        assert copilot.shipping_result == codex.shipping_result == claude.shipping_result

    def test_full_response_equality(self) -> None:
        """All deterministic response fields are equal across adapters."""
        output = _graph_output()
        copilot = CopilotAdapter().normalize_response(output)
        codex = CodexAdapter().normalize_response(output)
        claude = ClaudeCodeAdapter().normalize_response(output)

        # Full equality (all fields are deterministic for response)
        assert copilot == codex == claude


class TestAdapterRuntimeSelection:
    """Adapter selection is runtime-configurable."""

    def test_select_adapter_by_name(self) -> None:
        """Adapters can be selected at runtime from a registry."""
        registry: dict[str, type] = {
            "copilot": CopilotAdapter,
            "codex": CodexAdapter,
            "claude_code": ClaudeCodeAdapter,
        }

        for name, adapter_cls in registry.items():
            adapter = adapter_cls()
            assert adapter.adapter_id == name

    def test_all_adapters_share_base_interface(self) -> None:
        """All adapters expose the same interface methods."""
        from flowforge.adapters.base import AdapterBase

        adapters = [CopilotAdapter(), CodexAdapter(), ClaudeCodeAdapter()]
        for adapter in adapters:
            assert isinstance(adapter, AdapterBase)
            assert hasattr(adapter, "normalize_request")
            assert hasattr(adapter, "normalize_response")
            assert hasattr(adapter, "map_error")
