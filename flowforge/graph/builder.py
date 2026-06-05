"""LangGraph graph builder with full topology.

Supports two modes:
- build_graph(): Uses stub nodes (for LangGraph Studio visualization / testing)
- build_real_graph(llm): Uses real LLM-powered nodes (for production execution)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langgraph.graph import END, START, StateGraph

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

from src.nodes.stubs import (
    clarification_node as stub_clarification,
    code_review_node as stub_code_review,
    issue_orchestrator_node as stub_issue_orchestrator,
    plan_node as stub_plan,
    quality_gate_join,
    quality_gate_merge,
    security_audit_node as stub_security_audit,
    ship_node as stub_ship,
    spec_node as stub_spec,
    task_fanout_router,
    task_node as stub_task,
    test_engineer_node as stub_test_engineer,
)
from src.state.models import GraphState


def build_graph() -> CompiledStateGraph:  # type: ignore[type-arg]
    """Build and compile the FlowForge pipeline graph (stub nodes for visualization).

    Topology (from spec):
        START -> clarification_node -> spec_node -> plan_node
              -> task_fanout_router -> task_node
              -> quality_gate_join -> code_review_node -> quality_gate_join
                                   -> security_audit_node -> quality_gate_join
                                   -> test_engineer_node -> quality_gate_join
              -> issue_orchestrator_node -> ship_node -> END
    """
    graph = StateGraph(GraphState)

    # Register all nodes (stubs)
    graph.add_node("clarification_node", stub_clarification)
    graph.add_node("spec_node", stub_spec)
    graph.add_node("plan_node", stub_plan)
    graph.add_node("task_fanout_router", task_fanout_router)
    graph.add_node("task_node", stub_task)
    graph.add_node("quality_gate_join", quality_gate_join)
    graph.add_node("code_review_node", stub_code_review)
    graph.add_node("security_audit_node", stub_security_audit)
    graph.add_node("test_engineer_node", stub_test_engineer)
    graph.add_node("quality_gate_merge", quality_gate_merge)
    graph.add_node("issue_orchestrator_node", stub_issue_orchestrator)
    graph.add_node("ship_node", stub_ship)

    # Linear path: START -> clarification -> spec -> plan
    graph.add_edge(START, "clarification_node")
    graph.add_edge("clarification_node", "spec_node")
    graph.add_edge("spec_node", "plan_node")

    # Plan -> task fanout -> task execution -> quality gate
    graph.add_edge("plan_node", "task_fanout_router")
    graph.add_edge("task_fanout_router", "task_node")
    graph.add_edge("task_node", "quality_gate_join")

    # Quality gate fans out to parallel review branches
    graph.add_edge("quality_gate_join", "code_review_node")
    graph.add_edge("quality_gate_join", "security_audit_node")
    graph.add_edge("quality_gate_join", "test_engineer_node")

    # All review branches merge before issue triage
    graph.add_edge("code_review_node", "quality_gate_merge")
    graph.add_edge("security_audit_node", "quality_gate_merge")
    graph.add_edge("test_engineer_node", "quality_gate_merge")

    # Issue triage -> ship -> END
    graph.add_edge("quality_gate_merge", "issue_orchestrator_node")
    graph.add_edge("issue_orchestrator_node", "ship_node")
    graph.add_edge("ship_node", END)

    return graph.compile()


def build_real_graph(llm: Any) -> CompiledStateGraph:  # type: ignore[type-arg]
    """Build graph with real LLM-powered nodes for production execution.

    Args:
        llm: An object implementing invoke(prompt) -> response with .content attribute.
    """
    from src.nodes.clarification import clarification_node
    from src.nodes.code_review import code_review_node
    from src.nodes.issue_orchestrator import issue_orchestrator_node
    from src.nodes.plan import plan_node
    from src.nodes.security_audit import security_audit_node
    from src.nodes.ship import ship_node
    from src.nodes.spec import spec_node

    # Create node wrappers that inject the LLM
    def real_clarification(state: GraphState) -> dict[str, Any]:
        return clarification_node(state, llm=llm)

    def real_spec(state: GraphState) -> dict[str, Any]:
        return spec_node(state, llm=llm)

    def real_plan(state: GraphState) -> dict[str, Any]:
        return plan_node(state, llm=llm)

    def real_code_review(state: GraphState) -> dict[str, Any]:
        return code_review_node(state, llm=llm)

    def real_security_audit(state: GraphState) -> dict[str, Any]:
        from src.nodes.security_audit import security_audit_node as sa_node

        return sa_node(state, llm=llm)

    def real_test_engineer(state: GraphState) -> dict[str, Any]:
        return {"test_findings": []}  # No test execution in current scope

    def real_issue_orchestrator(state: GraphState) -> dict[str, Any]:
        return issue_orchestrator_node(state, llm=llm)

    def real_ship(state: GraphState) -> dict[str, Any]:
        return ship_node(state, production_mode=False, llm=llm)

    graph = StateGraph(GraphState)

    graph.add_node("clarification_node", real_clarification)
    graph.add_node("spec_node", real_spec)
    graph.add_node("plan_node", real_plan)
    graph.add_node("task_fanout_router", task_fanout_router)
    graph.add_node("task_node", stub_task)  # Task execution handled by PipelineRunner
    graph.add_node("quality_gate_join", quality_gate_join)
    graph.add_node("code_review_node", real_code_review)
    graph.add_node("security_audit_node", real_security_audit)
    graph.add_node("test_engineer_node", real_test_engineer)
    graph.add_node("quality_gate_merge", quality_gate_merge)
    graph.add_node("issue_orchestrator_node", real_issue_orchestrator)
    graph.add_node("ship_node", real_ship)

    graph.add_edge(START, "clarification_node")
    graph.add_edge("clarification_node", "spec_node")
    graph.add_edge("spec_node", "plan_node")
    graph.add_edge("plan_node", "task_fanout_router")
    graph.add_edge("task_fanout_router", "task_node")
    graph.add_edge("task_node", "quality_gate_join")

    # Parallel fan-out: code review, security audit, test engineer run simultaneously
    graph.add_edge("quality_gate_join", "code_review_node")
    graph.add_edge("quality_gate_join", "security_audit_node")
    graph.add_edge("quality_gate_join", "test_engineer_node")

    # Merge after all parallel branches complete
    graph.add_edge("code_review_node", "quality_gate_merge")
    graph.add_edge("security_audit_node", "quality_gate_merge")
    graph.add_edge("test_engineer_node", "quality_gate_merge")

    graph.add_edge("quality_gate_merge", "issue_orchestrator_node")
    graph.add_edge("issue_orchestrator_node", "ship_node")
    graph.add_edge("ship_node", END)

    return graph.compile()


def build_live_graph() -> CompiledStateGraph:  # type: ignore[type-arg]
    """Build graph with LLM configured from environment variables.

    Used by `langgraph dev` so that LangGraph Studio at
    https://smith.langchain.com/studio can visualize live execution.

    Required env vars:
        OPENAI_API_KEY: API key (or gh auth token for GitHub Models)
        OPENAI_API_BASE: API base URL (default: https://models.inference.ai.azure.com)
        OPENAI_MODEL: Model name (default: gpt-4o-mini)
    """
    import os

    from langchain_openai import ChatOpenAI

    api_key = os.environ.get("OPENAI_API_KEY", "")
    api_base = os.environ.get(
        "OPENAI_API_BASE", "https://models.inference.ai.azure.com"
    )
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    if not api_key:
        # Try gh auth token as fallback
        import subprocess

        try:
            result = subprocess.run(
                ["gh", "auth", "token"], capture_output=True, text=True, check=True
            )
            api_key = result.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

    if not api_key:
        raise RuntimeError(
            "No API key found. Set OPENAI_API_KEY env var or ensure `gh auth token` works."
        )

    llm = ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=api_base,
        temperature=0.0,
        max_tokens=4096,
    )

    class _LLMWrapper:
        """Adapter to match the invoke(prompt) -> response.content interface."""

        def __init__(self, inner: ChatOpenAI) -> None:
            self._inner = inner

        def invoke(self, prompt: str) -> Any:
            return self._inner.invoke(prompt)

    return build_real_graph(_LLMWrapper(llm))
