"""Tests for Deep Agent adapter helpers (T5)."""

from __future__ import annotations

import json
from pathlib import Path

from flowforge.deep_agents.adapters import (
    apply_agent_result,
    extract_findings,
    materialize_files,
    persist_files,
    state_to_input,
)
from flowforge.state.models import (
    CapabilityType,
    Finding,
    GraphState,
    ImplementationPlan,
    IssueSeverity,
    SpecOutput,
    Task,
    TaskArtifact,
    TaskDAG,
    TaskDefinition,
    TaskStatus,
)


def _state_with_artifacts(workdir: Path) -> GraphState:
    task_definition = TaskDefinition(
        task_id="task-001",
        title="Add greeting",
        description="Implement greeting feature",
        acceptance_checks=["tests pass"],
        estimated_complexity="s",
        capability_type=CapabilityType.AGENT_ONLY,
        verification_step="pytest -q",
    )
    task = Task(
        task_id="task-001",
        definition=task_definition,
        status=TaskStatus.SUCCEEDED,
        artifacts=[
            TaskArtifact(
                artifact_id="artifact-001",
                artifact_type="source",
                path="src/greet.py",
                fingerprint="fp-source",
                content="def greet() -> str:\n    return 'hi'\n",
            ),
        ],
    )
    return GraphState(
        request="Build greeting",
        workdir=str(workdir),
        spec=SpecOutput(
            artifact_path="docs/specs/greeting.md",
            summary="Greeting spec",
            objective="Ship a greeting function",
            acceptance_criteria=["returns hi"],
        ),
        implementation_plan=ImplementationPlan(
            phases=["phase-1"],
            dag=TaskDAG(tasks=[task_definition], edges=[]),
        ),
        tasks=[task],
        review_findings=[
            Finding(
                finding_id="review-1",
                source_node="code_review_node",
                severity=IssueSeverity.MEDIUM,
                confidence=0.8,
                title="Need edge case coverage",
                description="Missing empty-input test",
                file_path="tests/test_greet.py",
            ),
        ],
        security_findings=[
            Finding(
                finding_id="security-1",
                source_node="security_audit_node",
                severity=IssueSeverity.LOW,
                confidence=0.7,
                title="No issue",
                description="Looks fine",
                file_path="src/greet.py",
            ),
        ],
        test_findings=[],
    )


def test_materialize_files_includes_artifacts_and_context(tmp_path: Path) -> None:
    state = _state_with_artifacts(tmp_path)

    files = materialize_files(state)

    assert files["vfs:/src/greet.py"].startswith("def greet")
    assert json.loads(files["vfs:/context/spec.json"])["summary"] == "Greeting spec"
    assert json.loads(files["vfs:/context/implementation-plan.json"])["phases"] == ["phase-1"]
    review_findings = json.loads(files["vfs:/context/review-findings.json"])
    assert review_findings[0]["finding_id"] == "review-1"


def test_persist_files_writes_only_changed_paths(tmp_path: Path) -> None:
    result = {
        "files": {
            "vfs:/src/greet.py": "print('hi')\n",
            "vfs:/findings/review.json": json.dumps(
                [
                    {
                        "finding_id": "review-1",
                        "source_node": "code_review_node",
                        "severity": "medium",
                        "confidence": 0.9,
                        "title": "Test title",
                        "description": "Test description",
                    },
                ],
            ),
        },
    }

    first_write = persist_files(result, tmp_path)
    second_write = persist_files(result, tmp_path)

    assert sorted(first_write) == ["findings/review.json", "src/greet.py"]
    assert second_write == []
    assert (tmp_path / "src" / "greet.py").read_text(encoding="utf-8") == "print('hi')\n"


def test_extract_findings_reads_canonical_vfs_json() -> None:
    result = {
        "files": {
            "vfs:/findings/review.json": json.dumps(
                {
                    "findings": [
                        {
                            "finding_id": "review-1",
                            "source_node": "code_review_node",
                            "severity": "high",
                            "confidence": 0.95,
                            "title": "Broken behavior",
                            "description": "Behavior is broken",
                            "file_path": "src/greet.py",
                            "line_range": [1, 2],
                            "suggestion": "Fix it",
                        },
                    ],
                },
            ),
        },
    }

    findings = extract_findings(result)

    assert len(findings) == 1
    assert findings[0].finding_id == "review-1"
    assert findings[0].severity == IssueSeverity.HIGH
    assert findings[0].line_range == (1, 2)


def test_materialize_then_noop_persist_is_idempotent(tmp_path: Path) -> None:
    state = _state_with_artifacts(tmp_path)
    files = materialize_files(state)

    first_write = persist_files({"files": files}, tmp_path)
    second_write = persist_files({"files": files}, tmp_path)

    assert "src/greet.py" in first_write
    assert second_write == []


def test_state_to_input_wraps_seed_prompt_and_files(tmp_path: Path) -> None:
    state = _state_with_artifacts(tmp_path)

    payload = state_to_input(state, seed_prompt="Review this change")

    assert payload["messages"] == [{"role": "user", "content": "Review this change"}]
    assert "vfs:/src/greet.py" in payload["files"]


def test_apply_agent_result_persists_files_and_returns_summary(tmp_path: Path) -> None:
    state = _state_with_artifacts(tmp_path)
    result = {
        "files": {
            "vfs:/src/greet.py": "print('updated')\n",
            "vfs:/findings/test.json": json.dumps(
                [
                    {
                        "finding_id": "test-1",
                        "source_node": "test_engineer_node",
                        "severity": "low",
                        "confidence": 0.5,
                        "title": "Add more assertions",
                        "description": "A little more coverage would help",
                    },
                ],
            ),
        },
    }

    delta = apply_agent_result(state, result, node_name="test_engineer_node")

    assert delta["deep_agent_node_name"] == "test_engineer_node"
    assert delta["deep_agent_changed_paths"] == ["findings/test.json", "src/greet.py"]
    assert len(delta["deep_agent_findings"]) == 1
