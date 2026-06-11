"""Deep-agent path tests for ``issue_orchestrator_node`` (T8)."""

from __future__ import annotations

import json
from pathlib import Path  # noqa: TC003
from typing import Any

import pytest

from flowforge.deep_agents import AgentRole
from flowforge.nodes import issue_orchestrator as iorch_module
from flowforge.nodes.issue_orchestrator import (
    _parse_issue_items,
    compute_fingerprint,
    issue_orchestrator_node,
)
from flowforge.state.models import (
    DeepAgentTrace,
    Finding,
    GraphState,
    Issue,
    IssueDisposition,
    IssueSeverity,
    RunStatus,
)
from tests.mocks import MockLLM


def _finding() -> Finding:
    return Finding(
        finding_id="rev-1",
        source_node="code_review_node",
        severity=IssueSeverity.HIGH,
        confidence=0.9,
        title="Missing input validation on /login",
        description="Login endpoint accepts unsanitised input.",
        file_path="src/routes/login.py",
        line_range=(10, 25),
        suggestion="Add Zod-equivalent validation.",
    )


def _state(workdir: str, finding: Finding) -> GraphState:
    return GraphState(
        request="Build login service.",
        run_status=RunStatus.RUNNING,
        review_findings=[finding],
        workdir=workdir,
    )


def _canned_result(fingerprint: str) -> dict[str, object]:
    body = json.dumps(
        {
            "issues": [
                {
                    "fingerprint": fingerprint,
                    "disposition": "must_fix_before_ship",
                    "remediation": "Add validation middleware.",
                    "owner": "code_review_node",
                    "sla_target": "24h",
                },
            ],
        },
    )
    return {
        "messages": [{"role": "user", "content": "triage"}],
        "files": {"vfs:/context/issues_output.json": body},
    }


@pytest.fixture
def deep_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWFORGE_DEEP_AGENTS", "1")


