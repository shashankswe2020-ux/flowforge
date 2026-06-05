"""Unit tests for task_node executor with 3 capability types."""

from __future__ import annotations

import json

from flowforge.nodes.capability import (
    AgentOnlyExecutor,
    AgentWithToolsExecutor,
    DirectToolExecutor,
)
from flowforge.nodes.task_executor import execute_task
from flowforge.state.models import (
    CapabilityType,
    Task,
    TaskDefinition,
    TaskStatus,
)
from tests.mocks import MockLLM


def _make_definition(
    task_id: str = "task-001",
    capability: CapabilityType = CapabilityType.AGENT_WITH_TOOLS,
) -> TaskDefinition:
    return TaskDefinition(
        task_id=task_id,
        title="Test task",
        description="Do something testable",
        acceptance_checks=["output exists", "no errors"],
        estimated_complexity="s",
        capability_type=capability,
        verification_step="pytest tests/test_it.py",
    )


def _make_task(
    task_id: str = "task-001",
    capability: CapabilityType = CapabilityType.AGENT_WITH_TOOLS,
) -> Task:
    return Task(
        task_id=task_id,
        definition=_make_definition(task_id, capability),
        status=TaskStatus.RUNNING,
    )


class TestCapabilityTypeDispatch:
    """Each capability type executes with correct behavior."""

    def test_agent_only_uses_llm(self) -> None:
        """AGENT_ONLY invokes LLM without tools."""
        llm = MockLLM(
            responses=[
                json.dumps(
                    {
                        "status": "succeeded",
                        "artifacts": [
                            {
                                "artifact_id": "a1",
                                "artifact_type": "code",
                                "path": "src/feature.py",
                                "fingerprint": "abc123",
                            },
                        ],
                        "verification_evidence": ["all tests pass"],
                    },
                ),
            ],
        )
        task = _make_task(capability=CapabilityType.AGENT_ONLY)
        result = execute_task(task, llm=llm)
        assert result.status == TaskStatus.SUCCEEDED
        assert llm.call_count == 1

    def test_agent_with_tools_uses_llm(self) -> None:
        """AGENT_WITH_TOOLS invokes LLM (tools handled separately)."""
        llm = MockLLM(
            responses=[
                json.dumps(
                    {
                        "status": "succeeded",
                        "artifacts": [],
                        "verification_evidence": ["build passes"],
                    },
                ),
            ],
        )
        task = _make_task(capability=CapabilityType.AGENT_WITH_TOOLS)
        result = execute_task(task, llm=llm)
        assert result.status == TaskStatus.SUCCEEDED
        assert llm.call_count == 1

    def test_direct_tool_no_llm(self) -> None:
        """DIRECT_TOOL executes without LLM."""
        task = _make_task(capability=CapabilityType.DIRECT_TOOL)
        result = execute_task(task, llm=None)
        assert result.status == TaskStatus.SUCCEEDED

    def test_direct_tool_ignores_provided_llm(self) -> None:
        """DIRECT_TOOL does not invoke LLM even if provided."""
        llm = MockLLM(responses=["should not be called"])
        task = _make_task(capability=CapabilityType.DIRECT_TOOL)
        execute_task(task, llm=llm)
        assert llm.call_count == 0


class TestTaskExecutionResult:
    """TaskExecutionResult has correct structure."""

    def test_result_contains_status(self) -> None:
        """Result includes final task status."""
        llm = MockLLM(
            responses=[
                json.dumps(
                    {
                        "status": "succeeded",
                        "artifacts": [],
                        "verification_evidence": [],
                    },
                ),
            ],
        )
        task = _make_task(capability=CapabilityType.AGENT_ONLY)
        result = execute_task(task, llm=llm)
        assert result.status in (TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.BLOCKED)

    def test_result_contains_artifacts(self) -> None:
        """Result includes produced artifacts."""
        llm = MockLLM(
            responses=[
                json.dumps(
                    {
                        "status": "succeeded",
                        "artifacts": [
                            {
                                "artifact_id": "art-1",
                                "artifact_type": "file",
                                "path": "src/main.py",
                                "fingerprint": "sha256:abc",
                            },
                        ],
                        "verification_evidence": [],
                    },
                ),
            ],
        )
        task = _make_task(capability=CapabilityType.AGENT_ONLY)
        result = execute_task(task, llm=llm)
        assert len(result.artifacts) == 1
        assert result.artifacts[0].artifact_id == "art-1"

    def test_result_contains_verification_evidence(self) -> None:
        """Result includes verification evidence."""
        llm = MockLLM(
            responses=[
                json.dumps(
                    {
                        "status": "succeeded",
                        "artifacts": [],
                        "verification_evidence": ["pytest: 10 passed", "mypy: clean"],
                    },
                ),
            ],
        )
        task = _make_task(capability=CapabilityType.AGENT_ONLY)
        result = execute_task(task, llm=llm)
        assert len(result.verification_evidence) == 2


