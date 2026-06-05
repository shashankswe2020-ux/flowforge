"""Unit tests for plan_node and DAG validation."""

from __future__ import annotations

import json

import pytest

from src.dag.validator import CyclicDAGError, validate_dag
from src.nodes.plan import SpecMissingError, plan_node
from src.state.models import (
    GraphState,
    ImplementationPlan,
    RunStatus,
    SpecOutput,
    TaskDAG,
    TaskDefinition,
    TaskDependency,
)
from tests.mocks import MockLLM


def _state_with_spec() -> GraphState:
    """State with completed spec output."""
    return GraphState(
        request="Build a REST API",
        run_status=RunStatus.RUNNING,
        spec=SpecOutput(
            artifact_path="docs/specs/api.md",
            summary="REST API for user management",
            acceptance_criteria=["auth works", "CRUD works"],
            assumptions=["Python 3.12+"],
        ),
    )


def _plan_llm_response(
    *,
    phases: list[str] | None = None,
    tasks: list[dict[str, object]] | None = None,
    edges: list[dict[str, str]] | None = None,
) -> str:
    """Build LLM JSON response for plan generation."""
    default_tasks = [
        {
            "task_id": "task-001",
            "title": "Project scaffold",
            "description": "Set up project structure",
            "acceptance_checks": ["files exist", "builds pass"],
            "estimated_complexity": "s",
            "capability_type": "direct_tool",
            "verification_step": "python -m compileall src",
        },
        {
            "task_id": "task-002",
            "title": "Implement auth",
            "description": "Add authentication module",
            "acceptance_checks": ["login works", "tokens valid"],
            "estimated_complexity": "m",
            "capability_type": "agent_with_tools",
            "verification_step": "pytest tests/test_auth.py",
        },
        {
            "task_id": "task-003",
            "title": "Implement CRUD",
            "description": "Add CRUD endpoints",
            "acceptance_checks": ["create works", "read works"],
            "estimated_complexity": "m",
            "capability_type": "agent_with_tools",
            "verification_step": "pytest tests/test_crud.py",
        },
    ]
    default_edges = [
        {"from_task_id": "task-001", "to_task_id": "task-002"},
        {"from_task_id": "task-001", "to_task_id": "task-003"},
    ]
    return json.dumps(
        {
            "phases": phases or ["foundation", "core features"],
            "tasks": tasks or default_tasks,
            "edges": edges or default_edges,
            "plan_revision": 1,
        },
    )


class TestPlanProduction:
    """plan_node produces a valid ImplementationPlan."""

    def test_produces_implementation_plan(self) -> None:
        """Returns a valid ImplementationPlan."""
        llm = MockLLM(responses=[_plan_llm_response()])
        state = _state_with_spec()
        result = plan_node(state, llm=llm)
        assert result["implementation_plan"] is not None
        assert isinstance(result["implementation_plan"], ImplementationPlan)

    def test_plan_has_phases(self) -> None:
        """Plan includes named phases."""
        llm = MockLLM(
            responses=[
                _plan_llm_response(
                    phases=["scaffold", "core", "polish"],
                ),
            ],
        )
        state = _state_with_spec()
        result = plan_node(state, llm=llm)
        assert result["implementation_plan"].phases == ["scaffold", "core", "polish"]

    def test_plan_has_task_dag(self) -> None:
        """Plan includes a TaskDAG with tasks."""
        llm = MockLLM(responses=[_plan_llm_response()])
        state = _state_with_spec()
        result = plan_node(state, llm=llm)
        dag = result["implementation_plan"].dag
        assert len(dag.tasks) == 3

    def test_tasks_have_deterministic_ids(self) -> None:
        """Task IDs match what the LLM produced."""
        llm = MockLLM(responses=[_plan_llm_response()])
        state = _state_with_spec()
        result = plan_node(state, llm=llm)
        task_ids = [t.task_id for t in result["implementation_plan"].dag.tasks]
        assert task_ids == ["task-001", "task-002", "task-003"]

    def test_tasks_have_verification_steps(self) -> None:
        """Every task has a non-empty verification_step."""
        llm = MockLLM(responses=[_plan_llm_response()])
        state = _state_with_spec()
        result = plan_node(state, llm=llm)
        for task in result["implementation_plan"].dag.tasks:
            assert task.verification_step != ""

    def test_plan_revision_included(self) -> None:
        """Plan includes planRevision metadata."""
        llm = MockLLM(responses=[_plan_llm_response()])
        state = _state_with_spec()
        result = plan_node(state, llm=llm)
        assert result["implementation_plan"].plan_revision == 1

    def test_run_status_stays_running(self) -> None:
        """Successful plan keeps RUNNING status."""
        llm = MockLLM(responses=[_plan_llm_response()])
        state = _state_with_spec()
        result = plan_node(state, llm=llm)
        assert result["run_status"] == RunStatus.RUNNING


