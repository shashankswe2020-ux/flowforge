"""Pydantic models for the canonical FlowForge graph state."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from flowforge.deep_agents import AgentRole  # noqa: TC001  # runtime use by Pydantic

# --- Enums ---


class RunStatus(StrEnum):
    """Run-level status values per state machine spec."""

    PENDING = "pending"
    RUNNING = "running"
    WAITING_FOR_INPUT = "waiting_for_input"
    BLOCKED = "blocked"
    FAILED = "failed"
    SUCCEEDED = "succeeded"
    CANCELLED = "cancelled"


class TaskStatus(StrEnum):
    """Task-level status values per state machine spec."""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class CapabilityType(StrEnum):
    """Node capability type declaration."""

    AGENT_ONLY = "agent_only"
    AGENT_WITH_TOOLS = "agent_with_tools"
    DIRECT_TOOL = "direct_tool"


class IssueSeverity(StrEnum):
    """Issue severity levels."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class IssueDisposition(StrEnum):
    """Issue triage disposition."""

    MUST_FIX_BEFORE_SHIP = "must_fix_before_ship"
    CAN_FOLLOW_UP = "can_follow_up"
    REJECTED = "rejected"


class ToolSideEffect(StrEnum):
    """Tool side-effect classification."""

    READ_ONLY = "read_only"
    WRITE_SCOPED = "write_scoped"
    DESTRUCTIVE = "destructive"


# --- Model Configuration ---


class DefaultModelConfig(BaseModel):
    """Run-level default model/provider and decoding parameters."""

    model_id: str
    provider: str
    temperature: float = 0.0
    max_tokens: int = 4096
    additional_params: dict[str, str | int | float | bool] = Field(default_factory=dict)


class NodeModelOverride(BaseModel):
    """Per-node model selection override."""

    node_id: str
    model_id: str
    provider: str
    temperature: float | None = None
    max_tokens: int | None = None
    additional_params: dict[str, str | int | float | bool] = Field(default_factory=dict)


# --- Clarification ---


class ClarificationQA(BaseModel):
    """Single question/answer pair in clarification transcript."""

    question: str
    answer: str | None = None
    dimension: str
    timestamp: datetime


class ClarificationTranscript(BaseModel):
    """Full Q&A history from clarification loop."""

    exchanges: list[ClarificationQA] = Field(default_factory=list)


class AmbiguityStatus(BaseModel):
    """Tracks unresolved dimensions and deferments from clarification."""

    score: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="0.0 = fully resolved, 1.0 = fully ambiguous",
    )
    unresolved_dimensions: list[str] = Field(default_factory=list)
    deferred_dimensions: list[str] = Field(default_factory=list)
    is_complete: bool = False


class ClarifiedRequest(BaseModel):
    """Normalized request after conversational clarification."""

    solution_type: str
    scope_size: str
    target_users: str
    must_have: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    tech_preferences: list[str] = Field(default_factory=list)
    summary: str


# --- Spec ---


class SpecOutput(BaseModel):
    """Output from spec_node — structured specification document."""

    artifact_path: str
    summary: str
    objective: str = ""
    target_users: str = ""
    acceptance_criteria: list[str]
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    tech_stack: list[str] = Field(default_factory=list)
    commands: dict[str, str] = Field(default_factory=dict)  # build/test/lint/dev
    project_structure: list[str] = Field(default_factory=list)
    code_style: list[str] = Field(default_factory=list)
    security_considerations: list[str] = Field(default_factory=list)
    testing_strategy: list[str] = Field(default_factory=list)
    boundaries: dict[str, list[str]] = Field(default_factory=dict)  # always/ask_first/never


# --- Plan and DAG ---


class TaskDependency(BaseModel):
    """Edge in the task DAG."""

    from_task_id: str
    to_task_id: str


class TaskDefinition(BaseModel):
    """A single task in the implementation plan DAG."""

    task_id: str
    title: str
    description: str
    acceptance_checks: list[str]
    estimated_complexity: Annotated[str, Field(pattern=r"^(xs|s|m|l)$")]
    capability_type: CapabilityType
    verification_step: str


class TaskDAG(BaseModel):
    """Acyclic task dependency graph from plan_node."""

    tasks: list[TaskDefinition]
    edges: list[TaskDependency] = Field(default_factory=list)
    plan_revision: int = 1


class ImplementationPlan(BaseModel):
    """Full implementation plan produced by plan_node."""

    phases: list[str]
    dag: TaskDAG
    plan_revision: int = 1


# --- Task Execution ---


class TaskArtifact(BaseModel):
    """Artifact produced by a task execution."""

    artifact_id: str
    artifact_type: str
    path: str
    fingerprint: str
    content: str = ""


class Task(BaseModel):
    """Runtime task state tracking execution progress."""

    task_id: str
    definition: TaskDefinition
    status: TaskStatus = TaskStatus.PENDING
    artifacts: list[TaskArtifact] = Field(default_factory=list)
    verification_evidence: list[str] = Field(default_factory=list)
    retry_count: int = 0
    max_retries: int = 3
    error_message: str | None = None
    idempotency_key: str | None = None


# --- Quality Findings ---


