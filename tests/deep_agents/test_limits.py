"""Tests for T10: limits — recursion, timeout, tool budget.

Covers the spec §10 item 6 contract:

* :class:`RecursionLimitExceededError`,
  :class:`AgentTimeoutError`, and :class:`ToolBudgetExceededError`
  are raised by :func:`run_deep_agent_bounded` and carry ``role``,
  ``node_name``, and a ``partial_trace`` :class:`DeepAgentTrace`.
* Recursion limit honoured via ``FLOWFORGE_DEEP_AGENT_RECURSION``
  (already covered in ``test_factory.py``); the typed wrapper here
  converts ``langgraph.errors.GraphRecursionError`` to
  :class:`RecursionLimitExceededError`.
* Wall-clock timeout from ``FLOWFORGE_DEEP_AGENT_TIMEOUT_S``
  (default 300) terminates the run with :class:`AgentTimeoutError`.
* Tool budget cap (default 200; env
  ``FLOWFORGE_DEEP_AGENT_TOOL_BUDGET``) raises
  :class:`ToolBudgetExceededError` on the (N+1)-th tool call.
"""

from __future__ import annotations

import json
import time

import pytest
from langgraph.errors import GraphRecursionError

from flowforge.deep_agents import AgentRole
from flowforge.deep_agents.errors import (
    AgentTimeoutError,
    DeepAgentLimitError,
    RecursionLimitExceededError,
    ToolBudgetExceededError,
)
from flowforge.deep_agents.factory import (
    _BUDGET_VAR,
    DEFAULT_TIMEOUT_S,
    DEFAULT_TOOL_BUDGET,
    _consume_tool_budget,
    _extract_subagent_dispatches,
    _resolve_timeout_s,
    _resolve_tool_budget,
    _RunBudget,
    run_deep_agent_bounded,
)
from flowforge.state.models import DeepAgentTrace, ToolInvocationRecord

# ---------------------------------------------------------------------------
# Resolution of env knobs
# ---------------------------------------------------------------------------


class TestResolveTimeout:
    def test_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FLOWFORGE_DEEP_AGENT_TIMEOUT_S", raising=False)
        assert _resolve_timeout_s() == DEFAULT_TIMEOUT_S == 300

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FLOWFORGE_DEEP_AGENT_TIMEOUT_S", "42")
        assert _resolve_timeout_s() == 42

    def test_invalid_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FLOWFORGE_DEEP_AGENT_TIMEOUT_S", "0")
        with pytest.raises(ValueError, match="positive"):
            _resolve_timeout_s()


class TestResolveToolBudget:
    def test_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FLOWFORGE_DEEP_AGENT_TOOL_BUDGET", raising=False)
        assert _resolve_tool_budget() == DEFAULT_TOOL_BUDGET == 200

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FLOWFORGE_DEEP_AGENT_TOOL_BUDGET", "5")
        assert _resolve_tool_budget() == 5

    def test_invalid_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FLOWFORGE_DEEP_AGENT_TOOL_BUDGET", "-1")
        with pytest.raises(ValueError, match="positive"):
            _resolve_tool_budget()


# ---------------------------------------------------------------------------
# _consume_tool_budget
# ---------------------------------------------------------------------------


def _budget(
    *,
    deadline: float | None = None,
    remaining: int = 10,
) -> _RunBudget:
    return _RunBudget(
        role=AgentRole.REVIEWER,
        node_name="code_review_node",
        deadline=deadline if deadline is not None else time.monotonic() + 60.0,
        remaining_calls=remaining,
    )


class TestConsumeToolBudget:
    def test_no_op_outside_run(self) -> None:
        # No budget context set — must not raise.
        _consume_tool_budget("run_tests")

    def test_decrements_counter(self) -> None:
        budget = _budget(remaining=3)
        token = _BUDGET_VAR.set(budget)
        try:
            _consume_tool_budget("run_tests")
            _consume_tool_budget("run_lint")
            assert budget.remaining_calls == 1
            assert [r.tool for r in budget.invocations] == ["run_tests", "run_lint"]
        finally:
            _BUDGET_VAR.reset(token)

    def test_raises_when_budget_exhausted(self) -> None:
        budget = _budget(remaining=0)
        token = _BUDGET_VAR.set(budget)
        try:
            with pytest.raises(ToolBudgetExceededError) as exc_info:
                _consume_tool_budget("run_tests")
        finally:
            _BUDGET_VAR.reset(token)
        err = exc_info.value
        assert err.role is AgentRole.REVIEWER
        assert err.node_name == "code_review_node"
        assert isinstance(err.partial_trace, DeepAgentTrace)

    def test_raises_when_deadline_passed(self) -> None:
        budget = _budget(deadline=time.monotonic() - 1.0)
        token = _BUDGET_VAR.set(budget)
        try:
            with pytest.raises(AgentTimeoutError) as exc_info:
                _consume_tool_budget("run_tests")
        finally:
            _BUDGET_VAR.reset(token)
        assert exc_info.value.role is AgentRole.REVIEWER
        assert isinstance(exc_info.value.partial_trace, DeepAgentTrace)

    def test_inheritance(self) -> None:
        for cls in (
            RecursionLimitExceededError,
            AgentTimeoutError,
            ToolBudgetExceededError,
        ):
            assert issubclass(cls, DeepAgentLimitError)


