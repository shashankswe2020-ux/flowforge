"""Unit tests for ship_node with readiness gate."""

from __future__ import annotations

from unittest.mock import patch

from src.nodes.ship import (
    _determine_next_version,
    _generate_changelog_entry,
    _generate_readme,
    _get_current_version,
    _write_changelog,
    _write_readme,
    check_readiness,
    ship_node,
)
from src.state.models import (
    GraphState,
    Issue,
    IssueDisposition,
    IssueSeverity,
    RunStatus,
    ShippingReadiness,
    SpecOutput,
    Task,
    TaskDefinition,
    TaskStatus,
)
from tests.mocks import MockLLM


def _task(task_id: str = "t1", title: str = "Add feature") -> Task:
    """Helper to create a Task with required definition."""
    return Task(
        task_id=task_id,
        definition=TaskDefinition(
            task_id=task_id,
            title=title,
            description="Implement the feature",
            acceptance_checks=["Works"],
            estimated_complexity="m",
            capability_type="agent_only",
            verification_step="Run tests",
        ),
        status=TaskStatus.SUCCEEDED,
    )


def _issue(
    *,
    issue_id: str = "issue-1",
    severity: IssueSeverity = IssueSeverity.MEDIUM,
    disposition: IssueDisposition = IssueDisposition.CAN_FOLLOW_UP,
) -> Issue:
    return Issue(
        id=issue_id,
        source_node="code_review_node",
        fingerprint="abc123",
        severity=severity,
        confidence=0.9,
        disposition=disposition,
        remediation="Fix it",
    )


def _state(
    *,
    issues: list[Issue] | None = None,
    human_approved: bool = False,
    production_mode: bool = True,
    security_report_present: bool = True,
) -> GraphState:
    return GraphState(
        request="Build API",
        run_status=RunStatus.RUNNING,
        triaged_issues=issues or [],
        shipping_readiness=ShippingReadiness(
            is_ready=False,
            waived_by="human" if human_approved else None,
        ),
    )


class TestReadinessChecks:
    """check_readiness validates shipping prerequisites."""

    def test_no_issues_is_ready(self) -> None:
        state = _state(issues=[])
        result = check_readiness(state, production_mode=False)
        assert result.is_ready
        assert result.blockers == []

    def test_critical_findings_block(self) -> None:
        issues = [
            _issue(
                severity=IssueSeverity.CRITICAL,
                disposition=IssueDisposition.MUST_FIX_BEFORE_SHIP,
            ),
        ]
        state = _state(issues=issues)
        result = check_readiness(state, production_mode=False)
        assert not result.is_ready
        assert len(result.blockers) == 1
        assert "critical" in result.blockers[0].reason.lower()

    def test_high_severity_must_fix_blocks(self) -> None:
        issues = [
            _issue(
                severity=IssueSeverity.HIGH,
                disposition=IssueDisposition.MUST_FIX_BEFORE_SHIP,
            ),
        ]
        state = _state(issues=issues)
        result = check_readiness(state, production_mode=False)
        assert not result.is_ready

    def test_can_follow_up_does_not_block(self) -> None:
        issues = [_issue(severity=IssueSeverity.HIGH, disposition=IssueDisposition.CAN_FOLLOW_UP)]
        state = _state(issues=issues)
        result = check_readiness(state, production_mode=False)
        assert result.is_ready

    def test_rejected_does_not_block(self) -> None:
        issues = [_issue(severity=IssueSeverity.CRITICAL, disposition=IssueDisposition.REJECTED)]
        state = _state(issues=issues)
        result = check_readiness(state, production_mode=False)
        assert result.is_ready

    def test_multiple_blockers_accumulated(self) -> None:
        issues = [
            _issue(
                issue_id="i1",
                severity=IssueSeverity.CRITICAL,
                disposition=IssueDisposition.MUST_FIX_BEFORE_SHIP,
            ),
            _issue(
                issue_id="i2",
                severity=IssueSeverity.HIGH,
                disposition=IssueDisposition.MUST_FIX_BEFORE_SHIP,
            ),
        ]
        state = _state(issues=issues)
        result = check_readiness(state, production_mode=False)
        assert len(result.blockers) == 2


class TestProductionApproval:
    """Production mode requires explicit human approval."""

    def test_production_without_approval_blocks(self) -> None:
        state = _state(issues=[], human_approved=False)
        result = check_readiness(state, production_mode=True)
        assert not result.is_ready
        assert any("approval" in b.reason.lower() for b in result.blockers)

    def test_production_with_approval_passes(self) -> None:
        state = _state(issues=[], human_approved=True)
        result = check_readiness(state, production_mode=True)
        assert result.is_ready

    def test_non_production_no_approval_needed(self) -> None:
        state = _state(issues=[], human_approved=False)
        result = check_readiness(state, production_mode=False)
        assert result.is_ready


