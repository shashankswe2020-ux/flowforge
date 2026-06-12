"""LangGraph graph builder with full topology.

Supports two modes:
- build_graph(): Uses stub nodes (for LangGraph Studio visualization / testing)
- build_real_graph(llm): Uses real LLM-powered nodes (for production execution)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

from flowforge.nodes.stubs import (
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
from flowforge.scheduler.router import compute_next_runnable
from flowforge.state.models import GraphState


def _route_runnable_tasks(state: GraphState) -> list[Send] | str:
    """Conditional router that emits one ``Send`` per ready task.

    Reads the implementation plan's DAG, identifies tasks whose
    predecessors have all reached a terminal state, and emits a ``Send``
    payload per ready task so they each run as a parallel ``task_node``
    invocation. When no tasks remain runnable (all done, or none could
    progress), routes to the quality gate.

    Returns either a list of :class:`langgraph.types.Send` (parallel
    fan-out) or the string name of the next node when there is nothing
    left to dispatch — see LangGraph conditional-edge semantics.
    """
    plan = state.implementation_plan
    if plan is None or not plan.dag.tasks:
        return "quality_gate_join"
    runnable = compute_next_runnable(plan.dag, state.tasks)
    if not runnable:
        return "quality_gate_join"
    base_payload = state.model_dump()
    return [
        Send("task_node", {**base_payload, "current_task_id": tid})
        for tid in runnable
    ]


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

    # Plan -> task fanout -> task execution -> quality gate.
    # task_fanout_router is a conditional edge that emits one Send per
    # runnable task (Option A — dynamic DAG dispatch). task_node loops
    # back into the router after each task so independent tasks run in
    # parallel and dependents wait for their predecessors. When no
    # tasks remain runnable, the router proceeds to the quality gate.
    graph.add_edge("plan_node", "task_fanout_router")
    graph.add_conditional_edges(
        "task_fanout_router",
        _route_runnable_tasks,
        {"quality_gate_join": "quality_gate_join", "task_node": "task_node"},
    )
    graph.add_edge("task_node", "task_fanout_router")

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
    from flowforge.nodes.clarification import clarification_node
    from flowforge.nodes.code_review import code_review_node
    from flowforge.nodes.issue_orchestrator import issue_orchestrator_node
    from flowforge.nodes.plan import plan_node
    from flowforge.nodes.security_audit import security_audit_node
    from flowforge.nodes.ship import ship_node
    from flowforge.nodes.spec import spec_node
    from flowforge.nodes.task_runner import task_node as real_task_node
    from flowforge.nodes.test_engineer import test_engineer_node

    # Create node wrappers that inject the LLM
    def real_clarification(state: GraphState) -> dict[str, Any]:
        return clarification_node(state, llm=llm)

    def real_spec(state: GraphState) -> dict[str, Any]:
        return spec_node(state, llm=llm)

    def real_plan(state: GraphState) -> dict[str, Any]:
        return plan_node(state, llm=llm)

    def real_task(state: GraphState) -> dict[str, Any]:
        return real_task_node(state, llm=llm)

    def real_code_review(state: GraphState) -> dict[str, Any]:
        return code_review_node(state, llm=llm)

    def real_security_audit(state: GraphState) -> dict[str, Any]:
        from flowforge.nodes.security_audit import security_audit_node as sa_node

        return sa_node(state, llm=llm)

    def real_test_engineer(state: GraphState) -> dict[str, Any]:
        return test_engineer_node(state, llm=llm)

    def real_issue_orchestrator(state: GraphState) -> dict[str, Any]:
        return issue_orchestrator_node(state, llm=llm)

    def real_ship(state: GraphState) -> dict[str, Any]:
        return ship_node(state, production_mode=False, llm=llm)

    graph = StateGraph(GraphState)

    graph.add_node("clarification_node", real_clarification)
    graph.add_node("spec_node", real_spec)
    graph.add_node("plan_node", real_plan)
    graph.add_node("task_fanout_router", task_fanout_router)
    graph.add_node("task_node", real_task)
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
    graph.add_conditional_edges(
        "task_fanout_router",
        _route_runnable_tasks,
        {"quality_gate_join": "quality_gate_join", "task_node": "task_node"},
    )
    graph.add_edge("task_node", "task_fanout_router")

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


_COPILOT_TOKEN_CACHE: dict[str, tuple[str, float]] = {}


def _exchange_for_copilot_token(oauth_token: str) -> str:
    """Trade a Copilot OAuth token for a short-lived session token.

    Thin wrapper around :func:`flowforge.auth.copilot.get_session_token`
    so callers don't need to import the auth module directly.
    """
    from flowforge.auth.copilot import get_session_token

    return get_session_token(oauth_token)


def _non_reasoning_max_tokens(model: str) -> int:
    """Output-token budget for non-reasoning chat models.

    Spec/plan nodes emit large JSON payloads. At 4096 tokens these truncated
    mid-string and crashed downstream ``json.loads`` with "Unterminated string".
    Claude models (esp. Opus) are far more verbose and can exceed 16k tokens of
    JSON; they support a 32k output cap. gpt-4o-class models cap at 16384 and
    reject larger requests.
    """
    return 32768 if "claude" in model.lower() else 16384


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

    # gpt-5 / o-series reject `max_tokens` and require
    # `max_completion_tokens`; older chat models accept either via the
    # OpenAI-compat surface but many third-party gateways only accept
    # the legacy name. Pick the one this model expects.
    is_reasoning_family = any(
        tag in model.lower() for tag in ("gpt-5", "/o1", "/o3", "/o4")
    )
    chat_kwargs: dict[str, Any] = {
        "model": model,
        "api_key": api_key,
        "base_url": api_base,
    }
    if "api.githubcopilot.com" in api_base:
        # GitHub Copilot's chat endpoint requires (a) a short-lived
        # Copilot session token exchanged from the user's Copilot
        # OAuth token, and (b) editor-identification headers.
        copilot_token = _exchange_for_copilot_token(api_key)
        chat_kwargs["api_key"] = copilot_token
        chat_kwargs["default_headers"] = {
            "Editor-Version": "vscode/1.95.0",
            "Editor-Plugin-Version": "copilot-chat/0.20.0",
            "Copilot-Integration-Id": "vscode-chat",
        }
        # Copilot's API uses bare model ids ("gpt-4o", not "openai/gpt-4o").
        if model.startswith("openai/"):
            chat_kwargs["model"] = model.split("/", 1)[1]
    if is_reasoning_family:
        # Reasoning-family models reject temperature overrides and
        # require `max_completion_tokens`; budget is generous so the
        # internal reasoning step has room before content tokens.
        chat_kwargs["model_kwargs"] = {"max_completion_tokens": 16384}
    else:
        chat_kwargs["temperature"] = 0.0
        chat_kwargs["max_tokens"] = _non_reasoning_max_tokens(model)

    llm = ChatOpenAI(**chat_kwargs)

    class _LLMWrapper:
        """Adapter to match the invoke(prompt) -> response.content interface."""

        def __init__(self, inner: ChatOpenAI) -> None:
            self._inner = inner

        def invoke(self, prompt: str) -> Any:
            return self._inner.invoke(prompt)

    from flowforge.config.deep_agents import resolve_deep_agents_enabled

    if resolve_deep_agents_enabled():
        # Deep agents need a real BaseChatModel (deepagents calls
        # methods beyond .invoke); the wrapper is only suitable for
        # legacy nodes that exercise the minimal invoke(str) contract.
        return build_deep_agent_graph(llm)
    return build_real_graph(_LLMWrapper(llm))


def build_deep_agent_graph(llm: Any) -> CompiledStateGraph:  # type: ignore[type-arg]  # noqa: ANN401
    """Build the Deep Agents variant of the FlowForge pipeline.

    During Phase 0 of the Deep Agents enhancement (T11) no agentic
    node has a Deep Agent variant yet (those land in T7–T9). This
    function therefore returns the same graph as
    :func:`build_real_graph`. As wrappers come online, individual
    nodes will switch on the
    :envvar:`FLOWFORGE_DEEP_AGENTS` flag internally and consume their
    role-bound Deep Agent.
    """
    return build_real_graph(llm)
