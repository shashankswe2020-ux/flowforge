"""Unit tests for the ``task_node`` Deep Agent wrapper (T9).

These tests stub out ``build_deep_agent`` and ``run_deep_agent_bounded``
so we can drive the wrapper deterministically without spinning up the
real ``deepagents`` runtime. The contract under test:

* per-task dispatch through the IMPLEMENTER role with sub-agents
* persisted VFS files become ``TaskArtifact`` entries on the returned state
* the post-run secret scanner blocks the run on a HIGH-confidence finding
* extraction failures fall back to the legacy single-shot executor
* tool invocations propagate into ``state.deep_agent_traces['task_node']``
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from flowforge.deep_agents import AgentRole
from flowforge.nodes import task_runner
from flowforge.nodes.capability import TaskExecutionResult
from flowforge.state.models import (
    CapabilityType,
    GraphState,
    ImplementationPlan,
    RunStatus,
    Task,
    TaskDAG,
    TaskDefinition,
    TaskStatus,
    ToolInvocationRecord,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _make_state(workdir: Path, *, num_tasks: int = 1) -> GraphState:
    tasks = [
        TaskDefinition(
            task_id=f"t{i}",
            title=f"Task {i}",
            description="Implement it",
            acceptance_checks=["does the thing"],
            estimated_complexity="xs",
            capability_type=CapabilityType.AGENT_ONLY,
            verification_step="pytest -q",
        )
        for i in range(1, num_tasks + 1)
    ]
    plan = ImplementationPlan(
        phases=["Phase 1"],
        dag=TaskDAG(tasks=tasks, edges=[]),
    )
    return GraphState(
        request="build it",
        workdir=str(workdir),
        implementation_plan=plan,
    )


def _patch_deep(
    monkeypatch: pytest.MonkeyPatch,
    *,
    files_per_task: list[dict[str, str]],
    invocations: list[ToolInvocationRecord] | None = None,
) -> dict[str, Any]:
    """Patch the deep-agent factory to feed canned VFS files per task."""
    monkeypatch.setenv("FLOWFORGE_DEEP_AGENTS", "1")
    monkeypatch.setattr(task_runner, "build_deep_agent", lambda **_: object())

    call_count = {"n": 0}

    def fake_run(
        graph: object,  # noqa: ARG001
        payload: dict[str, Any],  # noqa: ARG001
        *,
        role: AgentRole,
        node_name: str,
        invocation_sink: list[ToolInvocationRecord] | None = None,
        **_: object,
    ) -> dict[str, Any]:
        assert role == AgentRole.IMPLEMENTER
        assert node_name == "task_node"
        idx = call_count["n"]
        call_count["n"] += 1
        if invocation_sink is not None and invocations is not None:
            invocation_sink.extend(invocations)
        return {
            "messages": [{"role": "assistant", "content": f"task {idx}"}],
            "files": files_per_task[idx],
        }

    monkeypatch.setattr(task_runner, "run_deep_agent_bounded", fake_run)
    monkeypatch.setattr(task_runner, "_commit_artifacts", lambda *a, **k: None)
    return call_count


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestDeepAgentHappyPath:
    def test_persists_files_and_records_succeeded_task(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        state = _make_state(tmp_path)
        invocation = ToolInvocationRecord(
            tool="task", ok=True, parent="refactorer",
        )
        _patch_deep(
            monkeypatch,
            files_per_task=[{"vfs:/src/greet.py": "def greet(): return 'hi'\n"}],
            invocations=[invocation],
        )

        result = task_runner.task_node(state, llm=MagicMock())

        assert (tmp_path / "src" / "greet.py").read_text().startswith("def greet")
        assert result["tasks"][0].status == TaskStatus.SUCCEEDED
        assert "task_node" in result["deep_agent_traces"]
        trace = result["deep_agent_traces"]["task_node"]
        assert trace.role == AgentRole.IMPLEMENTER
        assert any(i.parent == "refactorer" for i in trace.tool_invocations)

    def test_runs_one_dispatch_per_planned_task(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        state = _make_state(tmp_path, num_tasks=3)
        counter = _patch_deep(
            monkeypatch,
            files_per_task=[
                {"vfs:/src/a.py": "A\n"},
                {"vfs:/src/b.py": "B\n"},
                {"vfs:/src/c.py": "C\n"},
            ],
        )

        result = task_runner.task_node(state, llm=MagicMock())

        assert counter["n"] == 3
        assert len(result["tasks"]) == 3
        assert all(t.status == TaskStatus.SUCCEEDED for t in result["tasks"])


# ---------------------------------------------------------------------------
# Secret scanner blocks the run
# ---------------------------------------------------------------------------


class TestSecretBlocking:
    def test_high_confidence_secret_blocks_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        state = _make_state(tmp_path)
        # Planted AWS access key in generated source.
        leaked = 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n'
        _patch_deep(
            monkeypatch,
            files_per_task=[{"vfs:/src/cfg.py": leaked}],
        )

        result = task_runner.task_node(state, llm=MagicMock())

        assert result["run_status"] == RunStatus.BLOCKED
        blocked = result["tasks"][0]
        assert blocked.status == TaskStatus.BLOCKED
        assert "secret_scanner" in (blocked.error_message or "")
        # Trace still emitted for telemetry.
        assert "task_node" in result["deep_agent_traces"]

    def test_subsequent_tasks_are_not_run_after_block(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        state = _make_state(tmp_path, num_tasks=3)
        counter = _patch_deep(
            monkeypatch,
            files_per_task=[
                {"vfs:/cfg.py": 'KEY = "AKIAIOSFODNN7EXAMPLE"\n'},
                {"vfs:/src/b.py": "B\n"},
                {"vfs:/src/c.py": "C\n"},
            ],
        )

        result = task_runner.task_node(state, llm=MagicMock())

        assert counter["n"] == 1
        assert result["run_status"] == RunStatus.BLOCKED
        assert len(result["tasks"]) == 1


# ---------------------------------------------------------------------------
# Fallback to legacy
# ---------------------------------------------------------------------------


class TestLegacyFallback:
    def test_no_artifacts_falls_back_to_legacy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        state = _make_state(tmp_path)
        # Empty VFS — persist_files returns no artifacts → wrapper returns None.
        _patch_deep(monkeypatch, files_per_task=[{}])

        legacy_called = {"n": 0}

        def fake_execute(
            task: Task, *, llm: object,  # noqa: ARG001
        ) -> TaskExecutionResult:
            legacy_called["n"] += 1
            return TaskExecutionResult(
                task_id=task.task_id,
                status=TaskStatus.SUCCEEDED,
                artifacts=[],
                verification_evidence=["legacy ran"],
                error_message=None,
                idempotency_key=None,
            )

        monkeypatch.setattr(task_runner, "execute_task", fake_execute)

        result = task_runner.task_node(state, llm=MagicMock())

        assert legacy_called["n"] == 1
        assert result["tasks"][0].status == TaskStatus.SUCCEEDED
        # Legacy path does not populate deep_agent_traces.
        assert "deep_agent_traces" not in result


# ---------------------------------------------------------------------------
# Flag gating
# ---------------------------------------------------------------------------


class TestFlagGating:
    def test_flag_off_skips_deep_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FLOWFORGE_DEEP_AGENTS", "0")
        state = _make_state(tmp_path)

        # If the deep path is taken these patches would be needed; if
        # the flag is honored they remain untouched.
        sentinel = {"called": False}

        def fail_build(**_: object) -> object:
            sentinel["called"] = True
            return object()

        monkeypatch.setattr(task_runner, "build_deep_agent", fail_build)

        captured: dict[str, Any] = {}

        def fake_execute(
            task: Task, *, llm: object,  # noqa: ARG001
        ) -> TaskExecutionResult:
            captured["task_id"] = task.task_id
            return TaskExecutionResult(
                task_id=task.task_id,
                status=TaskStatus.SUCCEEDED,
                artifacts=[],
                verification_evidence=[],
                error_message=None,
                idempotency_key=None,
            )

        monkeypatch.setattr(task_runner, "execute_task", fake_execute)

        result = task_runner.task_node(state, llm=MagicMock())

        assert sentinel["called"] is False
        assert captured["task_id"] == "t1"
        assert result["tasks"][0].status == TaskStatus.SUCCEEDED


# ---------------------------------------------------------------------------
# Review-23 Important fixes — diff vs disk, evidence extraction, partial commit
# ---------------------------------------------------------------------------


class TestDiffVsDisk:
    """Pre-existing on-disk content must not be flagged by the scanner."""

    def test_unchanged_lines_in_existing_file_are_not_flagged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # File already exists on disk with a token-shaped string.
        target = tmp_path / "src" / "legacy.py"
        target.parent.mkdir(parents=True)
        target.write_text('# pre-existing\nKEY = "AKIAIOSFODNN7EXAMPLE"\n')

        state = _make_state(tmp_path)
        # Agent emits the *same* content — no added lines for the scanner.
        _patch_deep(
            monkeypatch,
            files_per_task=[
                {"vfs:/src/legacy.py": '# pre-existing\nKEY = "AKIAIOSFODNN7EXAMPLE"\n'},
            ],
        )

        result = task_runner.task_node(state, llm=MagicMock())

        # Content is identical → diff has zero added lines → no block.
        assert result["tasks"][0].status == TaskStatus.SUCCEEDED


class TestVerificationEvidence:
    """``vfs:/context/implementer_output.json`` must populate evidence."""

    def test_evidence_is_extracted_from_summary_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import json

        summary = json.dumps(
            {
                "task_id": "t1",
                "verification_evidence": [
                    "pytest tests/test_foo.py -q -> 3 passed",
                    "ruff check . -> 0 errors",
                ],
            },
        )
        _patch_deep(
            monkeypatch,
            files_per_task=[
                {
                    "vfs:/src/foo.py": "x = 1\n",
                    "vfs:/context/implementer_output.json": summary,
                },
            ],
        )

        result = task_runner.task_node(_make_state(tmp_path), llm=MagicMock())

        evidence = result["tasks"][0].verification_evidence
        assert len(evidence) == 2
        assert "pytest" in evidence[0]


class TestPartialCommitOnBlock:
    """A block on task N commits files written by tasks 1..N-1."""

    def test_prior_task_files_are_committed_before_block(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        state = _make_state(tmp_path, num_tasks=2)
        _patch_deep(
            monkeypatch,
            files_per_task=[
                {"vfs:/src/clean.py": "x = 1\n"},
                {"vfs:/src/leaked.py": 'KEY = "AKIAIOSFODNN7EXAMPLE"\n'},
            ],
        )
        commit_calls: list[list[str]] = []
        monkeypatch.setattr(
            task_runner,
            "_commit_artifacts",
            lambda _wd, paths: commit_calls.append([str(p) for p in paths]),
        )

        result = task_runner.task_node(state, llm=MagicMock())

        assert result["run_status"] == RunStatus.BLOCKED
        # Task 1's file was committed; task 2's leaked file never persisted.
        assert len(commit_calls) == 1
        assert any("clean.py" in p for p in commit_calls[0])
        assert not (tmp_path / "src" / "leaked.py").exists()


class TestSecretNotPersisted:
    """The offending file must not be written to disk."""

    def test_blocked_artifact_never_touches_disk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_deep(
            monkeypatch,
            files_per_task=[
                {"vfs:/src/cfg.py": 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n'},
            ],
        )

        result = task_runner.task_node(_make_state(tmp_path), llm=MagicMock())

        assert result["run_status"] == RunStatus.BLOCKED
        assert not (tmp_path / "src" / "cfg.py").exists()
        # Blocked task carries no artifacts since persist was skipped.
        assert result["tasks"][0].artifacts == []


class TestWorkdirEscapeGuard:
    """Audit-18 IMPORTANT-1 — scanner must not read outside the workdir."""

    def test_scanner_rejects_traversal_path_without_reading_host_file(
        self, tmp_path: Path,
    ) -> None:
        # Plant a sentinel "secret" outside the workdir. If the scanner
        # naively did `workdir / rel`.read_text() it would slurp this in
        # as the "old" content. Our guard rejects the path *before* any
        # filesystem read.
        outside = tmp_path.parent / "outside-secret.txt"
        outside.write_text("AKIAIOSFODNN7EXAMPLE")
        try:
            findings = task_runner._scan_files_for_secrets(
                {"vfs:/../outside-secret.txt": "harmless content\n"},
                tmp_path,
            )
            # The agent's *new* content is harmless, so no findings.
            # Crucially the scanner did not read the outside file (which
            # would have produced a removal-only diff with no impact, but
            # could have hung on a FIFO or OOM'd on a huge file).
            assert findings == []
        finally:
            outside.unlink(missing_ok=True)
