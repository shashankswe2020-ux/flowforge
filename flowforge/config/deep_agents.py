"""Resolve the ``deep_agents`` setting from CLI / env / config / default.

Resolution priority (high → low) per spec §9 / plan T11:

1. Explicit ``cli_value`` argument (the click flag);
2. ``FLOWFORGE_DEEP_AGENTS`` environment variable;
3. ``deep_agents`` field on the persisted
   :class:`flowforge.cli.config.FlowForgeConfig`;
4. Hard-coded default (``False``).
"""

from __future__ import annotations

import os
from typing import Final

__all__ = ["DEEP_AGENTS_ENV_VAR", "resolve_deep_agents_enabled"]

DEEP_AGENTS_ENV_VAR: Final[str] = "FLOWFORGE_DEEP_AGENTS"

_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})
_FALSY: Final[frozenset[str]] = frozenset({"0", "false", "no", "off"})


def _parse_env_value(raw: str) -> bool:
    lowered = raw.strip().lower()
    if lowered in _TRUTHY:
        return True
    if lowered in _FALSY:
        return False
    raise ValueError(
        f"{DEEP_AGENTS_ENV_VAR} must be one of "
        f"{sorted(_TRUTHY | _FALSY)}, got {raw!r}",
    )


def resolve_deep_agents_enabled(*, cli_value: bool | None = None) -> bool:
    """Return whether the Deep Agents path should be used for this run.

    Args:
        cli_value: The tri-state click flag value. ``None`` means the
            user passed neither ``--use-deep-agents`` nor
            ``--no-deep-agents``.

    Returns:
        Resolved boolean per the priority chain above.

    Raises:
        ValueError: If ``FLOWFORGE_DEEP_AGENTS`` is set to a value
            outside the recognised truthy / falsy strings.
    """
    if cli_value is not None:
        return cli_value

    raw = os.environ.get(DEEP_AGENTS_ENV_VAR)
    if raw is not None:
        return _parse_env_value(raw)

    # Lazy import to avoid a hard dependency cycle: config → cli.config
    # is fine because cli.config does not import config.
    from flowforge.cli.config import FlowForgeConfig

    if FlowForgeConfig.exists():
        return FlowForgeConfig.load().deep_agents

    return False
