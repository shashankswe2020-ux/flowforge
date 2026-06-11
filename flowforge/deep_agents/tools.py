"""FlowForge Deep Agent tool library + safety policy (T2).

Implements the typed tool functions enumerated in spec §6 and the
``_safe_path`` workdir-confinement helper. Every tool:

* takes a Pydantic-validated input model with no ``Any`` parameters;
* returns a typed result model;
* shells out (when applicable) via ``subprocess.run`` with
  ``shell=False`` and a fixed ``cwd``;
* emits ``tool.invoked`` / ``tool.succeeded`` / ``tool.failed``
  telemetry events through the package logger;
* re-raises policy errors from :mod:`flowforge.tools.policy`.

This module does not register tools with the LangGraph runtime — that
binding lands in T4 (`build_deep_agent`).
"""

from __future__ import annotations

import logging
import os
import subprocess  # noqa: S404 - shell=False enforced everywhere below
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from flowforge.tools.policy import ToolNotAllowedError, ToolSchemaViolationError

__all__ = [
    "CommandResult",
    "GhIssueCreateArgs",
    "GhLabelEnsureArgs",
    "GitDiffArgs",
    "McpInvokeArgs",
    "McpInvokeResult",
    "McpTransport",
    "PathTraversalError",
    "RunTestsArgs",
    "WebSearchArgs",
    "WebSearchResult",
    "WebSearchResultItem",
    "_safe_path",
    "get_mcp_transport",
    "gh_issue_create",
    "gh_label_ensure",
    "git_diff",
    "git_status",
    "mcp_invoke",
    "run_lint",
    "run_tests",
    "run_typecheck",
    "set_mcp_transport",
    "web_search",
]

logger = logging.getLogger("flowforge.deep_agents.tools")

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PathTraversalError(ValueError):
    """Raised when a tool argument resolves outside the agent ``workdir``.

    Tools must never operate on paths that escape the per-run workdir
    via ``..``, absolute paths, or symlinks.
    """


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def _safe_path(workdir: Path, candidate: str | Path) -> Path:
    """Resolve ``candidate`` and confirm it lives inside ``workdir``.

    Args:
        workdir: Per-run agent workdir; the only writeable root.
        candidate: User-supplied path, **must be relative** to ``workdir``.

    Returns:
        The resolved real path, guaranteed to be inside the resolved
        ``workdir``.

    Raises:
        PathTraversalError: If ``workdir`` is missing, if ``candidate``
            is absolute, contains ``..`` segments that escape, or is a
            symlink resolving outside ``workdir``.
    """
    workdir_path = Path(workdir)
    if not workdir_path.is_dir():
        raise PathTraversalError(f"workdir does not exist: {workdir_path}")
    workdir_resolved = workdir_path.resolve(strict=True)

    candidate_path = Path(candidate)
    if candidate_path.is_absolute():
        raise PathTraversalError(f"absolute path not allowed: {candidate}")

    combined = workdir_resolved / candidate_path
    try:
        resolved = combined.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise PathTraversalError(f"could not resolve path: {candidate}") from exc

    try:
        resolved.relative_to(workdir_resolved)
    except ValueError as exc:
        raise PathTraversalError(
            f"path '{candidate}' escapes workdir '{workdir_resolved}'",
        ) from exc
    return resolved


# ---------------------------------------------------------------------------
# Telemetry helper
# ---------------------------------------------------------------------------

def _telemetry[R](tool: str, body: Callable[[], R]) -> R:
    """Invoke ``body`` while emitting structured telemetry events."""
    started_at = time.monotonic()
    logger.info("tool.invoked", extra={"tool": tool})
    try:
        result = body()
    except BaseException as exc:
        duration_ms = int((time.monotonic() - started_at) * 1000)
        logger.info(
            "tool.failed",
            extra={
                "tool": tool,
                "duration_ms": duration_ms,
                "error_type": type(exc).__name__,
            },
        )
        raise
    duration_ms = int((time.monotonic() - started_at) * 1000)
    logger.info(
        "tool.succeeded",
        extra={"tool": tool, "duration_ms": duration_ms},
    )
    return result


