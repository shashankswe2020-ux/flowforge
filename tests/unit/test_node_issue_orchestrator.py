"""Unit tests for issue_orchestrator_node."""

from __future__ import annotations

import json

from src.nodes.issue_orchestrator import (
    _classify_category,
    _prioritize,
    compute_fingerprint,
    issue_orchestrator_node,
    merge_findings,
)
from src.state.models import (
    Finding,
    GraphState,
    Issue,
    IssueDisposition,
    IssueSeverity,
    RunStatus,
)
from tests.mocks import MockLLM


def _finding(
    *,
    finding_id: str = "f1",
    source_node: str = "code_review_node",
    severity: IssueSeverity = IssueSeverity.MEDIUM,
    title: str = "Missing error handling",
    description: str = "No try/except",
    file_path: str | None = "src/auth.py",
    line_range: tuple[int, int] | None = (10, 12),
) -> Finding:
    return Finding(
        finding_id=finding_id,
        source_node=source_node,
        severity=severity,
        confidence=0.85,
        title=title,
        description=description,
        file_path=file_path,
        line_range=line_range,
    )


def _triage_response(dispositions: list[dict[str, str]] | None = None) -> str:
    default = [
        {
            "fingerprint": "fp1",
            "disposition": "must_fix_before_ship",
            "remediation": "Add error handling",
            "owner": None,
            "sla_target": None,
        },
    ]
    return json.dumps({"issues": dispositions or default})


def _state_with_findings(
    review: list[Finding] | None = None,
    security: list[Finding] | None = None,
    test: list[Finding] | None = None,
) -> GraphState:
    return GraphState(
        request="Build API",
        run_status=RunStatus.RUNNING,
        review_findings=review or [],
        security_findings=security or [],
        test_findings=test or [],
    )


class TestMergeFindings:
    """merge_findings collects findings from all quality nodes."""

    def test_merges_all_sources(self) -> None:
        review = [_finding(finding_id="r1", source_node="code_review_node")]
        security = [_finding(finding_id="s1", source_node="security_audit_node")]
        test = [_finding(finding_id="t1", source_node="test_engineer_node")]
        merged = merge_findings(review, security, test)
        assert len(merged) == 3

    def test_empty_sources(self) -> None:
        merged = merge_findings([], [], [])
        assert merged == []


class TestDeduplication:
    """Findings with same fingerprint are deduplicated."""

    def test_same_finding_deduplicated(self) -> None:
        f1 = _finding(finding_id="r1", title="Same issue", file_path="src/a.py", line_range=(1, 2))
        f2 = _finding(finding_id="s1", title="Same issue", file_path="src/a.py", line_range=(1, 2))
        fp1 = compute_fingerprint(f1)
        fp2 = compute_fingerprint(f2)
        assert fp1 == fp2

    def test_different_findings_different_fingerprints(self) -> None:
        f1 = _finding(finding_id="r1", title="Issue A", file_path="src/a.py")
        f2 = _finding(finding_id="r2", title="Issue B", file_path="src/b.py")
        fp1 = compute_fingerprint(f1)
        fp2 = compute_fingerprint(f2)
        assert fp1 != fp2

    def test_fingerprint_ignores_finding_id_and_source(self) -> None:
        """Fingerprint is content-based, not identity-based."""
        f1 = _finding(finding_id="r1", source_node="code_review_node", title="X", file_path="y")
        f2 = _finding(finding_id="s1", source_node="security_audit_node", title="X", file_path="y")
        assert compute_fingerprint(f1) == compute_fingerprint(f2)


