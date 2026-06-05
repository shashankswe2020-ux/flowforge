"""State factories for producing valid GraphState at any pipeline stage."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.state.models import (
    AmbiguityStatus,
    CapabilityType,
    ClarificationTranscript,
    ClarifiedRequest,
    DefaultModelConfig,
    Finding,
    GraphState,
    ImplementationPlan,
    Issue,
    IssueDisposition,
    IssueSeverity,
    RunMetadata,
    RunStatus,
    ShippingReadiness,
    ShippingResult,
    SpecOutput,
    Task,
    TaskDAG,
    TaskDefinition,
    TaskStatus,
)


def _default_run_metadata() -> RunMetadata:
    return RunMetadata(
        correlation_id="test-run-001",
        actor_identity="test-agent",
        policy_version="v1.0-test",
        start_time=datetime(2026, 6, 5, tzinfo=UTC),
    )


def _default_model_config() -> DefaultModelConfig:
    return DefaultModelConfig(
        model_id="test-model",
        provider="test-provider",
    )


def _default_clarified_request() -> ClarifiedRequest:
    return ClarifiedRequest(
        solution_type="web_app",
        scope_size="prototype",
        target_users="internal engineers",
        must_have=["auth", "dashboard"],
        nice_to_have=["dark mode"],
        constraints=["must use Python"],
        success_criteria=["loads in <2s"],
        tech_preferences=["FastAPI"],
        summary="Internal dashboard prototype with auth",
    )


def _default_spec_output() -> SpecOutput:
    return SpecOutput(
        artifact_path="docs/specs/test-feature.md",
        summary="Test feature specification",
        acceptance_criteria=["criterion 1", "criterion 2"],
        assumptions=["assumption 1"],
    )


def _default_task_definition(task_id: str = "task-001") -> TaskDefinition:
    return TaskDefinition(
        task_id=task_id,
        title=f"Test task {task_id}",
        description=f"Implementation task {task_id}",
        acceptance_checks=["tests pass", "lint clean"],
        estimated_complexity="s",
        capability_type=CapabilityType.AGENT_WITH_TOOLS,
        verification_step="pytest passes",
    )


def _default_task_dag() -> TaskDAG:
    return TaskDAG(
        tasks=[
            _default_task_definition("task-001"),
            _default_task_definition("task-002"),
        ],
        edges=[],
        plan_revision=1,
    )


def _default_implementation_plan() -> ImplementationPlan:
    return ImplementationPlan(
        phases=["foundation", "core"],
        dag=_default_task_dag(),
        plan_revision=1,
    )


def _default_finding(source: str = "code_review_node") -> Finding:
    return Finding(
        finding_id="finding-001",
        source_node=source,
        severity=IssueSeverity.MEDIUM,
        confidence=0.85,
        title="Test finding",
        description="A test finding for verification",
        file_path="src/example.py",
        line_range=(10, 15),
    )


def _default_issue() -> Issue:
    return Issue(
        id="issue-001",
        source_node="code_review_node",
        fingerprint="fp-abc123",
        severity=IssueSeverity.MEDIUM,
        confidence=0.85,
        owner="test-owner",
        disposition=IssueDisposition.CAN_FOLLOW_UP,
        remediation="Fix the thing",
        evidence_links=["finding-001"],
    )


def _succeeded_task(task_id: str = "task-001") -> Task:
    return Task(
        task_id=task_id,
        definition=_default_task_definition(task_id),
        status=TaskStatus.SUCCEEDED,
    )


# Stage definitions mapping pipeline stage to required state
_STAGES = (
    "start",
    "clarification",
    "spec",
    "plan",
    "task_execution",
    "quality_gate",
    "issue_triage",
    "shipping",
    "complete",
)


def make_state(
    stage: str = "start",
    overrides: dict[str, Any] | None = None,
) -> GraphState:
    """Produce a valid GraphState at a given pipeline stage.

    Args:
        stage: Pipeline stage name. One of: start, clarification, spec, plan,
               task_execution, quality_gate, issue_triage, shipping, complete.
        overrides: Optional dict of field overrides applied after stage defaults.

    Returns:
        A valid GraphState instance for the requested stage.
    """
    if stage not in _STAGES:
        msg = f"Unknown stage '{stage}'. Valid stages: {_STAGES}"
        raise ValueError(msg)

    state_kwargs: dict[str, Any] = {
        "run_metadata": _default_run_metadata(),
        "default_model_config": _default_model_config(),
    }

    if stage == "start":
        state_kwargs["run_status"] = RunStatus.PENDING
        state_kwargs["request"] = "Build me a dashboard"

    elif stage == "clarification":
        state_kwargs["run_status"] = RunStatus.RUNNING
        state_kwargs["request"] = "Build me a dashboard"
        state_kwargs["clarified_request"] = _default_clarified_request()
        state_kwargs["ambiguity_status"] = AmbiguityStatus(score=0.0, is_complete=True)
        state_kwargs["clarification_transcript"] = ClarificationTranscript(exchanges=[])

    elif stage == "spec":
        state_kwargs["run_status"] = RunStatus.RUNNING
        state_kwargs["request"] = "Build me a dashboard"
        state_kwargs["clarified_request"] = _default_clarified_request()
        state_kwargs["ambiguity_status"] = AmbiguityStatus(score=0.0, is_complete=True)
        state_kwargs["spec"] = _default_spec_output()

    elif stage == "plan":
        state_kwargs["run_status"] = RunStatus.RUNNING
        state_kwargs["request"] = "Build me a dashboard"
        state_kwargs["clarified_request"] = _default_clarified_request()
        state_kwargs["ambiguity_status"] = AmbiguityStatus(score=0.0, is_complete=True)
        state_kwargs["spec"] = _default_spec_output()
        state_kwargs["implementation_plan"] = _default_implementation_plan()

    elif stage == "task_execution":
        state_kwargs["run_status"] = RunStatus.RUNNING
        state_kwargs["request"] = "Build me a dashboard"
        state_kwargs["clarified_request"] = _default_clarified_request()
        state_kwargs["ambiguity_status"] = AmbiguityStatus(score=0.0, is_complete=True)
        state_kwargs["spec"] = _default_spec_output()
        state_kwargs["implementation_plan"] = _default_implementation_plan()
        state_kwargs["tasks"] = [
            _succeeded_task("task-001"),
            _succeeded_task("task-002"),
        ]

    elif stage == "quality_gate":
        state_kwargs["run_status"] = RunStatus.RUNNING
        state_kwargs["request"] = "Build me a dashboard"
        state_kwargs["clarified_request"] = _default_clarified_request()
        state_kwargs["ambiguity_status"] = AmbiguityStatus(score=0.0, is_complete=True)
        state_kwargs["spec"] = _default_spec_output()
        state_kwargs["implementation_plan"] = _default_implementation_plan()
        state_kwargs["tasks"] = [
            _succeeded_task("task-001"),
        ]
        state_kwargs["review_findings"] = [_default_finding("code_review_node")]
        state_kwargs["security_findings"] = [_default_finding("security_audit_node")]
        state_kwargs["test_findings"] = [_default_finding("test_engineer_node")]

    elif stage == "issue_triage":
        state_kwargs["run_status"] = RunStatus.RUNNING
        state_kwargs["request"] = "Build me a dashboard"
        state_kwargs["clarified_request"] = _default_clarified_request()
        state_kwargs["ambiguity_status"] = AmbiguityStatus(score=0.0, is_complete=True)
        state_kwargs["spec"] = _default_spec_output()
        state_kwargs["implementation_plan"] = _default_implementation_plan()
        state_kwargs["tasks"] = [
            _succeeded_task("task-001"),
        ]
        state_kwargs["review_findings"] = [_default_finding("code_review_node")]
        state_kwargs["triaged_issues"] = [_default_issue()]

    elif stage == "shipping":
        state_kwargs["run_status"] = RunStatus.RUNNING
        state_kwargs["request"] = "Build me a dashboard"
        state_kwargs["clarified_request"] = _default_clarified_request()
        state_kwargs["ambiguity_status"] = AmbiguityStatus(score=0.0, is_complete=True)
        state_kwargs["spec"] = _default_spec_output()
        state_kwargs["implementation_plan"] = _default_implementation_plan()
        state_kwargs["tasks"] = [
            _succeeded_task("task-001"),
        ]
        state_kwargs["triaged_issues"] = [_default_issue()]
        state_kwargs["shipping_readiness"] = ShippingReadiness(is_ready=True)

    elif stage == "complete":
        state_kwargs["run_status"] = RunStatus.SUCCEEDED
        state_kwargs["request"] = "Build me a dashboard"
        state_kwargs["clarified_request"] = _default_clarified_request()
        state_kwargs["ambiguity_status"] = AmbiguityStatus(score=0.0, is_complete=True)
        state_kwargs["spec"] = _default_spec_output()
        state_kwargs["implementation_plan"] = _default_implementation_plan()
        state_kwargs["tasks"] = [
            _succeeded_task("task-001"),
        ]
        state_kwargs["triaged_issues"] = []
        state_kwargs["shipping_readiness"] = ShippingReadiness(is_ready=True)
        state_kwargs["shipping_result"] = ShippingResult(shipped=True, commit_sha="abc123")

    if overrides:
        state_kwargs.update(overrides)

    return GraphState(**state_kwargs)
