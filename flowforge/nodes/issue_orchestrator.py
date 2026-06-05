"""Issue orchestrator node — batch triage following issue-orchestrator agent methodology.

Merges findings from all parallel quality gate nodes (code_review, security_audit,
test_engineer), deduplicates by content fingerprint, classifies into 6 categories,
prioritizes by severity tier, triages via LLM into dispositions, creates GitHub
issues for actionable findings, closes rejected ones, and commits a triage report.

Categories (per agent spec):
- Security: XSS, injection, token exposure, auth bypass
- Bug: crashes, silent errors, missing handlers
- Code Quality: refactors, type improvements, validation gaps
- Testing: missing coverage, test infrastructure
- Documentation: missing docs, outdated references
- Dependency: version bumps, missing deps
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Protocol

from flowforge.state.models import (
    Finding,
    GraphState,
    Issue,
    IssueDisposition,
    IssueSeverity,
)


# ---------------------------------------------------------------------------
# Priority tiers (lower number = higher priority)
# ---------------------------------------------------------------------------

_PRIORITY_ORDER: dict[str, int] = {
    "critical_security": 1,
    "medium_security": 2,
    "bug": 3,
    "code_quality": 4,
    "testing": 5,
    "documentation": 6,
    "dependency": 7,
}


class LLMProtocol(Protocol):
    """Minimal LLM interface."""

    def invoke(self, prompt: str) -> Any: ...  # noqa: ANN401


def merge_findings(
    review: list[Finding],
    security: list[Finding],
    test: list[Finding],
) -> list[Finding]:
    """Merge findings from all quality gate nodes into a single list."""
    return list(review) + list(security) + list(test)


def compute_fingerprint(finding: Finding) -> str:
    """Compute a content-based fingerprint for deduplication.

    Fingerprint is based on title, file_path, and line_range —
    intentionally excludes finding_id and source_node so that
    the same issue found by different nodes deduplicates.
    """
    content = f"{finding.title}|{finding.file_path}|{finding.line_range}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _deduplicate(findings: list[Finding]) -> dict[str, Finding]:
    """Deduplicate findings by fingerprint, keeping first occurrence."""
    seen: dict[str, Finding] = {}
    for f in findings:
        fp = compute_fingerprint(f)
        if fp not in seen:
            seen[fp] = f
    return seen


def _classify_category(finding: Finding) -> str:
    """Classify finding into one of 6 categories per agent methodology."""
    title_lower = (finding.title or "").lower()
    desc_lower = (finding.description or "").lower()
    combined = f"{title_lower} {desc_lower}"

    security_signals = [
        "xss", "injection", "command injection", "sql injection",
        "token", "auth", "authentication", "pkce", "csrf",
        "exposure", "vulnerability", "cve", "owasp",
    ]
    bug_signals = [
        "crash", "hang", "timeout", "flaky", "error", "exception",
        "missing handler", "null", "undefined", "race condition",
    ]
    test_signals = [
        "test", "coverage", "missing test", "untested", "spec",
        "assertion", "mock",
    ]
    doc_signals = [
        "document", "readme", "comment", "jsdoc", "docstring",
        "outdated", "stale",
    ]
    dep_signals = [
        "dependency", "version", "bump", "upgrade", "deprecated",
        "package",
    ]

    if finding.source_node == "security_audit_node" or any(s in combined for s in security_signals):
        if finding.severity in (IssueSeverity.CRITICAL, IssueSeverity.HIGH):
            return "critical_security"
        return "medium_security"
    if any(s in combined for s in bug_signals):
        return "bug"
    if finding.source_node == "test_engineer_node" or any(s in combined for s in test_signals):
        return "testing"
    if any(s in combined for s in doc_signals):
        return "documentation"
    if any(s in combined for s in dep_signals):
        return "dependency"
    return "code_quality"


def _prioritize(deduped: dict[str, Finding]) -> list[tuple[str, Finding, str]]:
    """Sort deduplicated findings by priority tier.

    Returns list of (fingerprint, finding, category) sorted by priority.
    """
    categorized: list[tuple[str, Finding, str]] = []
    for fp, finding in deduped.items():
        category = _classify_category(finding)
        categorized.append((fp, finding, category))

    categorized.sort(key=lambda x: _PRIORITY_ORDER.get(x[2], 99))
    return categorized


def _build_prompt(prioritized: list[tuple[str, Finding, str]]) -> str:
    """Build triage prompt for LLM following agent methodology."""
    finding_lines: list[str] = []
    for fp, f, category in prioritized:
        finding_lines.append(
            f"- fingerprint={fp} category={category} severity={f.severity} "
            f"title={f.title} description={f.description} file={f.file_path} "
            f"source={f.source_node}",
        )

    findings_text = "\n".join(finding_lines)

    return (
        "You are an issue triage orchestrator. Your job is to classify each finding "
        "and determine the correct disposition.\n\n"
        "## Classification Categories\n"
        "- critical_security: blocks shipping — injection, XSS, auth bypass\n"
        "- medium_security: track with urgency — binding, uncapped retries, missing timeouts\n"
        "- bug: crashes, silent errors, flaky tests, missing handlers\n"
        "- code_quality: refactors, type improvements, validation gaps\n"
        "- testing: missing coverage, test infrastructure\n"
        "- documentation: missing docs, outdated references\n"
        "- dependency: version bumps, missing deps\n\n"
        "## Dispositions\n"
        "- must_fix_before_ship: blocks release (all critical_security, confirmed bugs)\n"
        "- can_follow_up: track but don't block (medium issues, enhancements)\n"
        "- rejected: false positive, not actionable, or already addressed\n\n"
        "## Priority (for SLA assignment)\n"
        "1. Critical security → immediate\n"
        "2. Medium security → 24h\n"
        "3. Bugs → 48h\n"
        "4. Code quality → next-sprint\n"
        "5. Testing → next-sprint\n"
        "6. Documentation/Dependency → backlog\n\n"
        f"## Findings (pre-sorted by priority)\n{findings_text}\n\n"
        "## Response Format\n"
        "Respond with JSON:\n"
        '{"issues": [{"fingerprint": "...", "disposition": "must_fix_before_ship|can_follow_up|rejected", '
        '"category": "...", "remediation": "concise fix description", '
        '"owner": "sub-agent-name|null", "sla_target": "immediate|24h|48h|next-sprint|backlog|null"}]}'
    )


def _parse_issues(
    response_content: str,
    deduped: dict[str, Finding],
) -> list[Issue]:
    """Parse LLM triage response into Issue objects."""
    # Extract JSON from potential markdown fences
    content = response_content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        lines = lines[1:]  # Remove opening fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines)

    parsed = json.loads(content)
    issues: list[Issue] = []

    for item in parsed.get("issues", []):
        fp = item["fingerprint"]
        finding = deduped.get(fp)
        if finding is None:
            continue

        issues.append(
            Issue(
                id=f"issue-{fp[:8]}",
                source_node=finding.source_node,
                fingerprint=fp,
                severity=finding.severity,
                confidence=finding.confidence,
                disposition=IssueDisposition(item["disposition"]),
                remediation=item["remediation"],
                owner=item.get("owner"),
                sla_target=item.get("sla_target"),
            ),
        )

    return issues


def _build_tool_operations(issues: list[Issue]) -> list[dict[str, Any]]:
    """Build MCP tool operations for issue tracker integration."""
    operations: list[dict[str, Any]] = []
    for issue in issues:
        if issue.disposition == IssueDisposition.REJECTED:
            operations.append(
                {
                    "tool": "close_issue",
                    "args": {
                        "id": issue.id,
                        "reason": "rejected — not actionable",
                    },
                },
            )
        else:
            operations.append(
                {
                    "tool": "create_issue",
                    "args": {
                        "id": issue.id,
                        "title": f"[{issue.severity.value.upper()}] {issue.fingerprint[:8]}",
                        "disposition": issue.disposition.value,
                        "category": _classify_category(
                            Finding(
                                finding_id=issue.id,
                                source_node=issue.source_node,
                                severity=issue.severity,
                                confidence=issue.confidence,
                                title=issue.fingerprint,
                                description=issue.remediation,
                            ),
                        ),
                        "remediation": issue.remediation,
                        "owner": issue.owner,
                        "sla_target": issue.sla_target,
                    },
                },
            )
    return operations


def _render_triage_markdown(
    issues: list[Issue],
    deduped: dict[str, Finding],
    prioritized: list[tuple[str, Finding, str]],
) -> str:
    """Render triage report as markdown."""
    lines: list[str] = [
        "# Issue Triage Report",
        "",
        "## Summary",
        "",
        f"- **Total findings (pre-dedup):** merged from quality gates",
        f"- **Unique findings:** {len(deduped)}",
        f"- **Issues triaged:** {len(issues)}",
        "",
    ]

    # Count by disposition
    must_fix = sum(1 for i in issues if i.disposition == IssueDisposition.MUST_FIX_BEFORE_SHIP)
    follow_up = sum(1 for i in issues if i.disposition == IssueDisposition.CAN_FOLLOW_UP)
    rejected = sum(1 for i in issues if i.disposition == IssueDisposition.REJECTED)

    lines.extend([
        "## Disposition Breakdown",
        "",
        f"| Disposition | Count |",
        f"|-------------|-------|",
        f"| Must Fix Before Ship | {must_fix} |",
        f"| Can Follow Up | {follow_up} |",
        f"| Rejected | {rejected} |",
        "",
        "## Issues by Priority",
        "",
    ])

    # Group by category
    category_issues: dict[str, list[Issue]] = {}
    for issue in issues:
        # Re-derive category from finding
        finding = deduped.get(issue.fingerprint)
        if finding:
            cat = _classify_category(finding)
        else:
            cat = "code_quality"
        category_issues.setdefault(cat, []).append(issue)

    for cat in sorted(category_issues.keys(), key=lambda c: _PRIORITY_ORDER.get(c, 99)):
        lines.append(f"### {cat.replace('_', ' ').title()}")
        lines.append("")
        for issue in category_issues[cat]:
            finding = deduped.get(issue.fingerprint)
            title = finding.title if finding else issue.fingerprint[:8]
            disposition_badge = {
                IssueDisposition.MUST_FIX_BEFORE_SHIP: "🔴 MUST FIX",
                IssueDisposition.CAN_FOLLOW_UP: "🟡 FOLLOW UP",
                IssueDisposition.REJECTED: "⚪ REJECTED",
            }.get(issue.disposition, issue.disposition.value)
            lines.append(
                f"- **{disposition_badge}** [{issue.severity.value.upper()}] {title}",
            )
            lines.append(f"  - Remediation: {issue.remediation}")
            if issue.owner:
                lines.append(f"  - Owner: {issue.owner}")
            if issue.sla_target:
                lines.append(f"  - SLA: {issue.sla_target}")
            lines.append("")

    return "\n".join(lines)


def _commit_triage_to_repo(
    issues: list[Issue],
    deduped: dict[str, Finding],
    prioritized: list[tuple[str, Finding, str]],
    state: GraphState,
) -> None:
    """Commit triage report markdown to ``<workdir>/docs/triage/``."""
    from flowforge.nodes._workspace import get_workdir

    workdir = get_workdir(state)
    cwd = str(workdir)

    docs_dir = workdir / "docs" / "triage"
    docs_dir.mkdir(parents=True, exist_ok=True)

    existing = list(docs_dir.glob("triage-report-*.md"))
    next_num = len(existing) + 1
    filename = f"triage-report-{next_num}.md"
    filepath = docs_dir / filename

    content = _render_triage_markdown(issues, deduped, prioritized)
    filepath.write_text(content)

    try:
        rel = filepath.relative_to(workdir)
        subprocess.run(["git", "add", str(rel)], cwd=cwd, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", f"docs: add triage report #{next_num}"],
            cwd=cwd, capture_output=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass  # Skip if git not available or commit fails


def _create_github_issues(
    issues: list[Issue], deduped: dict[str, Finding], state: GraphState
) -> None:
    """Create GitHub issues for actionable findings, close rejected ones.

    Uses gh CLI. Labels with 'issue-by-orchestrator' + category-specific labels.
    Runs from ``state.workdir`` so issues target the project's repo.
    """
    from flowforge.nodes._workspace import get_workdir

    cwd = str(get_workdir(state))

    # Ensure labels exist
    label_configs = [
        ("issue-by-orchestrator", "5319E7", "Issue triaged by orchestrator"),
        ("priority-critical", "B60205", "Critical priority — blocks shipping"),
        ("priority-high", "D93F0B", "High priority"),
        ("priority-medium", "FBCA04", "Medium priority"),
        ("priority-low", "0E8A16", "Low priority"),
    ]
    for label_name, color, desc in label_configs:
        try:
            subprocess.run(
                ["gh", "label", "create", label_name, "--color", color, "--description", desc],
                cwd=cwd, capture_output=True, text=True,
            )
        except FileNotFoundError:
            return  # gh CLI not available

    for issue in issues:
        finding = deduped.get(issue.fingerprint)
        if not finding:
            continue

        if issue.disposition == IssueDisposition.REJECTED:
            # Close any existing issue matching this fingerprint
            _close_matching_issues(issue, finding, cwd)
            continue

        # Skip INFO-level findings
        if issue.severity == IssueSeverity.INFO:
            continue

        category = _classify_category(finding)
        title = f"[{issue.severity.value.upper()}] {finding.title}"

        # Build labels
        labels = ["issue-by-orchestrator"]
        if issue.severity == IssueSeverity.CRITICAL:
            labels.append("priority-critical")
        elif issue.severity == IssueSeverity.HIGH:
            labels.append("priority-high")
        elif issue.severity == IssueSeverity.MEDIUM:
            labels.append("priority-medium")
        else:
            labels.append("priority-low")

        # Category-specific labels
        cat_label_map = {
            "critical_security": "security",
            "medium_security": "security",
            "bug": "bug",
            "code_quality": "code-quality",
            "testing": "testing",
            "documentation": "documentation",
            "dependency": "dependencies",
        }
        if category in cat_label_map:
            labels.append(cat_label_map[category])

        body_parts = [
            f"**Source:** {finding.source_node} → orchestrator triage",
            f"**Category:** {category.replace('_', ' ').title()}",
            f"**Severity:** {issue.severity.value}",
            f"**Confidence:** {finding.confidence:.0%}",
            f"**Disposition:** {issue.disposition.value}",
        ]
        if issue.sla_target:
            body_parts.append(f"**SLA:** {issue.sla_target}")
        if issue.owner:
            body_parts.append(f"**Owner:** {issue.owner}")
        if finding.file_path:
            loc = finding.file_path
            if finding.line_range:
                loc += f":{finding.line_range[0]}-{finding.line_range[1]}"
            body_parts.append(f"**File:** `{loc}`")
        body_parts.append(f"\n**Problem:**\n{finding.description}")
        body_parts.append(f"\n**Remediation:**\n{issue.remediation}")

        body = "\n".join(body_parts)

        try:
            cmd = ["gh", "issue", "create", "--title", title, "--body", body]
            for label in labels:
                cmd.extend(["--label", label])
            subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue


def _close_matching_issues(issue: Issue, finding: Finding, cwd: str) -> None:
    """Close GitHub issues that match a rejected finding."""
    try:
        # Search for open issues with matching fingerprint in title
        result = subprocess.run(
            [
                "gh", "issue", "list",
                "--label", "issue-by-orchestrator",
                "--state", "open",
                "--search", finding.title,
                "--json", "number",
            ],
            cwd=cwd, capture_output=True, text=True, check=True,
        )
        issues_data = json.loads(result.stdout)
        for gh_issue in issues_data:
            subprocess.run(
                [
                    "gh", "issue", "close", str(gh_issue["number"]),
                    "--reason", "not planned",
                    "--comment", f"Closed by orchestrator triage: {issue.remediation}",
                ],
                cwd=cwd, capture_output=True, text=True, check=True,
            )
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError):
        pass  # Skip silently if gh unavailable or parsing fails


def issue_orchestrator_node(
    state: GraphState,
    *,
    llm: LLMProtocol,
) -> dict[str, Any]:
    """Triage findings following issue-orchestrator agent methodology.

    1. Merges findings from all parallel quality gate nodes
    2. Deduplicates by content fingerprint
    3. Classifies into 6 categories (security, bug, code-quality, testing, docs, deps)
    4. Prioritizes by severity tier
    5. Triages via LLM into dispositions
    6. Creates GitHub issues for actionable findings
    7. Closes rejected findings
    8. Commits triage report to docs/triage/
    """
    all_findings = merge_findings(
        state.review_findings,
        state.security_findings,
        state.test_findings,
    )

    deduped = _deduplicate(all_findings)

    if not deduped:
        return {"triaged_issues": [], "tool_operations": []}

    prioritized = _prioritize(deduped)
    prompt = _build_prompt(prioritized)
    response = llm.invoke(prompt)

    content = response.content if hasattr(response, "content") else str(response)
    issues = _parse_issues(content, deduped)
    tool_operations = _build_tool_operations(issues)

    # Commit triage report to repo
    _commit_triage_to_repo(issues, deduped, prioritized, state)

    # Create/close GitHub issues
    _create_github_issues(issues, deduped, state)

    return {
        "triaged_issues": issues,
        "tool_operations": tool_operations,
    }