def _run_subprocess(argv: list[str], *, workdir: Path) -> CommandResult:
    """Run ``argv`` with ``shell=False`` and capture stdout/stderr."""
    started_at = time.monotonic()
    completed = subprocess.run(  # noqa: S603 - argv is a list, shell=False
        argv,
        cwd=workdir,
        shell=False,
        capture_output=True,
        text=True,
        check=False,
    )
    duration_ms = int((time.monotonic() - started_at) * 1000)
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        duration_ms=duration_ms,
    )


# ---------------------------------------------------------------------------
# Shared models
# ---------------------------------------------------------------------------


class _StrictModel(BaseModel):
    """Base for all tool I/O models — forbids extras, freezes shape."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CommandResult(_StrictModel):
    """Result of a subprocess shell-out."""

    returncode: int
    stdout: str
    stderr: str
    duration_ms: int


# ---------------------------------------------------------------------------
# run_tests / run_lint / run_typecheck
# ---------------------------------------------------------------------------


class RunTestsArgs(_StrictModel):
    """Arguments for :func:`run_tests`."""

    path: str | None = None


def run_tests(*, workdir: Path, path: str | None = None) -> CommandResult:
    """Run ``pytest -q`` (optionally scoped to ``path``) inside ``workdir``."""
    args = RunTestsArgs(path=path)
    argv: list[str] = ["pytest", "-q"]
    if args.path is not None:
        # _safe_path raises PathTraversalError on escape.
        _safe_path(workdir, args.path)
        argv.append(args.path)
    return _telemetry("run_tests", lambda: _run_subprocess(argv, workdir=workdir))


def run_lint(*, workdir: Path) -> CommandResult:
    """Run ``ruff check .`` inside ``workdir``."""
    return _telemetry(
        "run_lint",
        lambda: _run_subprocess(["ruff", "check", "."], workdir=workdir),
    )


def run_typecheck(*, workdir: Path) -> CommandResult:
    """Run ``mypy .`` inside ``workdir``."""
    return _telemetry(
        "run_typecheck",
        lambda: _run_subprocess(["mypy", "."], workdir=workdir),
    )


# ---------------------------------------------------------------------------
# git_status / git_diff
# ---------------------------------------------------------------------------


class GitDiffArgs(_StrictModel):
    """Arguments for :func:`git_diff`."""

    rev: str = "HEAD"


def _validate_revision(rev: str) -> None:
    """Reject revisions that look like CLI flags or contain shell metas."""
    if not rev or rev.startswith("-"):
        raise ToolSchemaViolationError(
            tool_id="git_diff",
            reason=f"revision must not start with '-': {rev!r}",
        )
    forbidden = set(" \t\n\r;&|`$<>")
    if any(ch in forbidden for ch in rev):
        raise ToolSchemaViolationError(
            tool_id="git_diff",
            reason=f"revision contains forbidden characters: {rev!r}",
        )


def git_status(*, workdir: Path) -> CommandResult:
    """Run ``git status --porcelain`` inside ``workdir`` (read-only)."""
    return _telemetry(
        "git_status",
        lambda: _run_subprocess(["git", "status", "--porcelain"], workdir=workdir),
    )


def git_diff(*, workdir: Path, rev: str = "HEAD") -> CommandResult:
    """Run ``git diff <rev>`` inside ``workdir`` (read-only)."""
    args = GitDiffArgs(rev=rev)
    _validate_revision(args.rev)
    return _telemetry(
        "git_diff",
        lambda: _run_subprocess(["git", "diff", args.rev], workdir=workdir),
    )


# ---------------------------------------------------------------------------
# gh_issue_create / gh_label_ensure
# ---------------------------------------------------------------------------


class GhIssueCreateArgs(_StrictModel):
    """Arguments for :func:`gh_issue_create`."""

    title: str = Field(min_length=1, max_length=256)
    body: str = ""
    labels: tuple[str, ...] = ()


class GhLabelEnsureArgs(_StrictModel):
    """Arguments for :func:`gh_label_ensure`."""

    name: str = Field(min_length=1, max_length=64)
    color: str = Field(default="ededed", pattern=r"^[0-9a-fA-F]{6}$")
    description: str = ""


def gh_issue_create(
    *,
    workdir: Path,
    title: str,
    body: str = "",
    labels: tuple[str, ...] | list[str] = (),
) -> CommandResult:
    """Wrap ``gh issue create`` (idempotency is the caller's responsibility)."""
    args = GhIssueCreateArgs(title=title, body=body, labels=tuple(labels))
    argv: list[str] = ["gh", "issue", "create", "--title", args.title]
    if args.body:
        argv += ["--body", args.body]
    for label in args.labels:
        argv += ["--label", label]
    return _telemetry(
        "gh_issue_create",
        lambda: _run_subprocess(argv, workdir=workdir),
    )


def gh_label_ensure(
    *,
    workdir: Path,
    name: str,
    color: str = "ededed",
    description: str = "",
) -> CommandResult:
    """Wrap ``gh label create`` (``gh`` is idempotent for existing labels)."""
    args = GhLabelEnsureArgs(name=name, color=color, description=description)
    argv: list[str] = [
        "gh",
        "label",
        "create",
        args.name,
        "--color",
        args.color,
        "--force",
    ]
    if args.description:
        argv += ["--description", args.description]
    return _telemetry(
        "gh_label_ensure",
        lambda: _run_subprocess(argv, workdir=workdir),
    )


# ---------------------------------------------------------------------------
# web_search
# ---------------------------------------------------------------------------


class WebSearchArgs(_StrictModel):
    """Arguments for :func:`web_search`."""

    query: str = Field(min_length=1, max_length=512)
    max_results: int = Field(default=5, ge=1, le=25)


class WebSearchResultItem(_StrictModel):
    """A single web-search hit."""

    title: str
    url: str
    snippet: str = ""


class WebSearchResult(_StrictModel):
    """Result envelope from :func:`web_search`."""

    query: str
    results: list[WebSearchResultItem]


_WEB_ENV_FLAG = "FLOWFORGE_ALLOW_WEB"


def web_search(*, query: str, max_results: int = 5) -> WebSearchResult:
    """Optional web search, gated behind the ``FLOWFORGE_ALLOW_WEB`` env var.

    Without a configured backend, returns an empty result list. Networked
    backends register themselves by replacing this function in T4+.
    """
    args = WebSearchArgs(query=query, max_results=max_results)
    if os.environ.get(_WEB_ENV_FLAG) != "1":
        raise ToolNotAllowedError(tool_id="web_search")

    def _invoke() -> WebSearchResult:
        return WebSearchResult(query=args.query, results=[])

    return _telemetry("web_search", _invoke)


# ---------------------------------------------------------------------------
# mcp_invoke
# ---------------------------------------------------------------------------


McpTransport = Callable[[str, dict[str, object]], dict[str, object]]


class McpInvokeArgs(_StrictModel):
    """Arguments for :func:`mcp_invoke`."""

    tool: str = Field(min_length=1, max_length=128)
    arguments: dict[str, object] = Field(default_factory=dict)


class McpInvokeResult(_StrictModel):
    """Generic MCP tool response."""

    tool: str
    payload: dict[str, object]


_mcp_transport: McpTransport | None = None


def set_mcp_transport(transport: McpTransport | None) -> None:
    """Register (or clear) the MCP passthrough transport for tests / runtime."""
    global _mcp_transport  # noqa: PLW0603 - module-level registry by design
    _mcp_transport = transport


def get_mcp_transport() -> McpTransport | None:
    """Return the currently registered MCP transport (or ``None``)."""
    return _mcp_transport


def mcp_invoke(*, tool: str, arguments: dict[str, object]) -> McpInvokeResult:
    """Invoke an MCP tool through the registered transport.

    Raises:
        ToolNotAllowedError: If no transport has been registered.
    """
    args = McpInvokeArgs(tool=tool, arguments=arguments)
    transport = _mcp_transport
    if transport is None:
        raise ToolNotAllowedError(tool_id="mcp_invoke")

    def _invoke() -> McpInvokeResult:
        payload = transport(args.tool, dict(args.arguments))
        return McpInvokeResult(tool=args.tool, payload=payload)

    return _telemetry("mcp_invoke", _invoke)
