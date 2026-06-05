"""Ship node — readiness gate, documentation generation, versioning, and release.

Following the ship agent methodology:
1. Readiness checks (blockers, security findings, human approval)
2. README generation/update from spec + plan context
3. CHANGELOG generation with version entry
4. Semantic versioning based on change scope
5. Git commit of release artifacts
6. Ship or block with actionable feedback
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from flowforge.state.models import (
    GraphState,
    IssueDisposition,
    IssueSeverity,
    RunStatus,
    ShippingBlocker,
    ShippingReadiness,
    ShippingResult,
)

# Severities that block shipping when disposition is must_fix_before_ship
_BLOCKING_SEVERITIES: frozenset[IssueSeverity] = frozenset(
    {
        IssueSeverity.CRITICAL,
        IssueSeverity.HIGH,
    },
)


class LLMProtocol(Protocol):
    """Minimal LLM interface."""

    def invoke(self, prompt: str) -> Any: ...  # noqa: ANN401


class ShipBlockedError(Exception):
    """Raised when shipping is blocked by readiness checks."""

    def __init__(self, *, blockers: list[ShippingBlocker]) -> None:
        self.blockers = blockers
        super().__init__(
            f"Shipping blocked by {len(blockers)} blocker(s): "
            + ", ".join(b.reason for b in blockers),
        )


@dataclass(frozen=True)
class ReadinessCheckResult:
    """Result from readiness checks."""

    is_ready: bool
    blockers: list[ShippingBlocker] = field(default_factory=list)


def check_readiness(
    state: GraphState,
    *,
    production_mode: bool = True,
) -> ReadinessCheckResult:
    """Run all shipping readiness checks.

    Checks:
    1. No must_fix_before_ship issues with critical/high severity
    2. No security must-fix issues (fail-closed)
    3. Production mode requires explicit human approval

    Returns:
        ReadinessCheckResult with is_ready flag and any blockers.
    """
    blockers: list[ShippingBlocker] = []

    # Check must-fix issues
    for issue in state.triaged_issues:
        if issue.disposition != IssueDisposition.MUST_FIX_BEFORE_SHIP:
            continue

        if issue.severity in _BLOCKING_SEVERITIES:
            source_label = "security" if issue.source_node == "security_audit_node" else "quality"
            blockers.append(
                ShippingBlocker(
                    blocker_id=f"blocker-{issue.id}",
                    severity=issue.severity,
                    reason=(
                        f"{issue.severity.value.capitalize()} {source_label} issue "
                        f"must be fixed before shipping: {issue.remediation}"
                    ),
                    source_issue_id=issue.id,
                ),
            )

    # Production mode approval check
    if production_mode:
        readiness = state.shipping_readiness
        if readiness.waived_by is None:
            blockers.append(
                ShippingBlocker(
                    blocker_id="blocker-no-approval",
                    severity=IssueSeverity.CRITICAL,
                    reason="Production shipping requires explicit human approval",
                ),
            )

    is_ready = len(blockers) == 0
    return ReadinessCheckResult(is_ready=is_ready, blockers=blockers)


# ---------------------------------------------------------------------------
# Version management
# ---------------------------------------------------------------------------


def _get_current_version(workdir: Path) -> str:
    """Get current version from pyproject.toml or package.json in ``workdir``."""
    # Try pyproject.toml first
    pyproject = workdir / "pyproject.toml"
    if pyproject.exists():
        content = pyproject.read_text()
        match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
        if match:
            return match.group(1)

    # Try package.json
    pkg_json = workdir / "package.json"
    if pkg_json.exists():
        data = json.loads(pkg_json.read_text())
        return data.get("version", "0.1.0")

    return "0.1.0"


def _determine_next_version(state: GraphState, current: str) -> str:
    """Determine next version using semantic versioning.

    Rules:
    - MAJOR: breaking changes (new APIs replacing old ones)
    - MINOR: new features (new tasks completed)
    - PATCH: bug fixes, documentation, refactors
    """
    parts = current.split(".")
    if len(parts) != 3:
        return "0.1.0"

    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])

    # Check for breaking changes in findings
    has_breaking = any(
        "breaking" in (f.title or "").lower() or "breaking" in (f.description or "").lower()
        for f in (state.review_findings + state.security_findings + state.test_findings)
    )

    if has_breaking:
        if major == 0:
            return f"0.{minor + 1}.0"
        return f"{major + 1}.0.0"

    # New features = tasks completed
    task_count = len(state.tasks)
    if task_count > 0:
        return f"{major}.{minor + 1}.0"

    # Default to patch
    return f"{major}.{minor}.{patch + 1}"


def _update_version_in_pyproject(new_version: str, workdir: Path) -> bool:
    """Update version in ``<workdir>/pyproject.toml``. Returns True if updated."""
    pyproject = workdir / "pyproject.toml"
    if not pyproject.exists():
        return False

    content = pyproject.read_text()
    new_content = re.sub(
        r'^(version\s*=\s*)"[^"]+"',
        f'\\1"{new_version}"',
        content,
        count=1,
        flags=re.MULTILINE,
    )

    if new_content != content:
        pyproject.write_text(new_content)
        return True
    return False


# ---------------------------------------------------------------------------
# README generation
# ---------------------------------------------------------------------------


def _build_readme_prompt(state: GraphState) -> str:
    """Build prompt to generate README from spec + plan context."""
    spec_section = ""
    if state.spec:
        spec_section = f"""