class TestSpecValidation:
    """plan_node requires spec to be present."""

    def test_raises_when_no_spec(self) -> None:
        """Raises SpecMissingError when spec is None."""
        llm = MockLLM(responses=[_plan_llm_response()])
        state = GraphState(request="Build something", run_status=RunStatus.RUNNING)
        with pytest.raises(SpecMissingError):
            plan_node(state, llm=llm)

    def test_error_is_user_friendly(self) -> None:
        """Error message is plain language."""
        llm = MockLLM(responses=[_plan_llm_response()])
        state = GraphState(request="x", run_status=RunStatus.RUNNING)
        with pytest.raises(SpecMissingError) as exc_info:
            plan_node(state, llm=llm)
        assert "spec" in str(exc_info.value).lower()


class TestDAGValidation:
    """DAG cycle detection works correctly."""

    def test_valid_dag_passes(self) -> None:
        """Acyclic DAG validates without error."""
        tasks = [
            TaskDefinition(
                task_id="t1",
                title="A",
                description="a",
                acceptance_checks=["ok"],
                estimated_complexity="s",
                capability_type="direct_tool",
                verification_step="check",
            ),
            TaskDefinition(
                task_id="t2",
                title="B",
                description="b",
                acceptance_checks=["ok"],
                estimated_complexity="s",
                capability_type="direct_tool",
                verification_step="check",
            ),
        ]
        edges = [TaskDependency(from_task_id="t1", to_task_id="t2")]
        dag = TaskDAG(tasks=tasks, edges=edges)
        # Should not raise
        validate_dag(dag)

    def test_cyclic_dag_raises(self) -> None:
        """Cycle in DAG raises CyclicDAGError."""
        tasks = [
            TaskDefinition(
                task_id="t1",
                title="A",
                description="a",
                acceptance_checks=["ok"],
                estimated_complexity="s",
                capability_type="direct_tool",
                verification_step="check",
            ),
            TaskDefinition(
                task_id="t2",
                title="B",
                description="b",
                acceptance_checks=["ok"],
                estimated_complexity="s",
                capability_type="direct_tool",
                verification_step="check",
            ),
        ]
        edges = [
            TaskDependency(from_task_id="t1", to_task_id="t2"),
            TaskDependency(from_task_id="t2", to_task_id="t1"),
        ]
        dag = TaskDAG(tasks=tasks, edges=edges)
        with pytest.raises(CyclicDAGError) as exc_info:
            validate_dag(dag)
        assert "cycle" in str(exc_info.value).lower()

    def test_self_loop_raises(self) -> None:
        """Self-referencing edge is detected as cycle."""
        tasks = [
            TaskDefinition(
                task_id="t1",
                title="A",
                description="a",
                acceptance_checks=["ok"],
                estimated_complexity="s",
                capability_type="direct_tool",
                verification_step="check",
            ),
        ]
        edges = [TaskDependency(from_task_id="t1", to_task_id="t1")]
        dag = TaskDAG(tasks=tasks, edges=edges)
        with pytest.raises(CyclicDAGError):
            validate_dag(dag)

    def test_complex_acyclic_passes(self) -> None:
        """Diamond-shaped DAG (no cycle) validates."""
        tasks = [
            TaskDefinition(
                task_id=f"t{i}",
                title=f"T{i}",
                description="d",
                acceptance_checks=["ok"],
                estimated_complexity="s",
                capability_type="direct_tool",
                verification_step="check",
            )
            for i in range(1, 5)
        ]
        edges = [
            TaskDependency(from_task_id="t1", to_task_id="t2"),
            TaskDependency(from_task_id="t1", to_task_id="t3"),
            TaskDependency(from_task_id="t2", to_task_id="t4"),
            TaskDependency(from_task_id="t3", to_task_id="t4"),
        ]
        dag = TaskDAG(tasks=tasks, edges=edges)
        validate_dag(dag)  # Should not raise

    def test_plan_node_rejects_cyclic_llm_output(self) -> None:
        """plan_node raises CyclicDAGError if LLM produces a cyclic DAG."""
        cyclic_response = _plan_llm_response(
            tasks=[
                {
                    "task_id": "t1",
                    "title": "A",
                    "description": "a",
                    "acceptance_checks": ["ok"],
                    "estimated_complexity": "s",
                    "capability_type": "direct_tool",
                    "verification_step": "check",
                },
                {
                    "task_id": "t2",
                    "title": "B",
                    "description": "b",
                    "acceptance_checks": ["ok"],
                    "estimated_complexity": "s",
                    "capability_type": "direct_tool",
                    "verification_step": "check",
                },
            ],
            edges=[
                {"from_task_id": "t1", "to_task_id": "t2"},
                {"from_task_id": "t2", "to_task_id": "t1"},
            ],
        )
        llm = MockLLM(responses=[cyclic_response])
        state = _state_with_spec()
        with pytest.raises(CyclicDAGError):
            plan_node(state, llm=llm)


class TestLLMInteraction:
    """plan_node interacts correctly with the LLM."""

    def test_llm_receives_spec_context(self) -> None:
        """LLM prompt includes spec details."""
        llm = MockLLM(responses=[_plan_llm_response()])
        state = _state_with_spec()
        plan_node(state, llm=llm)
        prompt = llm.call_history[0]
        assert "user management" in prompt.lower() or "REST API" in prompt

    def test_llm_called_once(self) -> None:
        """Exactly one LLM call per plan_node invocation."""
        llm = MockLLM(responses=[_plan_llm_response()])
        state = _state_with_spec()
        plan_node(state, llm=llm)
        assert llm.call_count == 1
