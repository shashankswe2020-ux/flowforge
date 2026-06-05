"""Tool allowlist definitions.

Each allowlisted tool declares its ID, side-effect class,
argument schema, and max input size.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.state.models import ToolSideEffect


@dataclass(frozen=True)
class ToolDefinition:
    """Definition of an allowlisted tool."""

    tool_id: str
    side_effect: ToolSideEffect
    argument_schema: dict[str, str] = field(default_factory=dict)
    max_input_size: int = 65536


@dataclass
class ToolAllowlist:
    """Set of allowed tools for a node."""

    tools: list[ToolDefinition] = field(default_factory=list)

    def get(self, tool_id: str) -> ToolDefinition | None:
        """Look up a tool by ID."""
        return next((t for t in self.tools if t.tool_id == tool_id), None)

    def is_allowed(self, tool_id: str) -> bool:
        """Check if a tool is on the allowlist."""
        return any(t.tool_id == tool_id for t in self.tools)