class TestIssueClassification:
    """issue_orchestrator_node classifies findings into dispositions."""

    def test_produces_issues(self) -> None:
        review = [_finding(finding_id="r1")]
        state = _state_with_findings(review=review)
        fp = compute_fingerprint(review[0])
        response = json.dumps(
            {
                "issues": [
                    {
                        "fingerprint": fp,
                        "disposition": "must_fix_before_ship",
                        "remediation": "Fix it",
                        "owner": None,
                        "sla_target": None,
                    },
                ],
            },
        )
        llm = MockLLM(responses=[response])
        result = issue_orchestrator_node(state, llm=llm)
        assert len(result["triaged_issues"]) == 1

    def test_issue_schema(self) -> None:
        review = [_finding(finding_id="r1", severity=IssueSeverity.HIGH)]
        state = _state_with_findings(review=review)
        fp = compute_fingerprint(review[0])
        response = json.dumps(
            {
                "issues": [
                    {
                        "fingerprint": fp,
                        "disposition": "can_follow_up",
                        "remediation": "Track in backlog",
                        "owner": "team-backend",
                        "sla_target": "next-sprint",
                    },
                ],
            },
        )
        llm = MockLLM(responses=[response])
        result = issue_orchestrator_node(state, llm=llm)
        issue = result["triaged_issues"][0]
        assert isinstance(issue, Issue)
        assert issue.fingerprint == fp
        assert issue.severity == IssueSeverity.HIGH
        assert issue.disposition == IssueDisposition.CAN_FOLLOW_UP
        assert issue.remediation == "Track in backlog"
        assert issue.source_node == "code_review_node"

    def test_rejected_disposition(self) -> None:
        review = [_finding(finding_id="r1", severity=IssueSeverity.LOW)]
        state = _state_with_findings(review=review)
        fp = compute_fingerprint(review[0])
        response = json.dumps(
            {
                "issues": [
                    {
                        "fingerprint": fp,
                        "disposition": "rejected",
                        "remediation": "Not actionable",
                        "owner": None,
                        "sla_target": None,
                    },
                ],
            },
        )
        llm = MockLLM(responses=[response])
        result = issue_orchestrator_node(state, llm=llm)
        assert result["triaged_issues"][0].disposition == IssueDisposition.REJECTED

    def test_duplicates_merged_before_triage(self) -> None:
        """Same finding from two sources produces one issue."""
        f1 = _finding(
            finding_id="r1",
            source_node="code_review_node",
            title="Same",
            file_path="src/x.py",
            line_range=(5, 5),
        )
        f2 = _finding(
            finding_id="s1",
            source_node="security_audit_node",
            title="Same",
            file_path="src/x.py",
            line_range=(5, 5),
        )
        state = _state_with_findings(review=[f1], security=[f2])
        fp = compute_fingerprint(f1)
        response = json.dumps(
            {
                "issues": [
                    {
                        "fingerprint": fp,
                        "disposition": "must_fix_before_ship",
                        "remediation": "Fix",
                        "owner": None,
                        "sla_target": None,
                    },
                ],
            },
        )
        llm = MockLLM(responses=[response])
        result = issue_orchestrator_node(state, llm=llm)
        # Only 1 issue despite 2 findings with same fingerprint
        assert len(result["triaged_issues"]) == 1

    def test_llm_receives_findings_context(self) -> None:
        review = [_finding(finding_id="r1", title="Auth bug")]
        state = _state_with_findings(review=review)
        fp = compute_fingerprint(review[0])
        response = json.dumps(
            {
                "issues": [
                    {
                        "fingerprint": fp,
                        "disposition": "must_fix_before_ship",
                        "remediation": "Fix",
                        "owner": None,
                        "sla_target": None,
                    },
                ],
            },
        )
        llm = MockLLM(responses=[response])
        issue_orchestrator_node(state, llm=llm)
        assert "Auth bug" in llm.call_history[0]


class TestMCPToolTracking:
    """All tracker operations use MCP tool pattern."""

    def test_result_includes_tool_operations(self) -> None:
        review = [_finding(finding_id="r1")]
        state = _state_with_findings(review=review)
        fp = compute_fingerprint(review[0])
        response = json.dumps(
            {
                "issues": [
                    {
                        "fingerprint": fp,
                        "disposition": "must_fix_before_ship",
                        "remediation": "Fix",
                        "owner": None,
                        "sla_target": None,
                    },
                ],
            },
        )
        llm = MockLLM(responses=[response])
        result = issue_orchestrator_node(state, llm=llm)
        # MCP tool operations are tracked in the result
        assert "tool_operations" in result
        assert len(result["tool_operations"]) >= 1
        assert result["tool_operations"][0]["tool"] == "create_issue"


