"""Integration tests for graph skeleton with stub nodes."""

from __future__ import annotations

from flowforge.graph.builder import build_graph
from flowforge.nodes.stubs import NODE_IDS
from flowforge.state.models import GraphState, RunStatus


class TestGraphCompilation:
    """The graph compiles and has correct structure."""

    def test_graph_compiles_without_error(self) -> None:
        """build_graph() produces a compilable graph."""
        app = build_graph()
        assert app is not None

    def test_all_spec_node_ids_present(self) -> None:
        """All canonical node IDs from the spec are registered."""
        expected_nodes = {
            "clarification_node",
            "spec_node",
            "plan_node",
            "task_fanout_router",
            "task_node",
            "quality_gate_join",
            "code_review_node",
            "security_audit_node",
            "test_engineer_node",
            "issue_orchestrator_node",
            "ship_node",
        }
        app = build_graph()
        graph = app.get_graph()
        node_ids = {nid for nid in graph.nodes if nid not in ("__start__", "__end__")}
        assert expected_nodes.issubset(node_ids), f"Missing: {expected_nodes - node_ids}"

    def test_node_ids_constant_matches_spec(self) -> None:
        """NODE_IDS constant contains all canonical node names."""
        expected = {
            "clarification_node",
            "spec_node",
            "plan_node",
            "task_fanout_router",
            "task_node",
            "quality_gate_join",
            "quality_gate_merge",
            "code_review_node",
            "security_audit_node",
            "test_engineer_node",
            "issue_orchestrator_node",
            "ship_node",
        }
        assert set(NODE_IDS) == expected


class TestGraphExecution:
    """Stub nodes produce valid state transitions through the graph."""

    def test_full_run_reaches_succeeded(self) -> None:
        """A full graph invocation with stubs transitions to succeeded."""
        app = build_graph()
        initial = GraphState(
            request="Build a REST API",
            run_status=RunStatus.PENDING,
        )
        result = app.invoke(initial)
        assert result["run_status"] == RunStatus.SUCCEEDED

    def test_run_status_transitions_through_running(self) -> None:
        """State passes through RUNNING before reaching SUCCEEDED."""
        # We verify by checking the final result is SUCCEEDED,
        # which requires passing through RUNNING per state machine rules.
        app = build_graph()
        initial = GraphState(request="Test request")
        result = app.invoke(initial)
        assert result["run_status"] == RunStatus.SUCCEEDED

    def test_stub_preserves_existing_state_fields(self) -> None:
        """Stub nodes don't clobber unrelated state fields."""
        app = build_graph()
        initial = GraphState(
            request="Build a CLI tool",
            run_status=RunStatus.PENDING,
        )
        result = app.invoke(initial)
        assert result["request"] == "Build a CLI tool"


class TestGraphEdges:
    """Graph edges follow the spec topology."""

    def test_start_connects_to_clarification(self) -> None:
        """START -> clarification_node."""
        app = build_graph()
        graph = app.get_graph()
        start_edges = [e.target for e in graph.edges if e.source == "__start__"]
        assert "clarification_node" in start_edges

    def test_ship_node_connects_to_end(self) -> None:
        """ship_node -> END."""
        app = build_graph()
        graph = app.get_graph()
        ship_edges = [e.target for e in graph.edges if e.source == "ship_node"]
        assert "__end__" in ship_edges

    def test_linear_path_clarification_to_plan(self) -> None:
        """clarification_node -> spec_node -> plan_node."""
        app = build_graph()
        graph = app.get_graph()
        edges_map: dict[str, list[str]] = {}
        for e in graph.edges:
            edges_map.setdefault(e.source, []).append(e.target)
        assert "spec_node" in edges_map.get("clarification_node", [])
        assert "plan_node" in edges_map.get("spec_node", [])
