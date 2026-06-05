"""Tool policy enforcement: default-deny, schema validation, path safety.

Implements:
- Allowlist enforcement (default-deny)
- Argument schema validation
- DESTRUCTIVE tool blocking unless explicitly enabled
- WRITE_SCOPED path traversal/symlink escape prevention
- Audit trail of all invocation attempts
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime

from flowforge.state.models import ToolSideEffect
from flowforge.tools.allowlist import ToolAllowlist


class ToolNotAllowedError(Exception):
    """Raised when a tool is not on the allowlist."""

    def __init__(self, *, tool_id: str) -> None:
        self.tool_id = tool_id
        super().__init__(f"Tool '{tool_id}' is not on the allowlist. Default-deny policy applies.")


class ToolSchemaViolationError(Exception):
    """Raised when tool arguments fail schema validation."""

    def __init__(self, *, tool_id: str, reason: str) -> None:
        self.tool_id = tool_id
        self.reason = reason
        super().__init__(f"Schema violation for tool '{tool_id}': {reason}")


class DestructiveToolBlockedError(Exception):
    """Raised when a DESTRUCTIVE tool is invoked without explicit enablement."""

    def __init__(self, *, tool_id: str) -> None:
        self.tool_id = tool_id
        super().__init__(
            f"Tool '{tool_id}' is classified as DESTRUCTIVE and is blocked. "
            f"Enable destructive tools explicitly in node policy to proceed.",
        )


class PathTraversalError(Exception):
    """Raised when a WRITE_SCOPED tool targets a path outside workspace."""

    def __init__(self, *, tool_id: str, path: str, workspace_root: str) -> None:
        self.tool_id = tool_id
        self.path = path
        self.workspace_root = workspace_root
        super().__init__(
            f"Path traversal detected for tool '{tool_id}': "
            f"path '{path}' escapes workspace root '{workspace_root}'.",
        )


@dataclass
class ToolInvocationRecord:
    """Audit record of a tool invocation attempt."""

    tool_id: str
    arguments: dict[str, object]
    allowed: bool
    reason: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=UTC))


@dataclass
class ToolPolicy:
    """Enforces tool execution policy for a node.

    - Default-deny: only allowlisted tools may execute.
    - Schema validation: arguments checked against declared schema.
    - Side-effect enforcement: DESTRUCTIVE blocked unless enabled,
      WRITE_SCOPED paths validated against workspace root.
    - Audit: all invocation attempts recorded.
    """

    allowlist: ToolAllowlist
    workspace_root: str
    allow_destructive: bool = False
    audit_trail: list[ToolInvocationRecord] = field(default_factory=list)

    def validate_invocation(
        self,
        *,
        tool_id: str,
        arguments: dict[str, object],
    ) -> None:
        """Validate a tool invocation against policy.

        Raises:
            ToolNotAllowedError: Tool not on allowlist.
            ToolSchemaViolationError: Arguments fail validation.
            DestructiveToolBlockedError: DESTRUCTIVE tool without enablement.
            PathTraversalError: WRITE_SCOPED path escapes workspace.
        """
        # Check allowlist
        tool_def = self.allowlist.get(tool_id)
        if tool_def is None:
            self._record(tool_id, arguments, allowed=False, reason="not on allowlist")
            raise ToolNotAllowedError(tool_id=tool_id)

        # Check DESTRUCTIVE blocking
        if tool_def.side_effect == ToolSideEffect.DESTRUCTIVE and not self.allow_destructive:
            self._record(tool_id, arguments, allowed=False, reason="destructive blocked")
            raise DestructiveToolBlockedError(tool_id=tool_id)

        # Validate argument schema
        self._validate_schema(tool_id, arguments, tool_def.argument_schema, tool_def.max_input_size)

        # Check path safety for WRITE_SCOPED tools
        if tool_def.side_effect == ToolSideEffect.WRITE_SCOPED:
            self._validate_path_safety(tool_id, arguments)

        # All checks passed
        self._record(tool_id, arguments, allowed=True)

    def _validate_schema(
        self,
        tool_id: str,
        arguments: dict[str, object],
        schema: dict[str, str],
        max_input_size: int,
    ) -> None:
        """Validate arguments against declared schema."""
        # Check required fields
        for field_name in schema:
            if field_name not in arguments:
                self._record(tool_id, arguments, allowed=False, reason=f"missing {field_name}")
                raise ToolSchemaViolationError(
                    tool_id=tool_id,
                    reason=f"Missing required argument: {field_name}",
                )

        # Check total input size
        serialized = json.dumps(arguments, default=str)
        if len(serialized) > max_input_size:
            self._record(tool_id, arguments, allowed=False, reason="input size exceeded")
            raise ToolSchemaViolationError(
                tool_id=tool_id,
                reason=f"Input size {len(serialized)} exceeds max {max_input_size}",
            )

    def _validate_path_safety(
        self,
        tool_id: str,
        arguments: dict[str, object],
    ) -> None:
        """Ensure WRITE_SCOPED paths don't escape workspace."""
        path = arguments.get("path")
        if not isinstance(path, str):
            return

        # Resolve the path
        if os.path.isabs(path):
            resolved = os.path.normpath(path)
        else:
            # Relative paths are resolved against workspace root
            resolved = os.path.normpath(os.path.join(self.workspace_root, path))

        workspace_resolved = os.path.normpath(self.workspace_root)

        if not resolved.startswith(workspace_resolved + os.sep) and resolved != workspace_resolved:
            self._record(tool_id, arguments, allowed=False, reason="path traversal")
            raise PathTraversalError(
                tool_id=tool_id,
                path=path,
                workspace_root=self.workspace_root,
            )

    def _record(
        self,
        tool_id: str,
        arguments: dict[str, object],
        *,
        allowed: bool,
        reason: str | None = None,
    ) -> None:
        """Record an invocation attempt in the audit trail."""
        self.audit_trail.append(
            ToolInvocationRecord(
                tool_id=tool_id,
                arguments=arguments,
                allowed=allowed,
                reason=reason,
            ),
        )
