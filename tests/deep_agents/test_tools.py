"""Tests for ``flowforge.deep_agents.tools`` (T2).

Covers:

* happy-path execution for every tool (subprocess shell-outs are
  mocked via ``monkeypatch`` so no real shell command runs);
* ``_safe_path`` rejects ``..`` traversal, absolute paths, and
  symlink escapes;
* ``web_search`` is gated by ``FLOWFORGE_ALLOW_WEB``;
* every tool emits ``tool.invoked`` / ``tool.succeeded`` /
  ``tool.failed`` telemetry events through the package logger;
* policy errors (``ToolNotAllowedError`` / ``ToolSchemaViolationError``)
  are re-raised from the central policy module.
"""

from __future__ import annotations

import logging
import subprocess
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError

from flowforge.deep_agents import tools
from flowforge.deep_agents.tools import (
    PathTraversalError,
    _safe_path,
    git_diff,
    git_status,
    run_lint,
    run_tests,
    run_typecheck,
    web_search,
)
from flowforge.tools.policy import ToolNotAllowedError, ToolSchemaViolationError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeCompleted:
    """Minimal ``subprocess.CompletedProcess`` stand-in for tests."""

    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        args: Sequence[str] | None = None,
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.args = list(args or [])


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """Provide a clean per-test workdir."""
    (tmp_path / "src").mkdir()
    return tmp_path


