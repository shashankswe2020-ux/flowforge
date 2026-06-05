"""Model selection resolver with 3-tier precedence."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.state.models import CapabilityType, GraphState

# System fallback when no user config is provided
_SYSTEM_FALLBACK_MODEL_ID = "gpt-4o"
_SYSTEM_FALLBACK_PROVIDER = "openai"
_SYSTEM_FALLBACK_TEMPERATURE = 0.0
_SYSTEM_FALLBACK_MAX_TOKENS = 4096


class InvalidModelConfigError(Exception):
    """Raised when a model ID is not in the allowed set."""

    def __init__(self, *, model_id: str, node_id: str, allowed: set[str]) -> None:
        self.model_id = model_id
        self.node_id = node_id
        self.allowed = allowed
        self.remediation = (
            f"Model '{model_id}' is not recognized for node '{node_id}'. "
            f"Allowed models: {sorted(allowed)}. "
            f"Update your defaultModelConfig or nodeModelOverrides with a valid model ID."
        )
        super().__init__(self.remediation)


@dataclass(frozen=True)
class ResolvedModel:
    """Effective model configuration after resolution."""

    model_id: str
    provider: str
    temperature: float
    max_tokens: int
    additional_params: dict[str, str | int | float | bool] = field(default_factory=dict)


def resolve_model(
    node_id: str,
    state: GraphState,
    capability_type: CapabilityType,
    *,
    allowed_models: set[str] | None = None,
) -> ResolvedModel | None:
    """Resolve effective model for a node following 3-tier precedence.

    Resolution order:
        1. nodeModelOverrides[node_id]
        2. defaultModelConfig
        3. system fallback

    Args:
        node_id: Canonical node identifier.
        state: Current graph state containing model configuration.
        capability_type: Node's declared capability type.
        allowed_models: Optional set of valid model IDs. If provided, resolved
            model_id is validated against this set.

    Returns:
        ResolvedModel with effective configuration, or None for DIRECT_TOOL nodes.

    Raises:
        InvalidModelConfigError: If resolved model_id is not in allowed_models.
    """
    # DIRECT_TOOL nodes skip model resolution entirely
    if capability_type == CapabilityType.DIRECT_TOOL:
        return None

    # Tier 1: Check node-level override
    override = next(
        (o for o in state.node_model_overrides if o.node_id == node_id),
        None,
    )

    # Tier 2: Default config
    default = state.default_model_config

    # Resolve model_id and provider
    if override is not None:
        model_id = override.model_id
        provider = override.provider
        # Inherit unset fields from default
        temperature = (
            override.temperature
            if override.temperature is not None
            else (default.temperature if default else _SYSTEM_FALLBACK_TEMPERATURE)
        )
        max_tokens = (
            override.max_tokens
            if override.max_tokens is not None
            else (default.max_tokens if default else _SYSTEM_FALLBACK_MAX_TOKENS)
        )
        additional_params = override.additional_params or (
            default.additional_params if default else {}
        )
    elif default is not None:
        # Tier 2: Use default config
        model_id = default.model_id
        provider = default.provider
        temperature = default.temperature
        max_tokens = default.max_tokens
        additional_params = default.additional_params
    else:
        # Tier 3: System fallback
        model_id = _SYSTEM_FALLBACK_MODEL_ID
        provider = _SYSTEM_FALLBACK_PROVIDER
        temperature = _SYSTEM_FALLBACK_TEMPERATURE
        max_tokens = _SYSTEM_FALLBACK_MAX_TOKENS
        additional_params = {}

    # Validate against allowed models if specified
    if allowed_models is not None and model_id not in allowed_models:
        raise InvalidModelConfigError(
            model_id=model_id,
            node_id=node_id,
            allowed=allowed_models,
        )

    return ResolvedModel(
        model_id=model_id,
        provider=provider,
        temperature=temperature,
        max_tokens=max_tokens,
        additional_params=additional_params,
    )
