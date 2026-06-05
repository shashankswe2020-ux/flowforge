"""Unit tests for model selection resolver."""

from __future__ import annotations

import pytest

from flowforge.config.model_selection import (
    InvalidModelConfigError,
    resolve_model,
)
from flowforge.state.models import (
    CapabilityType,
    DefaultModelConfig,
    GraphState,
    NodeModelOverride,
    RunStatus,
)


def _state_with_config(
    *,
    default: DefaultModelConfig | None = None,
    overrides: list[NodeModelOverride] | None = None,
) -> GraphState:
    """Build a minimal GraphState with model config."""
    return GraphState(
        request="test",
        run_status=RunStatus.RUNNING,
        default_model_config=default,
        node_model_overrides=overrides or [],
    )


class TestPrecedenceOrder:
    """Model resolution follows nodeModelOverrides → defaultModelConfig → fallback."""

    def test_node_override_takes_precedence_over_default(self) -> None:
        """Node-level override wins over run-level default."""
        state = _state_with_config(
            default=DefaultModelConfig(model_id="gpt-4o", provider="openai"),
            overrides=[
                NodeModelOverride(
                    node_id="spec_node",
                    model_id="claude-4",
                    provider="anthropic",
                    temperature=0.2,
                ),
            ],
        )
        result = resolve_model("spec_node", state, CapabilityType.AGENT_ONLY)
        assert result.model_id == "claude-4"
        assert result.provider == "anthropic"
        assert result.temperature == 0.2

    def test_default_config_used_when_no_override(self) -> None:
        """Falls back to defaultModelConfig when no node override exists."""
        state = _state_with_config(
            default=DefaultModelConfig(
                model_id="gpt-4o",
                provider="openai",
                temperature=0.1,
                max_tokens=8192,
            ),
        )
        result = resolve_model("plan_node", state, CapabilityType.AGENT_WITH_TOOLS)
        assert result.model_id == "gpt-4o"
        assert result.provider == "openai"
        assert result.temperature == 0.1
        assert result.max_tokens == 8192

    def test_system_fallback_when_no_config_at_all(self) -> None:
        """Uses system fallback when neither override nor default is set."""
        state = _state_with_config()
        result = resolve_model("clarification_node", state, CapabilityType.AGENT_ONLY)
        assert result.model_id is not None
        assert result.provider is not None

    def test_override_inherits_defaults_for_unset_fields(self) -> None:
        """Override with None temperature/max_tokens inherits from default."""
        state = _state_with_config(
            default=DefaultModelConfig(
                model_id="gpt-4o",
                provider="openai",
                temperature=0.5,
                max_tokens=2048,
            ),
            overrides=[
                NodeModelOverride(
                    node_id="spec_node",
                    model_id="claude-4",
                    provider="anthropic",
                    # temperature and max_tokens are None → inherit from default
                ),
            ],
        )
        result = resolve_model("spec_node", state, CapabilityType.AGENT_ONLY)
        assert result.model_id == "claude-4"
        assert result.temperature == 0.5
        assert result.max_tokens == 2048


class TestDirectToolNodes:
    """DIRECT_TOOL nodes skip model resolution without error."""

    def test_direct_tool_returns_none(self) -> None:
        """DIRECT_TOOL capability type returns None (no model needed)."""
        state = _state_with_config(
            default=DefaultModelConfig(model_id="gpt-4o", provider="openai"),
        )
        result = resolve_model("task_fanout_router", state, CapabilityType.DIRECT_TOOL)
        assert result is None

    def test_direct_tool_with_override_still_returns_none(self) -> None:
        """Even if an override exists for a DIRECT_TOOL node, skip model resolution."""
        state = _state_with_config(
            default=DefaultModelConfig(model_id="gpt-4o", provider="openai"),
            overrides=[
                NodeModelOverride(
                    node_id="task_fanout_router",
                    model_id="claude-4",
                    provider="anthropic",
                ),
            ],
        )
        result = resolve_model("task_fanout_router", state, CapabilityType.DIRECT_TOOL)
        assert result is None


class TestInvalidModelConfig:
    """Unknown model IDs raise InvalidModelConfigError with remediation."""

    def test_invalid_override_model_id_raises(self) -> None:
        """Override with an unknown model ID raises typed error."""
        state = _state_with_config(
            overrides=[
                NodeModelOverride(
                    node_id="spec_node",
                    model_id="nonexistent-model-xyz",
                    provider="unknown-provider",
                ),
            ],
        )
        with pytest.raises(InvalidModelConfigError) as exc_info:
            resolve_model(
                "spec_node",
                state,
                CapabilityType.AGENT_ONLY,
                allowed_models={"gpt-4o", "claude-4"},
            )
        err = exc_info.value
        assert err.model_id == "nonexistent-model-xyz"
        assert err.node_id == "spec_node"
        assert "remediation" in err.remediation.lower() or len(err.remediation) > 0

    def test_invalid_default_model_id_raises(self) -> None:
        """Default with an unknown model ID raises typed error."""
        state = _state_with_config(
            default=DefaultModelConfig(model_id="bad-model", provider="unknown"),
        )
        with pytest.raises(InvalidModelConfigError) as exc_info:
            resolve_model(
                "plan_node",
                state,
                CapabilityType.AGENT_ONLY,
                allowed_models={"gpt-4o", "claude-4"},
            )
        assert exc_info.value.model_id == "bad-model"

    def test_no_validation_when_allowed_models_not_specified(self) -> None:
        """When allowed_models is None, any model ID is accepted."""
        state = _state_with_config(
            default=DefaultModelConfig(model_id="any-model-works", provider="custom"),
        )
        result = resolve_model("spec_node", state, CapabilityType.AGENT_ONLY)
        assert result is not None
        assert result.model_id == "any-model-works"


class TestResolvedModelShape:
    """ResolvedModel has correct structure."""

    def test_resolved_model_has_required_fields(self) -> None:
        """ResolvedModel contains model_id, provider, temperature, max_tokens."""
        state = _state_with_config(
            default=DefaultModelConfig(model_id="gpt-4o", provider="openai"),
        )
        result = resolve_model("spec_node", state, CapabilityType.AGENT_ONLY)
        assert result is not None
        assert hasattr(result, "model_id")
        assert hasattr(result, "provider")
        assert hasattr(result, "temperature")
        assert hasattr(result, "max_tokens")

    def test_resolved_model_includes_additional_params(self) -> None:
        """Additional params are passed through."""
        state = _state_with_config(
            default=DefaultModelConfig(
                model_id="gpt-4o",
                provider="openai",
                additional_params={"top_p": 0.9},
            ),
        )
        result = resolve_model("spec_node", state, CapabilityType.AGENT_ONLY)
        assert result is not None
        assert result.additional_params == {"top_p": 0.9}
