"""Unit tests for security_audit_node."""

from __future__ import annotations

import json

from src.nodes.security_audit import security_audit_node
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
    task_def = TaskDefinition(
        task_id="t1",
        title="Implement auth",
        description="Add auth",
        acceptance_checks=["ok"],
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
    )
    return GraphState(request="Build API", run_status=RunStatus.RUNNING, tasks=[task])


def _security_response(findings: list[dict[str, object]] | None = None) -> str:
    default_findings = [
        {
            "finding_id": "sec-001",
            "severity": "critical",
            "confidence": 0.95,
            "title": "Hardcoded credentials",
            "description": "API key exposed in source",
            "file_path": "src/auth.py",
            "line_range": [10, 10],
            "suggestion": "Use environment variables",
        },
    ]
    return json.dumps({"findings": findings or default_findings})


class TestSecurityAuditFindings:
    """security_audit_node produces structured security findings."""

    def test_produces_findings(self) -> None:
        llm = MockLLM(responses=[_security_response()])
        state = _state_with_artifacts()
        result = security_audit_node(state, llm=llm)
        assert len(result["security_findings"]) == 1

    def test_finding_has_correct_source(self) -> None:
        llm = MockLLM(responses=[_security_response()])
        state = _state_with_artifacts()
        result = security_audit_node(state, llm=llm)
        finding = result["security_findings"][0]
        assert isinstance(finding, Finding)
        assert finding.source_node == "security_audit_node"

    def test_critical_severity_parsed(self) -> None:
        llm = MockLLM(responses=[_security_response()])
        state = _state_with_artifacts()
        result = security_audit_node(state, llm=llm)
        assert result["security_findings"][0].severity == IssueSeverity.CRITICAL

    def test_llm_receives_context(self) -> None:
        llm = MockLLM(responses=[_security_response()])
        state = _state_with_artifacts()
        security_audit_node(state, llm=llm)
        assert "src/auth.py" in llm.call_history[0]
