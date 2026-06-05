"""GitHub Copilot adapter — request/response normalization for Copilot Chat."""

from __future__ import annotations

from flowforge.adapters.base import AdapterBase, IntegrationError
from flowforge.adapters.schemas import CanonicalRequest, CanonicalResponse


class CopilotAdapter(AdapterBase):
    """Adapter for GitHub Copilot Chat integration.

    Normalizes Copilot-specific input format (conversationId, repository object)
    into the canonical schema and maps graph output back.
    """

    adapter_id: str = "copilot"
    auth_mode: str = "github_token"

    def normalize_request(self, raw_input: dict[str, object]) -> CanonicalRequest:
        """Normalize Copilot Chat input to canonical request."""
        repo_obj = raw_input.get("repository", {})
        repo_context = ""
        if isinstance(repo_obj, dict):
            repo_context = str(repo_obj.get("fullName", ""))

        constraints_raw = raw_input.get("constraints", [])
        constraints: list[str] = []
        if isinstance(constraints_raw, list):
            constraints = [str(c) for c in constraints_raw]

        metadata_raw = raw_input.get("metadata", {})
        metadata: dict[str, object] = {}
        if isinstance(metadata_raw, dict):
            metadata = dict(metadata_raw)

        return CanonicalRequest(
            request_id=str(raw_input.get("conversationId", "")),
            assistant_provider="copilot",
            user_prompt=str(raw_input.get("prompt", "")),
            repository_context=repo_context,
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
        """Map exception to Copilot-specific IntegrationError."""
        return IntegrationError(
            adapter_id=self.adapter_id,
            original_error=error,
            message=str(error),
        )