class Finding(BaseModel):
    """Structured finding from a quality gate node."""

    finding_id: str
    source_node: str
    severity: IssueSeverity
    confidence: float = Field(ge=0.0, le=1.0)
    title: str
    description: str
    file_path: str | None = None
    line_range: tuple[int, int] | None = None
    suggestion: str | None = None
    evidence_links: list[str] = Field(default_factory=list)


# --- Issues ---


class Issue(BaseModel):
    """Triaged issue from issue_orchestrator_node."""

    id: str
    source_node: str
    fingerprint: str
    severity: IssueSeverity
    confidence: float = Field(ge=0.0, le=1.0)
    owner: str | None = None
    disposition: IssueDisposition
    remediation: str
    evidence_links: list[str] = Field(default_factory=list)
    sla_target: str | None = None


# --- Shipping ---


class ShippingBlocker(BaseModel):
    """A specific blocker preventing shipping."""

    blocker_id: str
    severity: IssueSeverity
    reason: str
    source_issue_id: str | None = None


class ShippingReadiness(BaseModel):
    """Computed shipping readiness report."""

    is_ready: bool = False
    blockers: list[ShippingBlocker] = Field(default_factory=list)
    blocker_count_by_severity: dict[str, int] = Field(default_factory=dict)
    unresolved_must_fix: int = 0
    waived_by: str | None = None
    waiver_reason: str | None = None
    decision: str | None = None
    decision_timestamp: datetime | None = None


class ShippingResult(BaseModel):
    """Output from a successful shipping action."""

    shipped: bool = False
    release_url: str | None = None
    pr_url: str | None = None
    repo_url: str | None = None
    commit_sha: str | None = None
    ship_timestamp: datetime | None = None
    provenance_chain: list[str] = Field(default_factory=list)


# --- Run Metadata ---


class RunMetadata(BaseModel):
    """Metadata tracking run-level context and audit trail."""

    correlation_id: str
    actor_identity: str
    policy_version: str
    start_time: datetime | None = None
    end_time: datetime | None = None
    node_durations: dict[str, float] = Field(default_factory=dict)
    retry_counts: dict[str, int] = Field(default_factory=dict)
    model_usage: dict[str, dict[str, int]] = Field(default_factory=dict)
    gate_decisions: dict[str, str] = Field(default_factory=dict)
    model_config_version: int = 1


# --- Deep Agent Trace (spec §8.1) ---


class Todo(BaseModel):
    """A single ``write_todos`` planning entry."""

    content: str
    status: Literal["pending", "in_progress", "completed"] = "pending"


class ToolInvocationRecord(BaseModel):
    """Audit record for one Deep Agent tool call."""

    tool: str
    ok: bool
    duration_ms: int = 0
    parent: str | None = None
    error: str | None = None


class DeepAgentTrace(BaseModel):
    """Per-node Deep Agent execution trace (spec §8.1)."""

    role: AgentRole
    todos: list[Todo] = Field(default_factory=list)
    vfs_keys: list[str] = Field(default_factory=list)
    messages_digest: str
    duration_ms: int = 0
    recursion_depth: int = 0
    tool_invocations: list[ToolInvocationRecord] = Field(default_factory=list)

    @staticmethod
    def digest_messages(messages: list[dict[str, object]]) -> str:
        """Return a deterministic sha256 over the canonical-JSON message list.

        Canonicalisation: ``json.dumps`` with ``sort_keys=True`` and
        compact separators, so dict-key ordering does not affect the
        digest.
        """
        canonical = json.dumps(
            messages, sort_keys=True, separators=(",", ":"), default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- Canonical Graph State ---


class GraphState(BaseModel):
    """Top-level canonical graph state shared by all nodes."""

    # Run status
    run_status: RunStatus = RunStatus.PENDING

    # Request and clarification
    request: str = ""
    clarified_request: ClarifiedRequest | None = None
    clarification_transcript: ClarificationTranscript = Field(
        default_factory=lambda: ClarificationTranscript(),
    )
    ambiguity_status: AmbiguityStatus = Field(default_factory=lambda: AmbiguityStatus())
    auto_clarify: bool = False  # If True, clarification_node resolves all dimensions in one LLM call

    # Target project working directory and repo (file writes + git/gh ops happen here)
    workdir: str | None = None
    target_repo: str | None = None
    repo_url: str | None = None

    # Model configuration
    default_model_config: DefaultModelConfig | None = None
    node_model_overrides: list[NodeModelOverride] = Field(default_factory=list)

    # Spec
    spec: SpecOutput | None = None

    # Plan
    implementation_plan: ImplementationPlan | None = None

    # Tasks
    tasks: list[Task] = Field(default_factory=list)

    # Quality findings
    review_findings: list[Finding] = Field(default_factory=list)
    security_findings: list[Finding] = Field(default_factory=list)
    test_findings: list[Finding] = Field(default_factory=list)
    proposed_tasks: list[TaskDefinition] = Field(default_factory=list)
    quality_iteration: int = 0

    # Issue triage
    triaged_issues: list[Issue] = Field(default_factory=list)

    # Shipping
    shipping_readiness: ShippingReadiness = Field(default_factory=lambda: ShippingReadiness())
    shipping_result: ShippingResult = Field(default_factory=lambda: ShippingResult())

    # Run metadata
    run_metadata: RunMetadata | None = None

    # Deep Agent traces, keyed by LangGraph node name (spec §8.1)
    deep_agent_traces: dict[str, DeepAgentTrace] = Field(default_factory=dict)