class TestSecurityGate:
    """Security findings enforce fail-closed semantics."""

    def test_security_must_fix_blocks(self) -> None:
        issues = [
            Issue(
                id="sec-1",
                source_node="security_audit_node",
                fingerprint="sec-fp",
                severity=IssueSeverity.HIGH,
                confidence=0.95,
                disposition=IssueDisposition.MUST_FIX_BEFORE_SHIP,
                remediation="Remove hardcoded secret",
            ),
        ]
        state = _state(issues=issues)
        result = check_readiness(state, production_mode=False)
        assert not result.is_ready
        assert any("security" in b.reason.lower() for b in result.blockers)


class TestShipNode:
    """ship_node produces readiness report or ships."""

    def test_blocked_produces_report(self) -> None:
        issues = [
            _issue(
                severity=IssueSeverity.CRITICAL,
                disposition=IssueDisposition.MUST_FIX_BEFORE_SHIP,
            ),
        ]
        state = _state(issues=issues)
        result = ship_node(state, production_mode=False)
        readiness = result["shipping_readiness"]
        assert not readiness.is_ready
        assert readiness.unresolved_must_fix == 1

    def test_ready_produces_ship_result(self) -> None:
        state = _state(issues=[], human_approved=False)
        result = ship_node(state, production_mode=False)
        assert result["shipping_readiness"].is_ready
        assert result["shipping_result"].shipped

    def test_blocked_sets_run_status(self) -> None:
        issues = [
            _issue(
                severity=IssueSeverity.CRITICAL,
                disposition=IssueDisposition.MUST_FIX_BEFORE_SHIP,
            ),
        ]
        state = _state(issues=issues)
        result = ship_node(state, production_mode=False)
        assert result["run_status"] == RunStatus.BLOCKED

    def test_success_sets_run_status(self) -> None:
        state = _state(issues=[], human_approved=False)
        result = ship_node(state, production_mode=False)
        assert result["run_status"] == RunStatus.SUCCEEDED

    def test_blocker_count_by_severity(self) -> None:
        issues = [
            _issue(
                issue_id="i1",
                severity=IssueSeverity.CRITICAL,
                disposition=IssueDisposition.MUST_FIX_BEFORE_SHIP,
            ),
            _issue(
                issue_id="i2",
                severity=IssueSeverity.HIGH,
                disposition=IssueDisposition.MUST_FIX_BEFORE_SHIP,
            ),
            _issue(
                issue_id="i3",
                severity=IssueSeverity.HIGH,
                disposition=IssueDisposition.MUST_FIX_BEFORE_SHIP,
            ),
        ]
        state = _state(issues=issues)
        result = ship_node(state, production_mode=False)
        counts = result["shipping_readiness"].blocker_count_by_severity
        assert counts["critical"] == 1
        assert counts["high"] == 2