# ---------------------------------------------------------------------------
# run_deep_agent_bounded
# ---------------------------------------------------------------------------


class _FakeGraph:
    """Minimal graph stub exposing ``invoke``."""

    def __init__(self, fn: object) -> None:
        self._fn = fn

    def invoke(self, payload: dict[str, object]) -> dict[str, object]:
        result = self._fn(payload)  # type: ignore[operator]
        if not isinstance(result, dict):
            raise TypeError("fake graph must return a dict")
        return result


class TestRunDeepAgentBounded:
    def test_returns_invoke_result(self) -> None:
        graph = _FakeGraph(lambda payload: {"echo": payload["x"]})
        out = run_deep_agent_bounded(
            graph,  # type: ignore[arg-type]
            {"x": 1},
            role=AgentRole.REVIEWER,
            node_name="code_review_node",
            timeout_s=5,
            tool_budget=10,
        )
        assert out == {"echo": 1}

    def test_timeout_raises_typed_error(self) -> None:
        def slow(_payload: dict[str, object]) -> dict[str, object]:
            time.sleep(0.5)
            return {}

        graph = _FakeGraph(slow)
        with pytest.raises(AgentTimeoutError) as exc_info:
            run_deep_agent_bounded(
                graph,  # type: ignore[arg-type]
                {},
                role=AgentRole.REVIEWER,
                node_name="code_review_node",
                timeout_s=0.05,
                tool_budget=10,
            )
        err = exc_info.value
        assert err.role is AgentRole.REVIEWER
        assert err.node_name == "code_review_node"
        assert isinstance(err.partial_trace, DeepAgentTrace)

    def test_tool_budget_propagates(self) -> None:
        def burner(_payload: dict[str, object]) -> dict[str, object]:
            for _ in range(10):
                _consume_tool_budget("run_tests")
            return {}

        graph = _FakeGraph(burner)
        with pytest.raises(ToolBudgetExceededError) as exc_info:
            run_deep_agent_bounded(
                graph,  # type: ignore[arg-type]
                {},
                role=AgentRole.IMPLEMENTER,
                node_name="task_node",
                timeout_s=5,
                tool_budget=3,
            )
        err = exc_info.value
        assert err.role is AgentRole.IMPLEMENTER
        assert err.node_name == "task_node"
        # Three successful invocations recorded before the (4th) failure.
        assert len(err.partial_trace.tool_invocations) == 3

    def test_recursion_error_converted(self) -> None:
        def boom(_payload: dict[str, object]) -> dict[str, object]:
            raise GraphRecursionError("recursion limit reached")

        graph = _FakeGraph(boom)
        with pytest.raises(RecursionLimitExceededError) as exc_info:
            run_deep_agent_bounded(
                graph,  # type: ignore[arg-type]
                {},
                role=AgentRole.PLANNER,
                node_name="plan_node",
                timeout_s=5,
                tool_budget=10,
            )
        assert exc_info.value.role is AgentRole.PLANNER
        assert exc_info.value.node_name == "plan_node"

    def test_unrelated_error_propagates_unwrapped(self) -> None:
        def boom(_payload: dict[str, object]) -> dict[str, object]:
            raise RuntimeError("unrelated")

        graph = _FakeGraph(boom)
        with pytest.raises(RuntimeError, match="unrelated"):
            run_deep_agent_bounded(
                graph,  # type: ignore[arg-type]
                {},
                role=AgentRole.REVIEWER,
                node_name="n",
                timeout_s=5,
                tool_budget=10,
            )

    def test_malformed_tool_call_json_falls_back_to_empty_result(self) -> None:
        """A truncated/malformed tool-call argument must not abort the node.

        The underlying model can emit a tool call whose JSON arguments are
        truncated; the deepagents/LangChain parser then raises
        ``json.JSONDecodeError`` from inside ``graph.invoke``. Rather than
        crash the whole pipeline, ``run_deep_agent_bounded`` returns an
        empty structured result so the node's ``_extract_*`` sees no
        artifact and falls back to its legacy single-shot path.
        """

        def malformed(_payload: dict[str, object]) -> dict[str, object]:
            # Reproduce the exact failure: parsing a truncated JSON string.
            json.loads('{"content": "unterminated')
            return {}  # pragma: no cover - never reached

        graph = _FakeGraph(malformed)
        sink: list[ToolInvocationRecord] = []
        out = run_deep_agent_bounded(
            graph,  # type: ignore[arg-type]
            {},
            role=AgentRole.CLARIFIER,
            node_name="clarification_node",
            timeout_s=5,
            tool_budget=10,
            invocation_sink=sink,
        )
        # Empty structured output -> node falls back to legacy.
        assert out == {"messages": [], "files": {}}

    def test_clears_budget_var_after_run(self) -> None:
        graph = _FakeGraph(lambda _p: {})
        assert _BUDGET_VAR.get() is None
        run_deep_agent_bounded(
            graph,  # type: ignore[arg-type]
            {},
            role=AgentRole.REVIEWER,
            node_name="n",
            timeout_s=5,
            tool_budget=10,
        )
        assert _BUDGET_VAR.get() is None

    def test_timeout_does_not_block_on_wedged_worker(self) -> None:
        """Audit HIGH-2: timeout must propagate even when worker is wedged.

        Previously the executor was used as a context manager, whose
        ``__exit__`` calls ``shutdown(wait=True)`` and so blocked the
        caller until the runaway worker finished. The wall-clock cap
        promised by spec §10 was therefore non-binding.
        """

        def wedged(_payload: dict[str, object]) -> dict[str, object]:
            time.sleep(5.0)
            return {}

        graph = _FakeGraph(wedged)
        started = time.monotonic()
        with pytest.raises(AgentTimeoutError):
            run_deep_agent_bounded(
                graph,  # type: ignore[arg-type]
                {},
                role=AgentRole.REVIEWER,
                node_name="code_review_node",
                timeout_s=0.1,
                tool_budget=10,
            )
        elapsed = time.monotonic() - started
        # Generous cap — fix should return well under 1s; the unfixed
        # behaviour would block ~5s waiting on ``shutdown(wait=True)``.
        assert elapsed < 1.5, (
            f"AgentTimeoutError took {elapsed:.2f}s to surface "
            "(expected < 1.5s; ThreadPoolExecutor.shutdown likely blocking)"
        )


