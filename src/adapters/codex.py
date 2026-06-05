"""Codex adapter — request/response normalization for OpenAI Codex CLI."""

from __future__ import annotations

from src.adapters.base import AdapterBase, IntegrationError
from src.adapters.schemas import CanonicalRequest, CanonicalResponse


class CodexAdapter(AdapterBase):
    """Adapter for OpenAI Codex CLI integration.

    Normalizes Codex-specific input format (taskId, instruction, repo string)
    into the canonical schema and maps graph output back.
    """

    adapter_id: str = "codex"
    auth_mode: str = "api_key"

    def normalize_request(self, raw_input: dict[str, object]) -> CanonicalRequest:
        """Normalize Codex CLI input to canonical request."""
        constraints_raw = raw_input.get("constraints", [])
        constraints: list[str] = []
        if isinstance(constraints_raw, list):
            constraints = [str(c) for c in constraints_raw]

        config_raw = raw_input.get("config", {})
        metadata: dict[str, object] = {}
        if isinstance(config_raw, dict):
            metadata = dict(config_raw)

        return CanonicalRequest(
            request_id=str(raw_input.get("taskId", "")),
            assistant_provider="codex",
            user_prompt=str(raw_input.get("instruction", "")),
            repository_context=str(raw_input.get("repo", "")),
            constraints=constraints,
            metadata=metadata,
        )

    def normalize_response(self, state: dict[str, object]) -> CanonicalResponse:
        """Normalize graph output state to canonical response."""
        artifacts_raw = state.get("artifacts", [])
        artifacts: list[str] = []
        if isinstance(artifacts_raw, list):
            artifacts = [str(a) for a in artifacts_raw]

        issues_raw = state.get("triaged_issues", [])
        issues: list[str] = []
        if isinstance(issues_raw, list):
            issues = [str(i) for i in issues_raw]

        shipping_readiness: dict[str, object] = {}
        sr_raw = state.get("shipping_readiness")
        if isinstance(sr_raw, dict):
            shipping_readiness = dict(sr_raw)

        shipping_result: dict[str, object] = {}
        sres_raw = state.get("shipping_result")
        if isinstance(sres_raw, dict):
            shipping_result = dict(sres_raw)

        return CanonicalResponse(
            request_id=str(state.get("request_id", "")),
            run_id=str(state.get("run_id", "")),
            terminal_status=str(state.get("run_status", "")),
            produced_artifacts=artifacts,
            triaged_issues=issues,
            shipping_readiness=shipping_readiness,
            shipping_result=shipping_result,
        )

    def map_error(self, error: Exception) -> IntegrationError:
        """Map exception to Codex-specific IntegrationError."""
        return IntegrationError(
            adapter_id=self.adapter_id,
            original_error=error,
            message=str(error),
        )
