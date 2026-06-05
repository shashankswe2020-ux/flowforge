"""Stub node implementations for graph skeleton."""

from __future__ import annotations

from typing import Any

from src.state.models import GraphState, RunStatus

# Canonical node IDs per spec
NODE_IDS: tuple[str, ...] = (
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
)


def clarification_node(state: GraphState) -> dict[str, Any]:
    """Stub: passes state through, transitions to RUNNING."""
    return {"run_status": RunStatus.RUNNING}


def spec_node(state: GraphState) -> dict[str, Any]:
    """Stub: passes state through."""
    return {}


def plan_node(state: GraphState) -> dict[str, Any]:
    """Stub: passes state through."""
    return {}


def task_fanout_router(state: GraphState) -> dict[str, Any]:
    """Stub: passes state through."""
    return {}


def task_node(state: GraphState) -> dict[str, Any]:
    """Stub: passes state through."""
    return {}


def quality_gate_join(state: GraphState) -> dict[str, Any]:
    """Stub: passes state through (fan-out point for parallel reviews)."""
    return {}


def quality_gate_merge(state: GraphState) -> dict[str, Any]:
    """Stub: merge point after parallel reviews complete."""
    return {}


def code_review_node(state: GraphState) -> dict[str, Any]:
    """Stub: passes state through."""
    return {}


def security_audit_node(state: GraphState) -> dict[str, Any]:
    """Stub: passes state through."""
    return {}


def test_engineer_node(state: GraphState) -> dict[str, Any]:
    """Stub: passes state through."""
    return {}


def issue_orchestrator_node(state: GraphState) -> dict[str, Any]:
    """Stub: passes state through."""
    return {}


def ship_node(state: GraphState) -> dict[str, Any]:
    """Stub: transitions to SUCCEEDED."""
    return {"run_status": RunStatus.SUCCEEDED}