## Specification Context
- Objective: {state.spec.objective}
- Target Users: {state.spec.target_users}
- Summary: {state.spec.summary}
- Tech Stack: {', '.join(state.spec.tech_stack)}
- Commands: {json.dumps(state.spec.commands)}
- Acceptance Criteria: {json.dumps(state.spec.acceptance_criteria)}
- Security: {json.dumps(state.spec.security_considerations)}
"""

    plan_section = ""
    if state.implementation_plan:
        tasks_desc = [
            f"- {t.title}: {t.description}"
            for t in state.implementation_plan.dag.tasks[:10]
        ]
        plan_section = f"""
## Implementation Plan
{chr(10).join(tasks_desc)}
"""

    task_section = ""
    if state.tasks:
        completed = [f"- {t.task_id}: {t.definition.title}" for t in state.tasks[:10]]
        task_section = f"""
## Completed Tasks
{chr(10).join(completed)}
"""

    return f"""Generate a production-quality README.md for this project.

{spec_section}
{plan_section}
{task_section}

## README Requirements (per ship agent methodology)
Include these sections:
1. **Title + Badge** — project name with a brief tagline
2. **Description** — what this project does and why
3. **Features** — bullet list of key capabilities
4. **Quick Start** — minimal steps to get running (install, configure, run)
5. **Usage** — examples of common operations
6. **Configuration** — environment variables, config files
7. **Development** — how to build, test, lint locally
8. **Architecture** — brief overview of project structure
9. **Contributing** — how to contribute
10. **License** — license type

Output ONLY the README markdown content, no wrapping fences.
"""


def _build_changelog_prompt(state: GraphState, version: str) -> str:
    """Build prompt to generate CHANGELOG entry."""
    changes: list[str] = []

    if state.tasks:
        for t in state.tasks:
            changes.append(f"- Implemented: {t.definition.title}")

    if state.triaged_issues:
        fixed = [i for i in state.triaged_issues if i.disposition == IssueDisposition.MUST_FIX_BEFORE_SHIP]
        for issue in fixed[:5]:
            changes.append(f"- Fixed: {issue.remediation}")

    changes_text = "\n".join(changes) if changes else "- Initial release"

    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")

    return f"""Generate a CHANGELOG entry for version {version} ({today}).

## Changes Made
{changes_text}

