"""Unit tests for tool policy: allowlist, schema validation, side-effect enforcement."""

from __future__ import annotations

import pytest

from flowforge.state.models import ToolSideEffect
from flowforge.tools.allowlist import ToolAllowlist, ToolDefinition
from flowforge.tools.policy import (
    DestructiveToolBlockedError,
    PathTraversalError,
    ToolNotAllowedError,
    ToolPolicy,
    ToolSchemaViolationError,
)


def _read_only_tool() -> ToolDefinition:
    return ToolDefinition(
        tool_id="read_file",
        side_effect=ToolSideEffect.READ_ONLY,
        argument_schema={"path": "str"},
        max_input_size=1024,
    )


def _write_scoped_tool() -> ToolDefinition:
    return ToolDefinition(
        tool_id="write_file",
        side_effect=ToolSideEffect.WRITE_SCOPED,
        argument_schema={"path": "str", "content": "str"},
        max_input_size=65536,
    )


def _destructive_tool() -> ToolDefinition:
    return ToolDefinition(
        tool_id="delete_repo",
        side_effect=ToolSideEffect.DESTRUCTIVE,
        argument_schema={"repo": "str"},
        max_input_size=256,
    )


def _basic_allowlist() -> ToolAllowlist:
    return ToolAllowlist(tools=[_read_only_tool(), _write_scoped_tool()])


class TestAllowlistEnforcement:
    """Non-allowlisted tool calls are rejected."""

    def test_allowed_tool_passes(self) -> None:
        """Tool on allowlist is permitted."""
        policy = ToolPolicy(
            allowlist=_basic_allowlist(),
            workspace_root="/workspace",
        )
        # Should not raise
        policy.validate_invocation(
            tool_id="read_file",
            arguments={"path": "/workspace/src/main.py"},
        )

    def test_non_allowed_tool_rejected(self) -> None:
        """Tool not on allowlist raises ToolNotAllowedError."""
        policy = ToolPolicy(
            allowlist=_basic_allowlist(),
            workspace_root="/workspace",
        )
        with pytest.raises(ToolNotAllowedError) as exc_info:
            policy.validate_invocation(
                tool_id="exec_shell",
                arguments={"cmd": "rm -rf /"},
            )
        assert exc_info.value.tool_id == "exec_shell"

    def test_empty_allowlist_rejects_all(self) -> None:
        """With no tools allowlisted, everything is rejected."""
        policy = ToolPolicy(
            allowlist=ToolAllowlist(tools=[]),
            workspace_root="/workspace",
        )
        with pytest.raises(ToolNotAllowedError):
            policy.validate_invocation(tool_id="read_file", arguments={})


class TestArgumentSchemaValidation:
    """Argument schemas validated before invocation."""

    def test_valid_arguments_pass(self) -> None:
        """Arguments matching schema are accepted."""
        policy = ToolPolicy(
            allowlist=_basic_allowlist(),
            workspace_root="/workspace",
        )
        policy.validate_invocation(
            tool_id="read_file",
            arguments={"path": "/workspace/file.txt"},
        )

    def test_missing_required_argument_rejected(self) -> None:
        """Missing required argument raises ToolSchemaViolationError."""
        policy = ToolPolicy(
            allowlist=_basic_allowlist(),
            workspace_root="/workspace",
        )
        with pytest.raises(ToolSchemaViolationError) as exc_info:
            policy.validate_invocation(
                tool_id="read_file",
                arguments={},  # missing 'path'
            )
        assert "path" in str(exc_info.value)

    def test_oversized_input_rejected(self) -> None:
        """Arguments exceeding max_input_size are rejected."""
        policy = ToolPolicy(
            allowlist=_basic_allowlist(),
            workspace_root="/workspace",
        )
        with pytest.raises(ToolSchemaViolationError) as exc_info:
            policy.validate_invocation(
                tool_id="read_file",
                arguments={"path": "x" * 2000},  # exceeds 1024
            )
        assert "size" in str(exc_info.value).lower()