class TestClassification:
    """_classify_category assigns correct category per agent methodology."""

    def test_security_finding_from_security_node(self) -> None:
        f = _finding(source_node="security_audit_node", severity=IssueSeverity.HIGH)
        assert _classify_category(f) == "critical_security"

    def test_security_finding_medium(self) -> None:
        f = _finding(source_node="security_audit_node", severity=IssueSeverity.MEDIUM)
        assert _classify_category(f) == "medium_security"

    def test_security_keyword_in_title(self) -> None:
        f = _finding(title="SQL injection in query builder", source_node="code_review_node",
                     severity=IssueSeverity.HIGH)
        assert _classify_category(f) == "critical_security"

    def test_bug_keyword(self) -> None:
        f = _finding(title="App crash on startup", source_node="code_review_node",
                     severity=IssueSeverity.MEDIUM)
        assert _classify_category(f) == "bug"

    def test_test_finding_from_test_node(self) -> None:
        f = _finding(source_node="test_engineer_node", title="Missing coverage")
        assert _classify_category(f) == "testing"

    def test_documentation_keyword(self) -> None:
        f = _finding(title="Missing docstring for public API", source_node="code_review_node")
        assert _classify_category(f) == "documentation"

    def test_dependency_keyword(self) -> None:
        f = _finding(title="Deprecated dependency needs bump", source_node="code_review_node")
        assert _classify_category(f) == "dependency"

    def test_default_to_code_quality(self) -> None:
        f = _finding(title="Extract helper method", source_node="code_review_node",
                     severity=IssueSeverity.LOW)
        assert _classify_category(f) == "code_quality"


class TestPrioritization:
    """_prioritize sorts findings by severity tier."""

    def test_critical_security_first(self) -> None:
        f_sec = _finding(finding_id="s1", source_node="security_audit_node",
                         severity=IssueSeverity.HIGH, title="XSS vuln")
        f_bug = _finding(finding_id="b1", source_node="code_review_node",
                         severity=IssueSeverity.MEDIUM, title="App crash on click")
        f_test = _finding(finding_id="t1", source_node="test_engineer_node",
                          severity=IssueSeverity.LOW, title="Missing test")
        from src.nodes.issue_orchestrator import _deduplicate
        deduped = _deduplicate([f_sec, f_bug, f_test])
        prioritized = _prioritize(deduped)
        categories = [cat for _, _, cat in prioritized]
        assert categories.index("critical_security") < categories.index("bug")
        assert categories.index("bug") < categories.index("testing")


class TestRejectedDisposition:
    """Rejected findings produce close_issue tool operations."""

    def test_rejected_produces_close_operation(self) -> None:
        review = [_finding(finding_id="r1")]
        state = _state_with_findings(review=review)
        fp = compute_fingerprint(review[0])
        response = json.dumps(
            {
                "issues": [
                    {
                        "fingerprint": fp,
                        "disposition": "rejected",
                        "remediation": "False positive",
                        "owner": None,
                        "sla_target": None,
                    },
                ],
            },
        )
        llm = MockLLM(responses=[response])
        result = issue_orchestrator_node(state, llm=llm)
        assert result["tool_operations"][0]["tool"] == "close_issue"
        assert "rejected" in result["tool_operations"][0]["args"]["reason"]


class TestTriagePrompt:
    """LLM receives enhanced prompt with categories and priority context."""

    def test_prompt_contains_categories(self) -> None:
        review = [_finding(finding_id="r1", title="Auth bypass")]
        state = _state_with_findings(review=review)
        fp = compute_fingerprint(review[0])
        response = json.dumps(
            {
                "issues": [
                    {
                        "fingerprint": fp,
                        "disposition": "must_fix_before_ship",
                        "remediation": "Fix",
                        "owner": None,
                        "sla_target": None,
                    },
                ],
            },
        )
        llm = MockLLM(responses=[response])
        issue_orchestrator_node(state, llm=llm)
        prompt = llm._call_history[0]
        assert "critical_security" in prompt
        assert "must_fix_before_ship" in prompt
        assert "can_follow_up" in prompt
        assert "rejected" in prompt