## Format
Use the Keep a Changelog format (https://keepachangelog.com/en/1.0.0/):
- Group by: Added, Changed, Fixed, Security, Removed
- Each item is a bullet point with a brief description
- Include the version header with date

If a CHANGELOG already exists, generate ONLY the new version section to prepend.
Output ONLY the changelog entry markdown, no wrapping fences.
"""


def _generate_readme(state: GraphState, llm: LLMProtocol) -> str:
    """Generate README content via LLM."""
    prompt = _build_readme_prompt(state)
    response = llm.invoke(prompt)
    content = response.content if hasattr(response, "content") else str(response)
    # Strip markdown fences if present
    if content.startswith("```"):
        lines = content.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines)
    return content.strip()


def _generate_changelog_entry(state: GraphState, version: str, llm: LLMProtocol) -> str:
    """Generate CHANGELOG entry via LLM."""
    prompt = _build_changelog_prompt(state, version)
    response = llm.invoke(prompt)
    content = response.content if hasattr(response, "content") else str(response)
    if content.startswith("```"):
        lines = content.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines)
    return content.strip()


def _write_readme(content: str, workdir: Path) -> Path:
    """Write or overwrite ``<workdir>/README.md``."""
    readme = workdir / "README.md"
    readme.write_text(content + "\n")
    return readme


def _write_changelog(entry: str, workdir: Path) -> Path:
    """Write or prepend to ``<workdir>/CHANGELOG.md``."""
    changelog = workdir / "CHANGELOG.md"

    if changelog.exists():
        existing = changelog.read_text()
        # Prepend new entry after the top-level header if present
        if existing.startswith("# Changelog"):
            header_end = existing.index("\n") + 1
            new_content = existing[:header_end] + "\n" + entry + "\n\n" + existing[header_end:]
        else:
            new_content = entry + "\n\n" + existing
    else:
        new_content = "# Changelog\n\nAll notable changes to this project will be documented in this file.\n\n" + entry

    changelog.write_text(new_content + "\n")
    return changelog


def _commit_release_artifacts(version: str, files: list[Path], workdir: Path) -> str | None:
    """Git add and commit release artifacts in ``workdir``. Returns commit SHA or None."""
    cwd = str(workdir)
    try:
        for f in files:
            try:
                rel = f.relative_to(workdir)
            except ValueError:
                rel = f
            subprocess.run(["git", "add", str(rel)], cwd=cwd, capture_output=True, check=True)

        # Also add pyproject.toml if version was updated
        pyproject = workdir / "pyproject.toml"
        if pyproject.exists():
            subprocess.run(
                ["git", "add", "pyproject.toml"],
                cwd=cwd, capture_output=True, check=True,
            )

        subprocess.run(
            ["git", "commit", "-m", f"release: v{version} — update README, CHANGELOG, version"],
            cwd=cwd, capture_output=True, text=True, check=True,
        )

        # Get commit SHA
        sha_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd, capture_output=True, text=True, check=True,
        )
        return sha_result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _create_git_tag(version: str, workdir: Path) -> bool:
    """Create a git tag for the release in ``workdir``. Returns True if successful."""
    try:
        subprocess.run(
            ["git", "tag", "-a", f"v{version}", "-m", f"Release v{version}"],
            cwd=str(workdir), capture_output=True, text=True, check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _push_to_remote(workdir: Path) -> bool:
    """Push commits and tags from ``workdir`` to origin. Returns True if pushed."""
    cwd = str(workdir)
    try:
        # Check if a remote named 'origin' exists
        result = subprocess.run(
            ["git", "remote"],
            cwd=cwd, capture_output=True, text=True, check=True,
        )
        if "origin" not in result.stdout.split():
            return False

        subprocess.run(
            ["git", "push", "origin", "HEAD", "--follow-tags"],
            cwd=cwd, capture_output=True, text=True, check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _current_branch(workdir: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(workdir), capture_output=True, text=True, check=True,
        )
        return result.stdout.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _push_branch(workdir: Path, branch: str) -> bool:
    """Push the feature branch with upstream tracking and follow tags."""
    cwd = str(workdir)
    try:
        result = subprocess.run(
            ["git", "remote"],
            cwd=cwd, capture_output=True, text=True, check=True,
        )
        if "origin" not in result.stdout.split():
            return False
        subprocess.run(
            ["git", "push", "-u", "origin", branch, "--follow-tags"],
            cwd=cwd, capture_output=True, text=True, check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _build_pr_body(state: GraphState, *, status: str, branch: str) -> str:
    spec = state.spec
    plan = state.implementation_plan

    lines: list[str] = []
    lines.append(f"**Status:** `{status}` &nbsp;·&nbsp; **Branch:** `{branch}`")
    lines.append("")
    lines.append(f"> {state.request}")
    lines.append("")

    if spec is not None:
        summary = (spec.summary or "").strip()
        if summary:
            lines.append("## Summary")
            lines.append(summary)
            lines.append("")
        if spec.acceptance_criteria:
            lines.append("## Acceptance criteria")
            for ac in spec.acceptance_criteria:
                lines.append(f"- {ac}")
            lines.append("")

    if plan is not None and plan.dag.tasks:
        lines.append(f"## Implementation plan ({len(plan.dag.tasks)} tasks)")
        for task in plan.dag.tasks:
            lines.append(f"- **{task.task_id}** — {task.title}")
        lines.append("")

    if state.tasks:
        artifact_count = sum(len(t.artifacts) for t in state.tasks)
        statuses: dict[str, int] = {}
        for t in state.tasks:
            statuses[t.status.value] = statuses.get(t.status.value, 0) + 1
        status_str = ", ".join(f"{k}={v}" for k, v in sorted(statuses.items()))
        lines.append(f"## Generated code")
        lines.append(f"{len(state.tasks)} tasks ({status_str}), {artifact_count} artifacts written.")
        lines.append("")

    must_fix = [
        i for i in state.triaged_issues
        if i.disposition == IssueDisposition.MUST_FIX_BEFORE_SHIP
    ]
    follow_up = [
        i for i in state.triaged_issues
        if i.disposition == IssueDisposition.CAN_FOLLOW_UP
    ]

    if must_fix:
        lines.append(f"## ❌ Must fix before merge ({len(must_fix)})")
        for issue in must_fix:
            sev = issue.severity.value.upper() if issue.severity else "?"
            lines.append(f"- **[{sev}]** `{issue.id}` — {issue.remediation}")
        lines.append("")

    if follow_up:
        lines.append(f"## 📝 Can follow up ({len(follow_up)})")
        for issue in follow_up:
            sev = issue.severity.value.upper() if issue.severity else "?"
            lines.append(f"- **[{sev}]** `{issue.id}` — {issue.remediation}")
        lines.append("")

    lines.append("---")
    lines.append("Generated by [`swe-forge`](https://pypi.org/project/swe-forge/).")
    return "\n".join(lines)


def _open_pull_request(
    workdir: Path,
    *,
    branch: str,
    title: str,
    body: str,
    base: str = "main",
) -> str | None:
    """Open a PR via ``gh pr create`` and return the PR URL, or None on failure."""
    cwd = str(workdir)
    try:
        # If a PR already exists for this branch, return its URL.
        result = subprocess.run(
            ["gh", "pr", "view", branch, "--json", "url", "-q", ".url"],
            cwd=cwd, capture_output=True, text=True, check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()

        result = subprocess.run(
            ["gh", "pr", "create",
             "--head", branch,
             "--base", base,
             "--title", title,
             "--body", body],
            cwd=cwd, capture_output=True, text=True, check=True,
        )
        url = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else None
        return url
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


# ---------------------------------------------------------------------------
# Main node
# ---------------------------------------------------------------------------


def ship_node(
    state: GraphState,
    *,
    production_mode: bool = True,
    llm: LLMProtocol | None = None,
) -> dict[str, Any]:
    """Execute shipping workflow following ship agent methodology.

    1. Check readiness (blockers, security, approval)
    2. If ready and LLM available:
       a. Determine next version
       b. Generate/update README.md
       c. Generate/update CHANGELOG.md
       d. Update version in pyproject.toml
       e. Commit release artifacts
       f. Create git tag
    3. Return shipping result

    Returns state update with shipping_readiness, shipping_result, and run_status.
    """
    result = check_readiness(state, production_mode=production_mode)

    # Compute blocker stats
    severity_counts: Counter[str] = Counter()
    for blocker in result.blockers:
        severity_counts[blocker.severity.value] += 1

    must_fix_count = sum(
        1
        for issue in state.triaged_issues
        if issue.disposition == IssueDisposition.MUST_FIX_BEFORE_SHIP
        and issue.severity in _BLOCKING_SEVERITIES
    )

    readiness = ShippingReadiness(
        is_ready=result.is_ready,
        blockers=result.blockers,
        blocker_count_by_severity=dict(severity_counts),
        unresolved_must_fix=must_fix_count,
        decision="ship" if result.is_ready else "blocked",
        decision_timestamp=datetime.now(tz=UTC),
    )

    from flowforge.nodes._workspace import get_workdir

    workdir = get_workdir(state)
    current_version = _get_current_version(workdir)
    next_version = (
        _determine_next_version(state, current_version) if result.is_ready else current_version
    )

    committed_files: list[Path] = []
    commit_sha: str | None = None

    # README is regenerated regardless of readiness so the GitHub repo always
    # reflects the latest spec/plan/findings, even on a blocked run.
    if llm is not None:
        readme_content = _generate_readme(state, llm)
        readme_path = _write_readme(readme_content, workdir)
        committed_files.append(readme_path)

        # CHANGELOG only on successful ship — it documents a release.
        if result.is_ready:
            changelog_entry = _generate_changelog_entry(state, next_version, llm)
            changelog_path = _write_changelog(changelog_entry, workdir)
            committed_files.append(changelog_path)

    # Version bump + tag only on successful ship.
    if result.is_ready:
        _update_version_in_pyproject(next_version, workdir)
        if committed_files:
            commit_sha = _commit_release_artifacts(next_version, committed_files, workdir)
        _create_git_tag(next_version, workdir)
    elif committed_files:
        # Blocked: still commit the README refresh so the user can see it.
        commit_sha = _commit_release_artifacts(current_version, committed_files, workdir)

    # Always push the feature branch and (if applicable) open a PR so
    # generated artifacts and findings are reviewable on GitHub even when the
    # run is blocked. The release tag is still gated by readiness.
    branch = _current_branch(workdir)
    pushed = False
    pr_url: str | None = None
    if branch and branch != "main":
        pushed = _push_branch(workdir, branch)
        if pushed:
            status_label = "succeeded" if result.is_ready else "blocked"
            title_prefix = "feat" if result.is_ready else "wip"
            prompt_short = (state.request or "swe-forge run").strip()[:60]
            pr_title = f"{title_prefix}: {prompt_short} [{status_label}]"
            pr_body = _build_pr_body(state, status=status_label, branch=branch)
            pr_url = _open_pull_request(
                workdir, branch=branch, title=pr_title, body=pr_body,
            )
    else:
        # No feature branch (e.g. legacy/local-only) — fall back to direct push.
        pushed = _push_to_remote(workdir)

    if not result.is_ready:
        return {
            "shipping_readiness": readiness,
            "shipping_result": ShippingResult(
                shipped=False,
                commit_sha=commit_sha,
                repo_url=state.repo_url,
                provenance_chain=[
                    f"version:{current_version}",
                    f"readme:{'generated' if llm else 'skipped'}",
                    f"changelog:skipped",
                    f"branch:{branch or 'unknown'}",
                    f"push:{'pushed' if pushed else 'skipped'}",
                    f"pr:{pr_url or 'skipped'}",
                ],
            ),
            "run_status": RunStatus.BLOCKED,
        }

    ship_result = ShippingResult(
        shipped=True,
        ship_timestamp=datetime.now(tz=UTC),
        commit_sha=commit_sha,
        repo_url=state.repo_url,
        provenance_chain=[
            f"version:{next_version}",
            f"previous:{current_version}",
            f"readme:{'generated' if llm else 'skipped'}",
            f"changelog:{'generated' if llm else 'skipped'}",
            f"branch:{branch or 'unknown'}",
            f"push:{'pushed' if pushed else 'skipped'}",
            f"pr:{pr_url or 'skipped'}",
        ],
    )

    return {
        "shipping_readiness": readiness,
        "shipping_result": ship_result,
        "run_status": RunStatus.SUCCEEDED,
    }