class TestDestructiveToolBlocking:
    """DESTRUCTIVE tools blocked unless explicitly enabled."""

    def test_destructive_tool_blocked_by_default(self) -> None:
        """DESTRUCTIVE tool raises even if on allowlist."""
        allowlist = ToolAllowlist(tools=[_destructive_tool()])
        policy = ToolPolicy(
            allowlist=allowlist,
            workspace_root="/workspace",
            allow_destructive=False,
        )
        with pytest.raises(DestructiveToolBlockedError) as exc_info:
            policy.validate_invocation(
                tool_id="delete_repo",
                arguments={"repo": "my-repo"},
            )
        assert exc_info.value.tool_id == "delete_repo"

    def test_destructive_tool_allowed_when_enabled(self) -> None:
        """DESTRUCTIVE tool passes when explicitly enabled."""
        allowlist = ToolAllowlist(tools=[_destructive_tool()])
        policy = ToolPolicy(
            allowlist=allowlist,
            workspace_root="/workspace",
            allow_destructive=True,
        )
        # Should not raise
        policy.validate_invocation(
            tool_id="delete_repo",
            arguments={"repo": "my-repo"},
        )


class TestPathTraversalProtection:
    """WRITE_SCOPED tools reject path traversal and symlink escape."""

    def test_path_within_workspace_allowed(self) -> None:
        """Paths within workspace root pass."""
        policy = ToolPolicy(
            allowlist=_basic_allowlist(),
            workspace_root="/workspace",
        )
        policy.validate_invocation(
            tool_id="write_file",
            arguments={"path": "/workspace/src/main.py", "content": "hello"},
        )

    def test_path_traversal_rejected(self) -> None:
        """Path with .. that escapes workspace is rejected."""
        policy = ToolPolicy(
            allowlist=_basic_allowlist(),
            workspace_root="/workspace",
        )
        with pytest.raises(PathTraversalError) as exc_info:
            policy.validate_invocation(
                tool_id="write_file",
                arguments={"path": "/workspace/../etc/passwd", "content": "bad"},
            )
        assert "traversal" in str(exc_info.value).lower()

    def test_absolute_path_outside_workspace_rejected(self) -> None:
        """Absolute path outside workspace is rejected."""
        policy = ToolPolicy(
            allowlist=_basic_allowlist(),
            workspace_root="/workspace",
        )
        with pytest.raises(PathTraversalError):
            policy.validate_invocation(
                tool_id="write_file",
                arguments={"path": "/etc/passwd", "content": "bad"},
            )

    def test_relative_path_accepted(self) -> None:
        """Relative paths (within workspace) are accepted."""
        policy = ToolPolicy(
            allowlist=_basic_allowlist(),
            workspace_root="/workspace",
        )
        policy.validate_invocation(
            tool_id="write_file",
            arguments={"path": "src/main.py", "content": "hello"},
        )


class TestAuditTrace:
    """All invocations logged in audit trace."""

    def test_successful_invocation_logged(self) -> None:
        """Successful validation is recorded in audit trail."""
        policy = ToolPolicy(
            allowlist=_basic_allowlist(),
            workspace_root="/workspace",
        )
        policy.validate_invocation(
            tool_id="read_file",
            arguments={"path": "/workspace/file.txt"},
        )
        assert len(policy.audit_trail) == 1
        record = policy.audit_trail[0]
        assert record.tool_id == "read_file"
        assert record.allowed is True

    def test_rejected_invocation_logged(self) -> None:
        """Rejected invocations are also recorded."""
        policy = ToolPolicy(
            allowlist=_basic_allowlist(),
            workspace_root="/workspace",
        )
        with pytest.raises(ToolNotAllowedError):
            policy.validate_invocation(
                tool_id="bad_tool",
                arguments={},
            )
        assert len(policy.audit_trail) == 1
        record = policy.audit_trail[0]
        assert record.tool_id == "bad_tool"
        assert record.allowed is False

    def test_multiple_invocations_accumulated(self) -> None:
        """Audit trail accumulates across multiple calls."""
        policy = ToolPolicy(
            allowlist=_basic_allowlist(),
            workspace_root="/workspace",
        )
        policy.validate_invocation(tool_id="read_file", arguments={"path": "/workspace/a"})
        policy.validate_invocation(tool_id="read_file", arguments={"path": "/workspace/b"})
        assert len(policy.audit_trail) == 2
