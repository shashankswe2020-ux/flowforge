"""Tests for state schema models — validation, serialization, and type safety."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from flowforge.state.models import (
    AmbiguityStatus,
    CapabilityType,
    ClarificationQA,
    ClarificationTranscript,
    ClarifiedRequest,
    DefaultModelConfig,
    Finding,
    GraphState,
    ImplementationPlan,
    Issue,
    IssueDisposition,
    IssueSeverity,
    NodeModelOverride,
    RunMetadata,
    RunStatus,
    ShippingBlocker,
    ShippingReadiness,
    ShippingResult,
    SpecOutput,
    Task,
    TaskDAG,
    TaskDefinition,
    TaskDependency,
    TaskStatus,
    ToolSideEffect,
)


class TestEnums:
    """Verify enum values match spec definitions."""

    def test_run_status_values(self) -> None:
        assert RunStatus.PENDING == "pending"
        assert RunStatus.RUNNING == "running"
        assert RunStatus.WAITING_FOR_INPUT == "waiting_for_input"
        assert RunStatus.BLOCKED == "blocked"
        assert RunStatus.FAILED == "failed"
        assert RunStatus.SUCCEEDED == "succeeded"
        assert RunStatus.CANCELLED == "cancelled"
        assert len(RunStatus) == 7

    def test_task_status_values(self) -> None:
        assert TaskStatus.PENDING == "pending"
        assert TaskStatus.READY == "ready"
        assert TaskStatus.RUNNING == "running"
        assert TaskStatus.RETRYING == "retrying"
        assert TaskStatus.SUCCEEDED == "succeeded"
        assert TaskStatus.FAILED == "failed"
        assert TaskStatus.BLOCKED == "blocked"
        assert TaskStatus.SKIPPED == "skipped"
        assert TaskStatus.CANCELLED == "cancelled"
        assert len(TaskStatus) == 9

    def test_capability_type_values(self) -> None:
        assert CapabilityType.AGENT_ONLY == "agent_only"
        assert CapabilityType.AGENT_WITH_TOOLS == "agent_with_tools"
        assert CapabilityType.DIRECT_TOOL == "direct_tool"

    def test_issue_severity_values(self) -> None:
        assert len(IssueSeverity) == 5

    def test_issue_disposition_values(self) -> None:
        assert IssueDisposition.MUST_FIX_BEFORE_SHIP == "must_fix_before_ship"
        assert IssueDisposition.CAN_FOLLOW_UP == "can_follow_up"
        assert IssueDisposition.REJECTED == "rejected"

    def test_tool_side_effect_values(self) -> None:
        assert ToolSideEffect.READ_ONLY == "read_only"
        assert ToolSideEffect.WRITE_SCOPED == "write_scoped"
        assert ToolSideEffect.DESTRUCTIVE == "destructive"


class TestDefaultModelConfig:
    """Tests for model configuration schema."""

    def test_valid_config(self) -> None:
        config = DefaultModelConfig(
            model_id="claude-sonnet-4-20250514",
            provider="anthropic",
            temperature=0.2,
            max_tokens=8192,
        )
        assert config.model_id == "claude-sonnet-4-20250514"
        assert config.provider == "anthropic"
        assert config.temperature == 0.2
        assert config.max_tokens == 8192

    def test_defaults(self) -> None:
        config = DefaultModelConfig(model_id="gpt-4o", provider="openai")
        assert config.temperature == 0.0
        assert config.max_tokens == 4096
        assert config.additional_params == {}

    def test_node_override(self) -> None:
        override = NodeModelOverride(
            node_id="security_audit_node",
            model_id="claude-sonnet-4-20250514",
            provider="anthropic",
        )
        assert override.node_id == "security_audit_node"
        assert override.temperature is None


class TestClarificationModels:
    """Tests for clarification-related state models."""

    def test_clarification_qa(self) -> None:
        qa = ClarificationQA(
            question="What type of application?",
            answer="A web app",
            dimension="solution_type",
            timestamp=datetime(2026, 6, 5, tzinfo=UTC),
        )
        assert qa.answer == "A web app"

    def test_clarification_transcript_empty(self) -> None:
        transcript = ClarificationTranscript()
        assert transcript.exchanges == []

    def test_ambiguity_status_score_bounds(self) -> None:
        valid = AmbiguityStatus(score=0.5)
        assert valid.score == 0.5

        with pytest.raises(ValidationError):
            AmbiguityStatus(score=-0.1)

        with pytest.raises(ValidationError):
            AmbiguityStatus(score=1.1)

    def test_clarified_request(self) -> None:
        req = ClarifiedRequest(
            solution_type="web_app",
            scope_size="prototype",
            target_users="internal engineers",
            must_have=["auth", "dashboard"],
            success_criteria=["loads in <2s"],
            summary="Internal dashboard prototype",
        )
        assert req.solution_type == "web_app"
        assert len(req.must_have) == 2


class TestSpecOutput:
    """Tests for spec output schema."""

    def test_valid_spec_output(self) -> None:
        spec = SpecOutput(
            artifact_path="docs/specs/feature.md",
            summary="Feature spec for X",
            acceptance_criteria=["criterion 1", "criterion 2"],
        )
        assert spec.artifact_path == "docs/specs/feature.md"
        assert len(spec.acceptance_criteria) == 2


class TestTaskDAGModels:
    """Tests for plan and DAG models."""

    def test_task_definition_valid_complexity(self) -> None:
        task_def = TaskDefinition(
            task_id="task-001",
            title="Scaffold project",
            description="Create project structure",
            acceptance_checks=["files exist"],
            estimated_complexity="s",
            capability_type=CapabilityType.DIRECT_TOOL,
            verification_step="pytest passes",
        )
        assert task_def.estimated_complexity == "s"

    def test_task_definition_invalid_complexity(self) -> None:
        with pytest.raises(ValidationError):
            TaskDefinition(
                task_id="task-001",
                title="X",
                description="Y",
                acceptance_checks=["z"],
                estimated_complexity="xl",
                capability_type=CapabilityType.AGENT_ONLY,
                verification_step="check",
            )

    def test_task_dag(self) -> None:
        dag = TaskDAG(
            tasks=[
                TaskDefinition(
                    task_id="t1",
                    title="A",
                    description="Do A",
                    acceptance_checks=["done"],
                    estimated_complexity="s",
                    capability_type=CapabilityType.AGENT_ONLY,
                    verification_step="test",
                ),
                TaskDefinition(
                    task_id="t2",
                    title="B",
                    description="Do B",
                    acceptance_checks=["done"],
                    estimated_complexity="m",
                    capability_type=CapabilityType.AGENT_WITH_TOOLS,
                    verification_step="test",
                ),
            ],
            edges=[TaskDependency(from_task_id="t1", to_task_id="t2")],
            plan_revision=1,
        )
        assert len(dag.tasks) == 2
        assert dag.edges[0].from_task_id == "t1"

    def test_implementation_plan(self) -> None:
        dag = TaskDAG(tasks=[], plan_revision=1)
        plan = ImplementationPlan(phases=["foundation", "core"], dag=dag)
        assert len(plan.phases) == 2


class TestTaskExecution:
    """Tests for task execution state tracking."""

    def test_task_defaults(self) -> None:
        defn = TaskDefinition(
            task_id="t1",
            title="T",
            description="D",
            acceptance_checks=["c"],
            estimated_complexity="s",
            capability_type=CapabilityType.DIRECT_TOOL,
            verification_step="v",
        )
        task = Task(task_id="t1", definition=defn)
        assert task.status == TaskStatus.PENDING
        assert task.retry_count == 0
        assert task.artifacts == []


class TestFindingAndIssue:
    """Tests for quality finding and issue models."""

    def test_finding_valid(self) -> None:
        finding = Finding(
            finding_id="f1",
            source_node="code_review_node",
            severity=IssueSeverity.HIGH,
            confidence=0.9,
            title="Unused import",
            description="Module X imported but unused",
            file_path="src/foo.py",
            line_range=(10, 10),
        )
        assert finding.severity == IssueSeverity.HIGH

    def test_finding_confidence_bounds(self) -> None:
        with pytest.raises(ValidationError):
            Finding(
                finding_id="f1",
                source_node="x",
                severity=IssueSeverity.LOW,
                confidence=1.5,
                title="T",
                description="D",
            )

    def test_issue_valid(self) -> None:
        issue = Issue(
            id="i1",
            source_node="security_audit_node",
            fingerprint="abc123",
            severity=IssueSeverity.CRITICAL,
            confidence=0.95,
            disposition=IssueDisposition.MUST_FIX_BEFORE_SHIP,
            remediation="Fix the vulnerability",
        )
        assert issue.disposition == IssueDisposition.MUST_FIX_BEFORE_SHIP


class TestShippingModels:
    """Tests for shipping readiness and result."""

    def test_shipping_readiness_default(self) -> None:
        readiness = ShippingReadiness()
        assert readiness.is_ready is False
        assert readiness.blockers == []

    def test_shipping_blocker(self) -> None:
        blocker = ShippingBlocker(
            blocker_id="b1",
            severity=IssueSeverity.CRITICAL,
            reason="Unresolved critical vulnerability",
        )
        assert blocker.severity == IssueSeverity.CRITICAL

    def test_shipping_result_default(self) -> None:
        result = ShippingResult()
        assert result.shipped is False
        assert result.provenance_chain == []


class TestRunMetadata:
    """Tests for run metadata model."""

    def test_valid_metadata(self) -> None:
        meta = RunMetadata(
            correlation_id="run-001",
            actor_identity="user@example.com",
            policy_version="v1.0",
        )
        assert meta.correlation_id == "run-001"
        assert meta.node_durations == {}


class TestGraphState:
    """Tests for top-level graph state."""

    def test_default_graph_state(self) -> None:
        state = GraphState()
        assert state.run_status == RunStatus.PENDING
        assert state.request == ""
        assert state.clarified_request is None
        assert state.default_model_config is None
        assert state.spec is None
        assert state.implementation_plan is None
        assert state.tasks == []
        assert state.review_findings == []
        assert state.triaged_issues == []
        assert state.shipping_readiness.is_ready is False
        assert state.run_metadata is None

    def test_graph_state_with_model_config(self) -> None:
        state = GraphState(
            default_model_config=DefaultModelConfig(
                model_id="gpt-4o",
                provider="openai",
            ),
            node_model_overrides=[
                NodeModelOverride(
                    node_id="spec_node",
                    model_id="claude-sonnet-4-20250514",
                    provider="anthropic",
                ),
            ],
        )
        assert state.default_model_config is not None
        assert state.default_model_config.model_id == "gpt-4o"
        assert len(state.node_model_overrides) == 1

    def test_graph_state_round_trip_serialization(self) -> None:
        state = GraphState(
            run_status=RunStatus.RUNNING,
            request="Build me a CLI tool",
            run_metadata=RunMetadata(
                correlation_id="r1",
                actor_identity="agent",
                policy_version="v1",
            ),
        )
        json_str = state.model_dump_json()
        restored = GraphState.model_validate_json(json_str)
        assert restored.run_status == RunStatus.RUNNING
        assert restored.request == "Build me a CLI tool"
        assert restored.run_metadata is not None
        assert restored.run_metadata.correlation_id == "r1"

    def test_graph_state_full_round_trip(self) -> None:
        """Full state with all nested models survives serialization."""
        state = GraphState(
            run_status=RunStatus.SUCCEEDED,
            request="Build a web app",
            clarified_request=ClarifiedRequest(
                solution_type="web_app",
                scope_size="production",
                target_users="consumers",
                must_have=["auth"],
                summary="Consumer web app",
            ),
            ambiguity_status=AmbiguityStatus(score=0.0, is_complete=True),
            default_model_config=DefaultModelConfig(
                model_id="gpt-4o",
                provider="openai",
            ),
            spec=SpecOutput(
                artifact_path="spec.md",
                summary="Spec",
                acceptance_criteria=["works"],
            ),
            implementation_plan=ImplementationPlan(
                phases=["p1"],
                dag=TaskDAG(tasks=[], plan_revision=1),
            ),
            shipping_result=ShippingResult(shipped=True, commit_sha="abc123"),
            run_metadata=RunMetadata(
                correlation_id="r1",
                actor_identity="ci",
                policy_version="v1",
            ),
        )
        json_str = state.model_dump_json()
        restored = GraphState.model_validate_json(json_str)
        assert restored.run_status == RunStatus.SUCCEEDED
        assert restored.clarified_request is not None
        assert restored.clarified_request.solution_type == "web_app"
        assert restored.spec is not None
        assert restored.shipping_result.shipped is True
