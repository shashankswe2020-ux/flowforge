"""Unit tests for code_review_node."""

from __future__ import annotations

import json

from src.nodes.code_review import code_review_node
from src.state.models import (
    CapabilityType,
    Finding,
    GraphState,
    IssueSeverity,
    RunStatus,
    Task,
    TaskArtifact,
    TaskDefinition,
    TaskStatus,
)
from tests.mocks import MockLLM


def _state_with_artifacts() -> GraphState:
    """State with completed tasks and artifacts for review."""
    task_def = TaskDefinition(
        task_id="t1",
        title="Implement auth",
        description="Add auth module",
        acceptance_checks=["login works"],
        estimated_complexity="m",
        capability_type=CapabilityType.AGENT_WITH_TOOLS,
        verification_step="pytest",
    )
    task = Task(
        task_id="t1",
        definition=task_def,
        status=TaskStatus.SUCCEEDED,
        artifacts=[
            TaskArtifact(
                artifact_id="a1",
                artifact_type="code",
                path="src/auth.py",
                fingerprint="sha256:abc",
            ),
        ],
        verification_evidence=["all tests pass"],
    )
    return GraphState(
        request="Build API",
        run_status=RunStatus.RUNNING,
        tasks=[task],
    )


def _review_response(findings: list[dict[str, object]] | None = None) -> str:
    default_findings = [
        {
            "finding_id": "cr-001",
            "severity": "medium",
            "confidence": 0.85,
            "title": "Missing error handling",
            "description": "Function lacks try/except for network calls",
            "file_path": "src/auth.py",
            "line_range": [42, 45],
            "suggestion": "Add try/except with specific exception types",
        },
    ]
    return json.dumps({"findings": findings or default_findings})


class TestCodeReviewFindings:
    """code_review_node produces structured findings."""

    def test_produces_findings(self) -> None:
        """Returns list of Finding objects."""
        llm = MockLLM(responses=[_review_response()])
        state = _state_with_artifacts()
        result = code_review_node(state, llm=llm)
        assert len(result["review_findings"]) == 1

    def test_finding_has_correct_structure(self) -> None:
        """Findings conform to Finding schema."""
        llm = MockLLM(responses=[_review_response()])
        state = _state_with_artifacts()
        result = code_review_node(state, llm=llm)
        finding = result["review_findings"][0]
        assert isinstance(finding, Finding)
        assert finding.source_node == "code_review_node"
        assert finding.severity == IssueSeverity.MEDIUM

    def test_finding_includes_evidence(self) -> None:
        """Findings include file path and line range."""
        llm = MockLLM(responses=[_review_response()])
        state = _state_with_artifacts()
        result = code_review_node(state, llm=llm)
        finding = result["review_findings"][0]
        assert finding.file_path == "src/auth.py"
        assert finding.line_range == (42, 45)

    def test_multiple_findings(self) -> None:
        """Multiple findings returned from single review."""
        findings = [
            {
                "finding_id": "cr-001",
                "severity": "high",
                "confidence": 0.9,
                "title": "SQL injection",
                "description": "Unsanitized input",
            },
            {
                "finding_id": "cr-002",
                "severity": "low",
                "confidence": 0.7,
                "title": "Style issue",
                "description": "Inconsistent naming",
            },
        ]
        llm = MockLLM(responses=[_review_response(findings)])
        state = _state_with_artifacts()
        result = code_review_node(state, llm=llm)
        assert len(result["review_findings"]) == 2

    def test_llm_receives_artifact_context(self) -> None:
        """LLM prompt includes artifact details."""
        llm = MockLLM(responses=[_review_response()])
        state = _state_with_artifacts()
        code_review_node(state, llm=llm)
        assert "src/auth.py" in llm.call_history[0]