@pytest.fixture
def patch_run(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Patch ``subprocess.run`` and capture invocations."""
    calls: list[dict[str, Any]] = []

    def _fake_run(
        argv: Sequence[str],
        **kwargs: Any,  # noqa: ANN401 - subprocess.run signature is dynamic
    ) -> _FakeCompleted:
        calls.append({"argv": list(argv), **kwargs})
        return _FakeCompleted(returncode=0, stdout="ok\n", stderr="", args=argv)

    monkeypatch.setattr(tools.subprocess, "run", _fake_run)
    return calls


# ---------------------------------------------------------------------------
# _safe_path
# ---------------------------------------------------------------------------


class TestSafePath:
    def test_resolves_relative_path_inside_workdir(self, workdir: Path) -> None:
        result = _safe_path(workdir, "src/foo.py")
        assert result == (workdir / "src" / "foo.py").resolve()

    def test_rejects_absolute_path(self, workdir: Path) -> None:
        with pytest.raises(PathTraversalError):
            _safe_path(workdir, "/etc/passwd")

    def test_rejects_dotdot_traversal(self, workdir: Path) -> None:
        with pytest.raises(PathTraversalError):
            _safe_path(workdir, "../escape.py")

    def test_rejects_symlink_escape(self, workdir: Path, tmp_path: Path) -> None:
        outside = tmp_path.parent / "outside-target"
        outside.mkdir(exist_ok=True)
        link = workdir / "evil"
        link.symlink_to(outside)

        with pytest.raises(PathTraversalError):
            _safe_path(workdir, "evil/file.txt")

    def test_rejects_when_workdir_missing(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope"
        with pytest.raises(PathTraversalError):
            _safe_path(missing, "x")


# ---------------------------------------------------------------------------
# Subprocess-backed tools (happy path + shell=False)
# ---------------------------------------------------------------------------


class TestSubprocessTools:
    def test_run_tests_invokes_pytest(
        self, workdir: Path, patch_run: list[dict[str, Any]],
    ) -> None:
        result = run_tests(workdir=workdir)
        assert result.returncode == 0
        call = patch_run[0]
        assert call["argv"][0:2] == ["pytest", "-q"]
        assert call["cwd"] == workdir
        assert call["shell"] is False

    def test_run_tests_with_path(
        self, workdir: Path, patch_run: list[dict[str, Any]],
    ) -> None:
        run_tests(workdir=workdir, path="src")
        assert patch_run[0]["argv"][-1] == "src"

    def test_run_tests_rejects_path_escape(self, workdir: Path) -> None:
        with pytest.raises(PathTraversalError):
            run_tests(workdir=workdir, path="../outside")

    def test_run_lint_invokes_ruff(
        self, workdir: Path, patch_run: list[dict[str, Any]],
    ) -> None:
        run_lint(workdir=workdir)
        assert patch_run[0]["argv"][:3] == ["ruff", "check", "."]
        assert patch_run[0]["shell"] is False

    def test_run_typecheck_invokes_mypy(
        self, workdir: Path, patch_run: list[dict[str, Any]],
    ) -> None:
        run_typecheck(workdir=workdir)
        assert patch_run[0]["argv"][0] == "mypy"
        assert patch_run[0]["shell"] is False

    def test_git_status_porcelain(
        self, workdir: Path, patch_run: list[dict[str, Any]],
    ) -> None:
        git_status(workdir=workdir)
        assert patch_run[0]["argv"] == ["git", "status", "--porcelain"]

    def test_git_diff_default_head(
        self, workdir: Path, patch_run: list[dict[str, Any]],
    ) -> None:
        git_diff(workdir=workdir)
        assert patch_run[0]["argv"] == ["git", "diff", "HEAD"]

    def test_git_diff_with_revision(
        self, workdir: Path, patch_run: list[dict[str, Any]],
    ) -> None:
        git_diff(workdir=workdir, rev="main")
        assert patch_run[0]["argv"] == ["git", "diff", "main"]

    def test_git_diff_rejects_dangerous_revision(self, workdir: Path) -> None:
        # leading dash could be interpreted as a flag — must be rejected.
        with pytest.raises(ToolSchemaViolationError):
            git_diff(workdir=workdir, rev="--exec=evil")

    def test_gh_issue_create_invokes_gh(
        self, workdir: Path, patch_run: list[dict[str, Any]],
    ) -> None:
        result = tools.gh_issue_create(
            workdir=workdir,
            title="Bug: thing broke",
            body="details",
            labels=["bug"],
        )
        assert result.returncode == 0
        argv = patch_run[0]["argv"]
        assert argv[0:3] == ["gh", "issue", "create"]
        assert "--title" in argv
        assert "Bug: thing broke" in argv
        assert "--body" in argv
        assert "details" in argv
        assert "--label" in argv
        assert "bug" in argv

    def test_gh_label_ensure_invokes_gh(
        self, workdir: Path, patch_run: list[dict[str, Any]],
    ) -> None:
        tools.gh_label_ensure(workdir=workdir, name="bug", color="ff0000")
        argv = patch_run[0]["argv"]
        assert argv[:3] == ["gh", "label", "create"]
        assert "bug" in argv


# ---------------------------------------------------------------------------
# web_search env-gating
# ---------------------------------------------------------------------------


class TestWebSearch:
    def test_blocked_without_env(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("FLOWFORGE_ALLOW_WEB", raising=False)
        with pytest.raises(ToolNotAllowedError):
            web_search(query="anything")

    def test_blocked_when_env_not_one(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FLOWFORGE_ALLOW_WEB", "0")
        with pytest.raises(ToolNotAllowedError):
            web_search(query="anything")

    def test_allowed_when_env_set(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FLOWFORGE_ALLOW_WEB", "1")
        result = web_search(query="anything")
        assert result.query == "anything"
        # Default web_search returns an empty result list when no
        # transport is configured — it must not crash.
        assert isinstance(result.results, list)

    def test_query_must_be_non_empty(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FLOWFORGE_ALLOW_WEB", "1")
        with pytest.raises(ValidationError):
            web_search(query="")


# ---------------------------------------------------------------------------
# mcp_invoke transport gating
# ---------------------------------------------------------------------------


class TestMcpInvoke:
    def test_raises_without_transport(self) -> None:
        # Reset transport to ensure deterministic state.
        tools.set_mcp_transport(None)
        with pytest.raises(ToolNotAllowedError):
            tools.mcp_invoke(tool="echo", arguments={"x": 1})

    def test_uses_registered_transport(self) -> None:
        recorded: list[tuple[str, dict[str, object]]] = []

        def transport(name: str, args: dict[str, object]) -> dict[str, object]:
            recorded.append((name, args))
            return {"ok": True}

        tools.set_mcp_transport(transport)
        try:
            result = tools.mcp_invoke(tool="echo", arguments={"x": 1})
        finally:
            tools.set_mcp_transport(None)
        assert result.payload == {"ok": True}
        assert recorded == [("echo", {"x": 1})]


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


class TestTelemetry:
    def test_emits_invoked_and_succeeded(
        self,
        workdir: Path,
        patch_run: list[dict[str, Any]],  # noqa: ARG002 - patches subprocess
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.INFO, logger="flowforge.deep_agents.tools"):
            run_tests(workdir=workdir)
        events = [r.message for r in caplog.records]
        assert "tool.invoked" in events
        assert "tool.succeeded" in events
        # Each record must carry the tool name as ``extra``.
        record = next(r for r in caplog.records if r.message == "tool.invoked")
        assert getattr(record, "tool", None) == "run_tests"

    def test_emits_failed_on_exception(
        self,
        workdir: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        def raising_run(
            argv: Sequence[str],
            **_: Any,  # noqa: ANN401
        ) -> _FakeCompleted:
            raise subprocess.SubprocessError("boom")

        monkeypatch.setattr(tools.subprocess, "run", raising_run)
        with (
            caplog.at_level(logging.INFO, logger="flowforge.deep_agents.tools"),
            pytest.raises(subprocess.SubprocessError),
        ):
            run_lint(workdir=workdir)
        events = [r.message for r in caplog.records]
        assert "tool.invoked" in events
        assert "tool.failed" in events
        assert "tool.succeeded" not in events
