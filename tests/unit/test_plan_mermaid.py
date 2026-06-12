"""Tests for plan node Mermaid DAG rendering."""

from __future__ import annotations

from flowforge.nodes.plan import _render_mermaid_dag


def test_mermaid_renders_nodes_with_titles_and_edges() -> None:
    parsed = {
        "tasks": [
            {"task_id": "t1", "title": "Set up scaffold"},
            {"task_id": "t2", "title": "Implement core"},
            {"task_id": "t3", "title": "Add tests"},
        ],
        "edges": [
            {"from_task_id": "t1", "to_task_id": "t2"},
            {"from_task_id": "t2", "to_task_id": "t3"},
        ],
    }
    out = _render_mermaid_dag(parsed)
    assert out.startswith("graph TD")
    assert 't1["Set up scaffold"]' in out
    assert 't2["Implement core"]' in out
    assert "t1 --> t2" in out
    assert "t2 --> t3" in out


def test_mermaid_handles_empty_plan() -> None:
    out = _render_mermaid_dag({"tasks": [], "edges": []})
    assert out.startswith("graph TD")
    assert "%% no tasks" in out


def test_mermaid_escapes_double_quotes_in_titles() -> None:
    parsed = {
        "tasks": [{"task_id": "t1", "title": 'a "quoted" title'}],
        "edges": [],
    }
    out = _render_mermaid_dag(parsed)
    assert '"' in out
    assert '"quoted"' not in out  # double-quotes replaced with single
    assert "'quoted'" in out


def test_plan_markdown_uses_mermaid_fence() -> None:
    from flowforge.nodes.plan import _render_plan_markdown
    from flowforge.state.models import (
        CapabilityType,
        ImplementationPlan,
        TaskDAG,
        TaskDefinition,
        TaskDependency,
    )

    defn = TaskDefinition(
        task_id="t1",
        title="x",
        description="d",
        acceptance_checks=["a"],
        estimated_complexity="xs",
        capability_type=CapabilityType.AGENT_ONLY,
        verification_step="pytest",
    )
    plan = ImplementationPlan(
        phases=["p"],
        dag=TaskDAG(
            tasks=[defn],
            edges=[TaskDependency(from_task_id="t1", to_task_id="t1")],
        ),
    )
    parsed = {
        "phases": ["p"],
        "tasks": [{"task_id": "t1", "title": "x"}],
        "edges": [{"from_task_id": "t1", "to_task_id": "t1"}],
    }
    md = _render_plan_markdown(parsed, plan)
    assert "```mermaid" in md
    assert "graph TD" in md
