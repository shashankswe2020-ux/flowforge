"""Deep-agent path tests for ``clarification_node`` (T8)."""

from __future__ import annotations

import json
from pathlib import Path  # noqa: TC003
from typing import Any

import pytest

from flowforge.deep_agents import AgentRole
from flowforge.nodes import clarification as clar_module
from flowforge.nodes.clarification import REQUIRED_DIMENSIONS, clarification_node
from flowforge.state.models import (
    AmbiguityStatus,
    ClarificationTranscript,
    DeepAgentTrace,
    GraphState,
    RunStatus,
)
from tests.mocks import MockLLM


def _state(workdir: str, *, auto_clarify: bool = True) -> GraphState:
    return GraphState(
        request="Build a TypeScript MCP server for the WHOOP API.",
        run_status=RunStatus.RUNNING,
        auto_clarify=auto_clarify,
        ambiguity_status=AmbiguityStatus(
            score=1.0,
            unresolved_dimensions=list(REQUIRED_DIMENSIONS),
            deferred_dimensions=[],
            is_complete=False,
        ),
        clarification_transcript=ClarificationTranscript(exchanges=[]),
        workdir=workdir,
    )


def _canned_clarified_payload() -> dict[str, str]:
    return {
        "solution_type": "library",
        "scope_size": "medium",
        "target_users": "AI developers",
        "delivery_boundaries": "Out: hosting infrastructure",
        "constraints": "Node 18+, native fetch",
        "success_criteria": "Tools accessible from Claude Desktop",
        "summary": "Build an MCP server wrapping the WHOOP REST API.",
    }


def _canned_result(payload: dict[str, str] | None = None) -> dict[str, object]:
    body = json.dumps(payload if payload is not None else _canned_clarified_payload())
    return {
        "messages": [{"role": "user", "content": "clarify"}],
        "files": {"vfs:/context/clarified_request_output.json": body},
    }


@pytest.fixture
def deep_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWFORGE_DEEP_AGENTS", "1")


@pytest.fixture
def patched_deep_agent(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {"build_calls": [], "run_calls": []}

    def fake_build(*args: object, **kwargs: object) -> object:
        captured["build_calls"].append({"args": args, "kwargs": kwargs})
        return object()

    def fake_run(graph: object, payload: dict[str, object], **kwargs: object) -> dict[str, object]:
        captured["run_calls"].append({"graph": graph, "payload": payload, "kwargs": kwargs})
        return _canned_result()

    monkeypatch.setattr(clar_module, "build_deep_agent", fake_build)
    monkeypatch.setattr(clar_module, "run_deep_agent_bounded", fake_run)
    return captured


class TestDeepAgentDispatch:
    def test_flag_on_dispatches_with_clarifier_role(
        self,
        tmp_path: Path,
        deep_flag_on: None,
        patched_deep_agent: dict[str, Any],
    ) -> None:
        del deep_flag_on
        llm = MockLLM(responses=["unused"])
        state = _state(str(tmp_path))

        result = clarification_node(state, llm=llm)

        assert len(patched_deep_agent["build_calls"]) == 1
        kwargs = patched_deep_agent["build_calls"][0]["kwargs"]
        assert kwargs.get("role") is AgentRole.CLARIFIER
        assert result["clarified_request"] is not None

    def test_flag_on_populates_clarified_request(
        self,
        tmp_path: Path,
        deep_flag_on: None,
        patched_deep_agent: dict[str, Any],
    ) -> None:
        del deep_flag_on, patched_deep_agent
        llm = MockLLM(responses=["unused"])
        state = _state(str(tmp_path))

        result = clarification_node(state, llm=llm)

        clarified = result["clarified_request"]
        assert clarified is not None
        assert clarified.solution_type == "library"
        assert clarified.scope_size == "medium"
        assert "WHOOP" in clarified.summary
        assert result["run_status"] is RunStatus.RUNNING
        assert result["ambiguity_status"].is_complete is True

    def test_flag_on_populates_trace_and_invocations(
        self,
        tmp_path: Path,
        deep_flag_on: None,
        patched_deep_agent: dict[str, Any],
    ) -> None:
        del deep_flag_on, patched_deep_agent
        llm = MockLLM(responses=["unused"])
        state = _state(str(tmp_path))

        result = clarification_node(state, llm=llm)

        trace = result["deep_agent_traces"]["clarification_node"]
        assert isinstance(trace, DeepAgentTrace)
        assert trace.role is AgentRole.CLARIFIER
        assert trace.tool_invocations == []  # patched runner emits none
        assert "vfs:/context/clarified_request_output.json" in trace.vfs_keys

    def test_flag_off_uses_legacy_path(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FLOWFORGE_DEEP_AGENTS", "0")
        called: dict[str, bool] = {"build": False}

        def boom(*_a: object, **_k: object) -> None:
            called["build"] = True

        monkeypatch.setattr(clar_module, "build_deep_agent", boom)
        legacy = json.dumps(_canned_clarified_payload())
        llm = MockLLM(responses=[legacy])
        state = _state(str(tmp_path))

        result = clarification_node(state, llm=llm)

        assert called["build"] is False
        assert result["clarified_request"] is not None
        assert "deep_agent_traces" not in result

    def test_auto_clarify_false_bypasses_deep_path(
        self,
        tmp_path: Path,
        deep_flag_on: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        del deep_flag_on
        called: dict[str, bool] = {"build": False}

        def boom(*_a: object, **_k: object) -> None:
            called["build"] = True

        monkeypatch.setattr(clar_module, "build_deep_agent", boom)
        # Interactive mode: ask-a-question response shape
        question_resp = json.dumps(
            {"question": "What scope?", "dimension": "scope_size"},
        )
        llm = MockLLM(responses=[question_resp])
        state = _state(str(tmp_path), auto_clarify=False)

        result = clarification_node(state, llm=llm)

        assert called["build"] is False
        assert result["run_status"] is RunStatus.WAITING_FOR_INPUT

    def test_malformed_vfs_falls_back_to_legacy(
        self,
        tmp_path: Path,
        deep_flag_on: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        del deep_flag_on
        monkeypatch.setattr(clar_module, "build_deep_agent", lambda *a, **k: object())
        monkeypatch.setattr(
            clar_module,
            "run_deep_agent_bounded",
            lambda *a, **k: {"messages": [], "files": {}},  # no clarified file
        )
        legacy = json.dumps(_canned_clarified_payload())
        llm = MockLLM(responses=[legacy])
        state = _state(str(tmp_path))

        result = clarification_node(state, llm=llm)

        # Falls back to legacy, no traces emitted
        assert result["clarified_request"] is not None
        assert "deep_agent_traces" not in result
