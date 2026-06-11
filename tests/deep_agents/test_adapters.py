"""Tests for ``flowforge.deep_agents.adapters`` (T5).

Covers the spec §5.4 / §8.1 adapter contract:

* ``materialize_files`` produces VFS entries for prior context (spec,
  plan, findings) and every task artifact;
* ``persist_files`` mirrors VFS contents to the workdir with
  diff-aware writes and rejects path traversal;
* ``extract_findings`` parses canonical ``vfs:/findings/*.json`` JSON
  arrays into :class:`flowforge.state.models.Finding` instances;
* ``materialize → no-op agent → persist`` is idempotent on disk.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from flowforge.deep_agents.adapters import (
    PathTraversalError,
    extract_findings,
    materialize_files,
    persist_files,
)
from flowforge.state.models import (
    CapabilityType,
    Finding,
    IssueSeverity,
    Task,
    TaskArtifact,
    TaskDefinition,
    TaskStatus,
)
from tests.factories import make_state

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task_with_artifacts(*artifacts: TaskArtifact) -> Task:
    definition = TaskDefinition(
        task_id="task-001",
        title="seed task",
        description="seed",
        acceptance_checks=["pass"],
        estimated_complexity="s",
        capability_type=CapabilityType.AGENT_WITH_TOOLS,
        verification_step="pytest",
    )
    return Task(
        task_id="task-001",
        definition=definition,
        status=TaskStatus.SUCCEEDED,
        artifacts=list(artifacts),
    )


# ---------------------------------------------------------------------------
# materialize_files
# ---------------------------------------------------------------------------


class TestMaterializeFiles:
    def test_returns_dict_str_to_str(self) -> None:
        state = make_state("start")
        files = materialize_files(state)
        assert isinstance(files, dict)
        for path, content in files.items():
            assert isinstance(path, str)
            assert isinstance(content, str)

    def test_includes_artifacts_under_vfs_prefix(self) -> None:
        artifact = TaskArtifact(
            artifact_id="a1",
            artifact_type="code",
            path="src/foo.py",
            fingerprint="abc",
            content="print('hi')\n",
        )
        state = make_state("task_execution")
        state.tasks = [_task_with_artifacts(artifact)]

        files = materialize_files(state)
        assert "vfs:/src/foo.py" in files
        assert files["vfs:/src/foo.py"] == "print('hi')\n"

    def test_includes_spec_when_set(self) -> None:
        state = make_state("spec")
        files = materialize_files(state)
        assert "vfs:/context/spec.json" in files
        payload = json.loads(files["vfs:/context/spec.json"])
        assert payload["summary"] == "Test feature specification"

    def test_includes_plan_when_set(self) -> None:
        state = make_state("plan")
        files = materialize_files(state)
        assert "vfs:/context/plan.json" in files
        payload = json.loads(files["vfs:/context/plan.json"])
        assert "dag" in payload

    def test_includes_findings_when_set(self) -> None:
        state = make_state("plan")
        state.review_findings = [
            Finding(
                finding_id="f1",
                source_node="code_review_node",
                severity=IssueSeverity.LOW,
                confidence=0.9,
                title="t",
                description="d",
            ),
        ]
        files = materialize_files(state)
        assert "vfs:/context/findings/review.json" in files
        payload = json.loads(files["vfs:/context/findings/review.json"])
        assert isinstance(payload, list)
        assert payload[0]["finding_id"] == "f1"

    def test_omits_unset_optional_context(self) -> None:
        state = make_state("start")
        files = materialize_files(state)
        assert "vfs:/context/spec.json" not in files
        assert "vfs:/context/plan.json" not in files

    def test_artifact_path_traversal_rejected(self) -> None:
        artifact = TaskArtifact(
            artifact_id="a1",
            artifact_type="code",
            path="../escape.py",
            fingerprint="abc",
            content="x",
        )
        state = make_state("task_execution")
        state.tasks = [_task_with_artifacts(artifact)]
        with pytest.raises(PathTraversalError):
            materialize_files(state)


# ---------------------------------------------------------------------------
# persist_files
# ---------------------------------------------------------------------------


class TestPersistFiles:
    def test_writes_changed_files_to_workdir(self, tmp_path: Path) -> None:
        result = {"files": {"vfs:/src/foo.py": "print('hi')\n"}}
        artifacts = persist_files(result, workdir=tmp_path)
        target = tmp_path / "src" / "foo.py"
        assert target.read_text() == "print('hi')\n"
        assert len(artifacts) == 1
        assert artifacts[0].path == "src/foo.py"
        assert artifacts[0].content == "print('hi')\n"

    def test_skips_unchanged_files(self, tmp_path: Path) -> None:
        target = tmp_path / "src" / "foo.py"
        target.parent.mkdir(parents=True)
        target.write_text("same\n")
        result = {"files": {"vfs:/src/foo.py": "same\n"}}
        artifacts = persist_files(result, workdir=tmp_path)
        assert artifacts == []

    def test_rewrites_when_content_changes(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_text("old")
        result = {"files": {"vfs:/a.txt": "new"}}
        artifacts = persist_files(result, workdir=tmp_path)
        assert target.read_text() == "new"
        assert [a.path for a in artifacts] == ["a.txt"]

    def test_skips_findings_namespace(self, tmp_path: Path) -> None:
        result = {"files": {"vfs:/findings/review.json": "[]"}}
        artifacts = persist_files(result, workdir=tmp_path)
        assert artifacts == []
        assert not (tmp_path / "findings").exists()

    def test_skips_context_and_subagent_namespaces(self, tmp_path: Path) -> None:
        result = {
            "files": {
                "vfs:/context/spec.json": "{}",
                "vfs:/subagent/researcher/notes.md": "x",
            },
        }
        artifacts = persist_files(result, workdir=tmp_path)
        assert artifacts == []
        assert not (tmp_path / "context").exists()
        assert not (tmp_path / "subagent").exists()

    def test_rejects_path_traversal(self, tmp_path: Path) -> None:
        result = {"files": {"vfs:/../escape.py": "x"}}
        with pytest.raises(PathTraversalError):
            persist_files(result, workdir=tmp_path)

    def test_rejects_absolute_path(self, tmp_path: Path) -> None:
        result = {"files": {"vfs:/etc/passwd": "x"}}
        # `vfs:/etc/passwd` → relative `etc/passwd`, allowed under workdir.
        # Use a true absolute marker instead.
        result = {"files": {"/etc/passwd": "x"}}
        with pytest.raises(PathTraversalError):
            persist_files(result, workdir=tmp_path)

    def test_accepts_paths_without_vfs_prefix(self, tmp_path: Path) -> None:
        result = {"files": {"notes.md": "hello"}}
        artifacts = persist_files(result, workdir=tmp_path)
        assert (tmp_path / "notes.md").read_text() == "hello"
        assert [a.path for a in artifacts] == ["notes.md"]

    def test_accepts_result_without_files_key(self, tmp_path: Path) -> None:
        assert persist_files({}, workdir=tmp_path) == []


# ---------------------------------------------------------------------------
# extract_findings
# ---------------------------------------------------------------------------


class TestExtractFindings:
    def test_parses_findings_json_array(self) -> None:
        finding = {
            "finding_id": "f1",
            "source_node": "code_review_node",
            "severity": "medium",
            "confidence": 0.8,
            "title": "t",
            "description": "d",
        }
        result = {
            "files": {
                "vfs:/findings/review.json": json.dumps([finding]),
            },
        }
        findings = extract_findings(result)
        assert len(findings) == 1
        assert isinstance(findings[0], Finding)
        assert findings[0].finding_id == "f1"

    def test_merges_multiple_finding_files(self) -> None:
        a = {
            "finding_id": "f1",
            "source_node": "code_review_node",
            "severity": "low",
            "confidence": 0.5,
            "title": "t",
            "description": "d",
        }
        b = {
            "finding_id": "f2",
            "source_node": "security_audit_node",
            "severity": "high",
            "confidence": 0.9,
            "title": "t",
            "description": "d",
        }
        result = {
            "files": {
                "vfs:/findings/review.json": json.dumps([a]),
                "vfs:/findings/security.json": json.dumps([b]),
            },
        }
        ids = sorted(f.finding_id for f in extract_findings(result))
        assert ids == ["f1", "f2"]

    def test_ignores_non_findings_files(self) -> None:
        result = {
            "files": {
                "vfs:/src/foo.py": "print()",
                "vfs:/context/spec.json": "{}",
            },
        }
        assert extract_findings(result) == []

    def test_invalid_json_raises_value_error(self) -> None:
        result = {"files": {"vfs:/findings/review.json": "not json"}}
        with pytest.raises(ValueError, match="findings"):
            extract_findings(result)

    def test_empty_when_no_files_key(self) -> None:
        assert extract_findings({}) == []


# ---------------------------------------------------------------------------
# Round-trip property
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_materialize_then_persist_is_idempotent(self, tmp_path: Path) -> None:
        artifact = TaskArtifact(
            artifact_id="a1",
            artifact_type="code",
            path="src/foo.py",
            fingerprint="abc",
            content="print('hi')\n",
        )
        state = make_state("task_execution")
        state.tasks = [_task_with_artifacts(artifact)]

        files = materialize_files(state)
        first = persist_files({"files": files}, workdir=tmp_path)
        second = persist_files({"files": files}, workdir=tmp_path)
        assert {a.path for a in first} == {"src/foo.py"}
        assert second == []