@pytest.fixture
def patched_deep_agent(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {"build_calls": [], "run_calls": [], "fingerprint": None}

    def fake_build(*args: object, **kwargs: object) -> object:
        captured["build_calls"].append({"args": args, "kwargs": kwargs})
        return object()

    def fake_run(graph: object, payload: dict[str, object], **kwargs: object) -> dict[str, object]:
        captured["run_calls"].append({"graph": graph, "payload": payload, "kwargs": kwargs})
        return _canned_result(captured["fingerprint"])

    monkeypatch.setattr(iorch_module, "build_deep_agent", fake_build)
    monkeypatch.setattr(iorch_module, "run_deep_agent_bounded", fake_run)
    monkeypatch.setattr(iorch_module, "_commit_triage_to_repo", lambda *a, **k: None)
    monkeypatch.setattr(iorch_module, "_create_github_issues", lambda *a, **k: None)
    return captured


class TestDeepAgentDispatch:
    def test_flag_on_dispatches_with_triager_role(
        self,
        tmp_path: Path,
        deep_flag_on: None,
        patched_deep_agent: dict[str, Any],
    ) -> None:
        del deep_flag_on
        finding = _finding()
        patched_deep_agent["fingerprint"] = compute_fingerprint(finding)
        llm = MockLLM(responses=["unused"])
        state = _state(str(tmp_path), finding)

        result = issue_orchestrator_node(state, llm=llm)

        assert len(patched_deep_agent["build_calls"]) == 1
        kwargs = patched_deep_agent["build_calls"][0]["kwargs"]
        assert kwargs.get("role") is AgentRole.TRIAGER
        assert "triaged_issues" in result

    def test_flag_on_parses_issues_by_fingerprint(
        self,
        tmp_path: Path,
        deep_flag_on: None,
        patched_deep_agent: dict[str, Any],
    ) -> None:
        del deep_flag_on
        finding = _finding()
        fp = compute_fingerprint(finding)
        patched_deep_agent["fingerprint"] = fp
        llm = MockLLM(responses=["unused"])
        state = _state(str(tmp_path), finding)

        result = issue_orchestrator_node(state, llm=llm)

        issues: list[Issue] = result["triaged_issues"]
        assert len(issues) == 1
        issue = issues[0]
        assert issue.fingerprint == fp
        assert issue.disposition is IssueDisposition.MUST_FIX_BEFORE_SHIP
        assert issue.sla_target == "24h"
        assert issue.severity is IssueSeverity.HIGH

    def test_flag_on_populates_trace(
        self,
        tmp_path: Path,
        deep_flag_on: None,
        patched_deep_agent: dict[str, Any],
    ) -> None:
        del deep_flag_on
        finding = _finding()
        patched_deep_agent["fingerprint"] = compute_fingerprint(finding)
        llm = MockLLM(responses=["unused"])
        state = _state(str(tmp_path), finding)

        result = issue_orchestrator_node(state, llm=llm)

        trace = result["deep_agent_traces"]["issue_orchestrator_node"]
        assert isinstance(trace, DeepAgentTrace)
        assert trace.role is AgentRole.TRIAGER
        assert trace.tool_invocations == []
        assert "vfs:/context/issues_output.json" in trace.vfs_keys

    def test_flag_off_uses_legacy_path(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("FLOWFORGE_DEEP_AGENTS", raising=False)
        called: dict[str, bool] = {"build": False}

        def boom(*_a: object, **_k: object) -> None:
            called["build"] = True

        monkeypatch.setattr(iorch_module, "build_deep_agent", boom)
        monkeypatch.setattr(iorch_module, "_commit_triage_to_repo", lambda *a, **k: None)
        monkeypatch.setattr(iorch_module, "_create_github_issues", lambda *a, **k: None)

        finding = _finding()
        fp = compute_fingerprint(finding)
        legacy = json.dumps(
            {
                "issues": [
                    {
                        "fingerprint": fp,
                        "disposition": "can_follow_up",
                        "remediation": "Track separately.",
                    },
                ],
            },
        )
        llm = MockLLM(responses=[legacy])
        state = _state(str(tmp_path), finding)

        result = issue_orchestrator_node(state, llm=llm)

        assert called["build"] is False
        assert "triaged_issues" in result
        assert "deep_agent_traces" not in result

    def test_no_findings_skips_dispatch(
        self,
        tmp_path: Path,
        deep_flag_on: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        del deep_flag_on
        called: dict[str, bool] = {"build": False}

        def boom(*_a: object, **_k: object) -> None:
            called["build"] = True

        monkeypatch.setattr(iorch_module, "build_deep_agent", boom)

        llm = MockLLM(responses=["unused"])
        state = GraphState(request="empty", run_status=RunStatus.RUNNING, workdir=str(tmp_path))

        result = issue_orchestrator_node(state, llm=llm)

        assert called["build"] is False
        assert result["triaged_issues"] == []


class TestParseIssueItems:
    """Defensive parsing of agent-emitted issue rows."""

    def _deduped(self) -> dict[str, Finding]:
        f = _finding()
        return {compute_fingerprint(f): f}

    def test_drops_non_dict_rows(self) -> None:
        deduped = self._deduped()
        assert _parse_issue_items(["string", 42, None], deduped) == []

    def test_drops_rows_with_non_string_fingerprint(self) -> None:
        deduped = self._deduped()
        items = [{"fingerprint": 123, "disposition": "must_fix_before_ship"}]
        assert _parse_issue_items(items, deduped) == []

    def test_drops_rows_with_unknown_fingerprint(self) -> None:
        deduped = self._deduped()
        items = [{"fingerprint": "nope", "disposition": "must_fix_before_ship"}]
        assert _parse_issue_items(items, deduped) == []

    def test_drops_rows_with_non_string_disposition(self) -> None:
        deduped = self._deduped()
        fp = next(iter(deduped))
        items = [{"fingerprint": fp, "disposition": 7}]
        assert _parse_issue_items(items, deduped) == []

    def test_drops_rows_with_invalid_disposition(self) -> None:
        deduped = self._deduped()
        fp = next(iter(deduped))
        items = [{"fingerprint": fp, "disposition": "definitely_not_a_thing"}]
        assert _parse_issue_items(items, deduped) == []

    def test_remediation_default_when_missing(self) -> None:
        deduped = self._deduped()
        fp = next(iter(deduped))
        items = [{"fingerprint": fp, "disposition": "can_follow_up"}]
        issues = _parse_issue_items(items, deduped)
        assert len(issues) == 1
        assert issues[0].remediation == ""

    def test_owner_and_sla_target_only_strings(self) -> None:
        deduped = self._deduped()
        fp = next(iter(deduped))
        items = [
            {
                "fingerprint": fp,
                "disposition": "can_follow_up",
                "remediation": "Track.",
                "owner": 7,  # non-string → None
                "sla_target": ["bad"],  # non-string → None
            },
        ]
        issues = _parse_issue_items(items, deduped)
        assert len(issues) == 1
        assert issues[0].owner is None
        assert issues[0].sla_target is None

    def test_well_formed_row_yields_issue(self) -> None:
        deduped = self._deduped()
        fp = next(iter(deduped))
        items = [
            {
                "fingerprint": fp,
                "disposition": "must_fix_before_ship",
                "remediation": "Fix it.",
                "owner": "code_review_node",
                "sla_target": "24h",
            },
        ]
        issues = _parse_issue_items(items, deduped)
        assert len(issues) == 1
        assert issues[0].disposition is IssueDisposition.MUST_FIX_BEFORE_SHIP
        assert issues[0].remediation == "Fix it."
        assert issues[0].owner == "code_review_node"
        assert issues[0].sla_target == "24h"