class TestSchemaValidation:
    """Task-level Pydantic schema validation enforced."""

    def test_invalid_llm_response_produces_failed(self) -> None:
        """Malformed LLM response results in FAILED status."""
        llm = MockLLM(responses=["not valid json at all"])
        task = _make_task(capability=CapabilityType.AGENT_ONLY)
        result = execute_task(task, llm=llm)
        assert result.status == TaskStatus.FAILED
        assert result.error_message is not None

    def test_missing_required_fields_produces_failed(self) -> None:
        """LLM response missing required fields results in FAILED."""
        llm = MockLLM(responses=[json.dumps({"status": "succeeded"})])
        task = _make_task(capability=CapabilityType.AGENT_ONLY)
        result = execute_task(task, llm=llm)
        assert result.status == TaskStatus.FAILED


class TestSoleWriter:
    """Node is sole writer for execution result and evidence."""

    def test_result_only_updates_own_task(self) -> None:
        """Execution result applies only to the executed task."""
        llm = MockLLM(
            responses=[
                json.dumps(
                    {
                        "status": "succeeded",
                        "artifacts": [],
                        "verification_evidence": ["ok"],
                    },
                ),
            ],
        )
        task = _make_task(task_id="task-007", capability=CapabilityType.AGENT_ONLY)
        result = execute_task(task, llm=llm)
        assert result.task_id == "task-007"

    def test_idempotency_key_set(self) -> None:
        """Execution sets an idempotency key for retry safety."""
        llm = MockLLM(
            responses=[
                json.dumps(
                    {
                        "status": "succeeded",
                        "artifacts": [],
                        "verification_evidence": [],
                    },
                ),
            ],
        )
        task = _make_task(capability=CapabilityType.AGENT_ONLY)
        result = execute_task(task, llm=llm)
        assert result.idempotency_key is not None
        assert len(result.idempotency_key) > 0


class TestExecutors:
    """Individual executor classes work correctly."""

    def test_agent_only_executor(self) -> None:
        """AgentOnlyExecutor produces valid result."""
        llm = MockLLM(
            responses=[
                json.dumps(
                    {
                        "status": "succeeded",
                        "artifacts": [],
                        "verification_evidence": ["done"],
                    },
                ),
            ],
        )
        executor = AgentOnlyExecutor()
        task = _make_task(capability=CapabilityType.AGENT_ONLY)
        result = executor.execute(task, llm=llm)
        assert result.status == TaskStatus.SUCCEEDED

    def test_agent_with_tools_executor(self) -> None:
        """AgentWithToolsExecutor produces valid result."""
        llm = MockLLM(
            responses=[
                json.dumps(
                    {
                        "status": "succeeded",
                        "artifacts": [],
                        "verification_evidence": ["done"],
                    },
                ),
            ],
        )
        executor = AgentWithToolsExecutor()
        task = _make_task(capability=CapabilityType.AGENT_WITH_TOOLS)
        result = executor.execute(task, llm=llm)
        assert result.status == TaskStatus.SUCCEEDED

    def test_direct_tool_executor(self) -> None:
        """DirectToolExecutor produces valid result without LLM."""
        executor = DirectToolExecutor()
        task = _make_task(capability=CapabilityType.DIRECT_TOOL)
        result = executor.execute(task, llm=None)
        assert result.status == TaskStatus.SUCCEEDED