# ---------------------------------------------------------------------------
# Sub-agent dispatch extraction (T8)
# ---------------------------------------------------------------------------


class TestExtractSubagentDispatches:
    """Cover ``_extract_subagent_dispatches`` independent of the runtime."""

    def test_returns_empty_for_non_list(self) -> None:
        assert _extract_subagent_dispatches(None) == []
        assert _extract_subagent_dispatches({}) == []
        assert _extract_subagent_dispatches("not a list") == []

    def test_extracts_dict_message_task_call(self) -> None:
        messages = [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "tc1",
                        "name": "task",
                        "args": {"subagent_type": "researcher"},
                    },
                ],
            },
        ]
        records = _extract_subagent_dispatches(messages)
        assert len(records) == 1
        assert records[0].tool == "task"
        assert records[0].parent == "researcher"
        assert records[0].ok is True

    def test_extracts_object_message_task_call(self) -> None:
        class _Msg:
            tool_calls = [
                {"name": "task", "args": {"subagent": "estimator"}},
            ]

        records = _extract_subagent_dispatches([_Msg()])
        assert len(records) == 1
        assert records[0].parent == "estimator"

    def test_falls_back_to_arguments_key(self) -> None:
        messages = [
            {
                "tool_calls": [
                    {"name": "task", "arguments": {"name": "dedupe_helper"}},
                ],
            },
        ]
        records = _extract_subagent_dispatches(messages)
        assert len(records) == 1
        assert records[0].parent == "dedupe_helper"

    def test_skips_non_task_calls(self) -> None:
        messages = [
            {"tool_calls": [{"name": "run_tests", "args": {}}]},
        ]
        assert _extract_subagent_dispatches(messages) == []

    def test_records_none_parent_when_subagent_missing(self) -> None:
        messages = [
            {"tool_calls": [{"name": "task", "args": {}}]},
        ]
        records = _extract_subagent_dispatches(messages)
        assert len(records) == 1
        assert records[0].parent is None


class TestRunDeepAgentInvocationSink:
    """End-to-end invocation_sink covers tool calls + sub-agent dispatches."""

    def test_sink_extends_with_consumed_tools_and_task_dispatches(self) -> None:
        def fn(_payload: dict[str, object]) -> dict[str, object]:
            _consume_tool_budget("run_tests")
            return {
                "messages": [
                    {
                        "tool_calls": [
                            {
                                "name": "task",
                                "args": {"subagent_type": "researcher"},
                            },
                        ],
                    },
                ],
            }

        graph = _FakeGraph(fn)
        sink: list[ToolInvocationRecord] = []
        run_deep_agent_bounded(
            graph,  # type: ignore[arg-type]
            {},
            role=AgentRole.REVIEWER,
            node_name="code_review_node",
            timeout_s=5,
            tool_budget=10,
            invocation_sink=sink,
        )
        tools = [r.tool for r in sink]
        parents = [r.parent for r in sink]
        assert "run_tests" in tools
        assert "task" in tools
        assert "researcher" in parents
