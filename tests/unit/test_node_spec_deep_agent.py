"""Deep-agent path tests for ``spec_node`` (T8)."""

from __future__ import annotations

import json
from pathlib import Path  # noqa: TC003
from typing import Any

import pytest

from flowforge.deep_agents import AgentRole
from flowforge.nodes import spec as spec_module
from flowforge.nodes.spec import spec_node
from flowforge.state.models import (
    AmbiguityStatus,
    ClarifiedRequest,
    DeepAgentTrace,
    GraphState,
    RunStatus,
)
from tests.mocks import MockLLM


def _state(workdir: str) -> GraphState:
    clarified = ClarifiedRequest(
        solution_type="library",
        scope_size="medium",
        target_users="AI developers",
        must_have=["MCP tools"],
        nice_to_have=[],
        constraints=["Node 18+"],
        success_criteria=["Tools accessible"],
        tech_preferences=["TypeScript"],
        summary="Build an MCP server wrapping the WHOOP REST API.",
    )
    return GraphState(
        request="Build a TypeScript MCP server for the WHOOP API.",
        run_status=RunStatus.RUNNING,
        clarified_request=clarified,
        ambiguity_status=AmbiguityStatus(
            score=0.0,
            unresolved_dimensions=[],
            deferred_dimensions=[],
            is_complete=True,
        ),
        workdir=workdir,
    )


def _canned_spec_payload() -> dict[str, Any]:
    return {
        "artifact_path": "docs/spec/whoop-mcp.md",
        "summary": "MCP server for WHOOP API.",
        "objective": "Provide MCP tools for AI assistants.",
        "target_users": "AI developers",
        "tech_stack": ["TypeScript ~5.x", "Node >= 18"],
        "commands": {"build": "npm run build", "test": "npm test"},
        "project_structure": ["src/", "tests/"],
        "code_style": ["No any", "Named exports only"],
        "acceptance_criteria": [
            "All 10 tools implemented",
            "Tests pass with >70% coverage",
        ],
        "assumptions": ["WHOOP API access granted"],
        "open_questions": [],
        "security_considerations": ["Tokens stored at 0600"],
        "testing_strategy": ["Unit > integration > e2e"],
        "boundaries": {
            "always": ["Validate input"],
            "ask_first": ["New deps"],
            "never": ["Hit real API in tests"],
        },
    }


def _canned_result(payload: dict[str, Any] | None = None) -> dict[str, object]:
    body = json.dumps(payload if payload is not None else _canned_spec_payload())
    return {
        "messages": [{"role": "user", "content": "spec"}],
        "files": {"vfs:/context/spec_output.json": body},
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

    monkeypatch.setattr(spec_module, "build_deep_agent", fake_build)
    monkeypatch.setattr(spec_module, "run_deep_agent_bounded", fake_run)
    monkeypatch.setattr(spec_module, "_commit_spec_to_repo", lambda *a, **k: None)
    return captured


class TestDeepAgentDispatch:
    def test_flag_on_dispatches_with_spec_author_role(
        self,
        tmp_path: Path,
        deep_flag_on: None,
        patched_deep_agent: dict[str, Any],
    ) -> None:
        del deep_flag_on
        llm = MockLLM(responses=["unused"])
        state = _state(str(tmp_path))

        result = spec_node(state, llm=llm)

        assert len(patched_deep_agent["build_calls"]) == 1
        kwargs = patched_deep_agent["build_calls"][0]["kwargs"]
        assert kwargs.get("role") is AgentRole.SPEC_AUTHOR
        assert "spec" in result

    def test_flag_on_parses_spec_output(
        self,
        tmp_path: Path,
        deep_flag_on: None,
        patched_deep_agent: dict[str, Any],
    ) -> None:
        del deep_flag_on, patched_deep_agent
        llm = MockLLM(responses=["unused"])
        state = _state(str(tmp_path))

        result = spec_node(state, llm=llm)

        spec = result["spec"]
        assert spec.artifact_path == "docs/spec/whoop-mcp.md"
        assert "All 10 tools implemented" in spec.acceptance_criteria
        assert spec.commands["build"] == "npm run build"
        assert result["run_status"] is RunStatus.RUNNING

    def test_flag_on_calls_commit_helper(
        self,
        tmp_path: Path,
        deep_flag_on: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        del deep_flag_on
        commit_calls: list[Any] = []
        monkeypatch.setattr(
            spec_module,
            "build_deep_agent",
            lambda *a, **k: object(),
        )
        monkeypatch.setattr(
            spec_module,
            "run_deep_agent_bounded",
            lambda *a, **k: _canned_result(),
        )
        monkeypatch.setattr(
            spec_module,
            "_commit_spec_to_repo",
            lambda spec, state: commit_calls.append((spec, state)),
        )

        llm = MockLLM(responses=["unused"])
        state = _state(str(tmp_path))

        spec_node(state, llm=llm)

        assert len(commit_calls) == 1
        spec, _ = commit_calls[0]
        assert spec.artifact_path == "docs/spec/whoop-mcp.md"

    def test_flag_on_populates_trace(
        self,
        tmp_path: Path,
        deep_flag_on: None,
        patched_deep_agent: dict[str, Any],
    ) -> None:
        del deep_flag_on, patched_deep_agent
        llm = MockLLM(responses=["unused"])
        state = _state(str(tmp_path))

        result = spec_node(state, llm=llm)

        trace = result["deep_agent_traces"]["spec_node"]
        assert isinstance(trace, DeepAgentTrace)
        assert trace.role is AgentRole.SPEC_AUTHOR
        assert trace.tool_invocations == []
        assert "vfs:/context/spec_output.json" in trace.vfs_keys

    def test_flag_off_uses_legacy_path(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FLOWFORGE_DEEP_AGENTS", "0")
        called: dict[str, bool] = {"build": False}

        def boom(*_a: object, **_k: object) -> None:
            called["build"] = True

        monkeypatch.setattr(spec_module, "build_deep_agent", boom)
        monkeypatch.setattr(spec_module, "_commit_spec_to_repo", lambda *a, **k: None)

        legacy = json.dumps(_canned_spec_payload())
        llm = MockLLM(responses=[legacy])
        state = _state(str(tmp_path))

        result = spec_node(state, llm=llm)

        assert called["build"] is False
        assert "spec" in result
        assert "deep_agent_traces" not in result