class TestVersioning:
    """Version determination follows semantic versioning."""

    def test_new_tasks_bump_minor(self) -> None:
        state = GraphState(
            request="Build API",
            run_status=RunStatus.RUNNING,
            tasks=[
                _task("t1", "Add endpoint"),
            ],
        )
        next_ver = _determine_next_version(state, "1.2.3")
        assert next_ver == "1.3.0"

    def test_no_changes_bump_patch(self) -> None:
        state = GraphState(request="Build API", run_status=RunStatus.RUNNING)
        next_ver = _determine_next_version(state, "1.2.3")
        assert next_ver == "1.2.4"

    def test_breaking_change_bumps_major(self) -> None:
        from src.state.models import Finding
        state = GraphState(
            request="Build API",
            run_status=RunStatus.RUNNING,
            review_findings=[
                Finding(
                    finding_id="f1",
                    source_node="code_review_node",
                    severity=IssueSeverity.HIGH,
                    confidence=0.9,
                    title="Breaking change in API contract",
                    description="breaking change detected",
                ),
            ],
        )
        next_ver = _determine_next_version(state, "1.2.3")
        assert next_ver == "2.0.0"

    def test_breaking_change_v0_bumps_minor(self) -> None:
        from src.state.models import Finding
        state = GraphState(
            request="Build API",
            run_status=RunStatus.RUNNING,
            review_findings=[
                Finding(
                    finding_id="f1",
                    source_node="code_review_node",
                    severity=IssueSeverity.HIGH,
                    confidence=0.9,
                    title="Breaking change",
                    description="breaking",
                ),
            ],
        )
        next_ver = _determine_next_version(state, "0.3.1")
        assert next_ver == "0.4.0"

    def test_get_current_version_from_pyproject(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nversion = "2.5.0"\n')
        assert _get_current_version(tmp_path) == "2.5.0"

    def test_get_current_version_default(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        assert _get_current_version(tmp_path) == "0.1.0"


class TestDocGeneration:
    """README and CHANGELOG generation via LLM."""

    def test_generate_readme(self) -> None:
        state = GraphState(
            request="Build API",
            run_status=RunStatus.RUNNING,
            spec=SpecOutput(
                artifact_path="docs/spec/spec.md",
                summary="An API server",
                objective="Build a REST API",
                target_users="Developers",
                acceptance_criteria=["Serves requests"],
                tech_stack=["Python", "FastAPI"],
                commands={"test": "pytest", "build": "pip install ."},
            ),
        )
        llm = MockLLM(responses=["# My Project\n\nA great project."])
        readme = _generate_readme(state, llm)
        assert "My Project" in readme
        assert "Build a REST API" in llm._call_history[0]

    def test_generate_changelog(self) -> None:
        state = GraphState(
            request="Build API",
            run_status=RunStatus.RUNNING,
            tasks=[
                _task("t1", "Add auth"),
            ],
        )
        llm = MockLLM(responses=["## [1.0.0] - 2026-06-05\n\n### Added\n- Auth module"])
        entry = _generate_changelog_entry(state, "1.0.0", llm)
        assert "1.0.0" in entry
        assert "Add auth" in llm._call_history[0]

    def test_write_readme(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        path = _write_readme("# Hello\n\nWorld", tmp_path)
        assert path.exists()
        assert "Hello" in path.read_text()

    def test_write_changelog_new(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        path = _write_changelog("## [1.0.0]\n\n### Added\n- Feature", tmp_path)
        assert path.exists()
        content = path.read_text()
        assert "# Changelog" in content
        assert "1.0.0" in content

    def test_write_changelog_prepend(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CHANGELOG.md").write_text(
            "# Changelog\n\n## [0.1.0]\n\n### Added\n- Initial\n",
        )
        _write_changelog("## [0.2.0]\n\n### Added\n- New feature", tmp_path)
        content = (tmp_path / "CHANGELOG.md").read_text()
        # New entry should appear before old
        assert content.index("0.2.0") < content.index("0.1.0")


class TestShipWithLLM:
    """ship_node with LLM generates docs and versions."""

    @patch("src.nodes.ship._commit_release_artifacts", return_value="abc123")
    @patch("src.nodes.ship._create_git_tag", return_value=True)
    @patch("src.nodes.ship._update_version_in_pyproject", return_value=True)
    def test_ship_generates_readme_and_changelog(
        self, mock_version, mock_tag, mock_commit, tmp_path, monkeypatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        state = GraphState(
            request="Build API",
            run_status=RunStatus.RUNNING,
            tasks=[
                _task("t1", "Add auth"),
            ],
            shipping_readiness=ShippingReadiness(is_ready=False, waived_by="human"),
        )
        llm = MockLLM(responses=[
            "# My Project\n\nGreat project.",
            "## [0.2.0]\n\n### Added\n- Auth",
        ])
        result = ship_node(state, production_mode=False, llm=llm)
        assert result["run_status"] == RunStatus.SUCCEEDED
        assert result["shipping_result"].shipped
        assert (tmp_path / "README.md").exists()
        assert (tmp_path / "CHANGELOG.md").exists()
        assert len(llm._call_history) == 2  # README + CHANGELOG prompts

    def test_ship_without_llm_skips_docs(self) -> None:
        state = _state(issues=[], human_approved=False)
        result = ship_node(state, production_mode=False, llm=None)
        assert result["run_status"] == RunStatus.SUCCEEDED
        assert result["shipping_result"].shipped

    def test_ship_provenance_chain(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n')
        state = GraphState(
            request="Build API",
            run_status=RunStatus.RUNNING,
            tasks=[
                _task("t1", "Feature"),
            ],
        )
        llm = MockLLM(responses=["# README", "## [1.1.0]"])
        with patch("src.nodes.ship._commit_release_artifacts", return_value=None), \
             patch("src.nodes.ship._create_git_tag", return_value=False):
            result = ship_node(state, production_mode=False, llm=llm)
        provenance = result["shipping_result"].provenance_chain
        assert "version:1.1.0" in provenance
        assert "previous:1.0.0" in provenance
        assert "readme:generated" in provenance
        assert "changelog:generated" in provenance
