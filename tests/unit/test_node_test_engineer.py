"""Unit tests for test_engineer_node."""

from __future__ import annotations

import json

from src.nodes.test_engineer import test_engineer_node
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


def _test_engineer_response(
    findings: list[dict[str, object]] | None = None,
    proposed_tasks: list[dict[str, object]] | None = None,
) -> str:
    default_findings = [
        {
            "finding_id": "te-001",
            "severity": "medium",
            "confidence": 0.8,
            "title": "Missing edge case tests",
            "description": "No tests for invalid credentials",
            "file_path": "tests/test_auth.py",
            "suggestion": "Add tests for expired tokens and invalid passwords",
        },
    ]
    return json.dumps(
        {
            "findings": findings or default_findings,
            "proposed_tasks": proposed_tasks or [],
        },
    )


class TestTestEngineerFindings:
    """test_engineer_node produces structured test findings."""

    def test_produces_findings(self) -> None:
        llm = MockLLM(responses=[_test_engineer_response()])
        state = _state_with_artifacts()
        result = test_engineer_node(state, llm=llm)
        assert len(result["test_findings"]) == 1

    def test_finding_has_correct_source(self) -> None:
        llm = MockLLM(responses=[_test_engineer_response()])
        state = _state_with_artifacts()
        result = test_engineer_node(state, llm=llm)
        finding = result["test_findings"][0]
        assert isinstance(finding, Finding)
        assert finding.source_node == "test_engineer_node"

    def test_finding_severity(self) -> None:
        llm = MockLLM(responses=[_test_engineer_response()])
        state = _state_with_artifacts()
        result = test_engineer_node(state, llm=llm)
        assert result["test_findings"][0].severity == IssueSeverity.MEDIUM

    def test_llm_receives_context(self) -> None:
        llm = MockLLM(responses=[_test_engineer_response()])
        state = _state_with_artifacts()
        test_engineer_node(state, llm=llm)
        assert "src/auth.py" in llm.call_history[0]


class TestProposedTasks:
    """test_engineer_node can propose additional tasks."""

    def test_no_proposed_tasks_by_default(self) -> None:
        llm = MockLLM(responses=[_test_engineer_response(proposed_tasks=[])])
        state = _state_with_artifacts()
        result = test_engineer_node(state, llm=llm)
        assert result["proposed_tasks"] == []

    def test_proposed_tasks_returned(self) -> None:
        proposed = [
            {
                "task_id": "t-new-001",
                "title": "Add auth edge case tests",
                "description": "Test expired tokens and invalid passwords",
                "acceptance_checks": ["tests cover edge cases"],
                "estimated_complexity": "s",
                "capability_type": "agent_with_tools",
                "verification_step": "pytest tests/test_auth_edge.py",
            },
        ]
        llm = MockLLM(responses=[_test_engineer_response(proposed_tasks=proposed)])
        state = _state_with_artifacts()
        result = test_engineer_node(state, llm=llm)
        assert len(result["proposed_tasks"]) == 1
        assert result["proposed_tasks"][0].task_id == "t-new-001"

    def test_proposed_tasks_are_task_definitions(self) -> None:
        proposed = [
            {
                "task_id": "t-new-001",
                "title": "Add tests",
                "description": "More tests",
                "acceptance_checks": ["passes"],
                "estimated_complexity": "s",
                "capability_type": "direct_tool",
                "verification_step": "pytest",
            },
        ]
        llm = MockLLM(responses=[_test_engineer_response(proposed_tasks=proposed)])
        state = _state_with_artifacts()
        result = test_engineer_node(state, llm=llm)
        task_def = result["proposed_tasks"][0]
        assert isinstance(task_def, TaskDefinition)
