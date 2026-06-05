"""Code review node — five-axis review following code-reviewer agent methodology.

Evaluates changes across five dimensions:
1. Correctness — does the code do what the spec says?
2. Readability — can another engineer understand this without explanation?
3. Architecture — does it fit the system's design?
4. Security — does it introduce vulnerabilities?
5. Performance — does it introduce performance problems?

Produces categorized findings (Critical/Important/Suggestion),
commits review markdown to docs/reviews/, and creates GitHub issues.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Protocol

from flowforge.state.models import Finding, GraphState, IssueSeverity


class LLMProtocol(Protocol):
    """Minimal LLM interface."""

    def invoke(self, prompt: str) -> Any: ...  # noqa: ANN401


def _build_prompt(state: GraphState) -> str:
    """Build code review prompt following code-reviewer agent's five-axis methodology."""
    # Gather artifact details including content
    artifact_sections: list[str] = []
    for task in state.tasks:
        if task.artifacts:
            for art in task.artifacts:
                section = f"### {art.path} ({art.artifact_type})"
                if hasattr(art, "content") and art.content:
                    section += f"\n```\n{art.content}\n```"
                else:
                    section += f"\n  fingerprint: {art.fingerprint}"
                artifact_sections.append(section)

    artifacts_text = "\n\n".join(artifact_sections) if artifact_sections else "(no artifacts to review)"

    # Get spec context if available
    spec_context = ""
    if state.spec:
        spec_context = f"""
## Spec Context
- **Objective**: {state.spec.objective}
- **Acceptance criteria**: {'; '.join(state.spec.acceptance_criteria[:5])}
"""
        if state.spec.security_considerations:
            spec_context += f"- **Security requirements**: {'; '.join(state.spec.security_considerations[:3])}\n"
        if state.spec.boundaries:
            never = state.spec.boundaries.get("never", [])
            if never:
                spec_context += f"- **Never do**: {'; '.join(never[:3])}\n"

    return f"""You are a Staff Engineer conducting a thorough code review. Evaluate the
proposed changes across five dimensions, provide actionable categorized feedback,
and flag anything that should block merge.

## Methodology: Five-Axis Code Review

Evaluate EVERY artifact across these five dimensions:

### 1. Correctness
- Does the code do what the spec says it should?
- Are edge cases handled (null, empty, boundary values, error paths)?
- Are there race conditions, off-by-one errors, or state inconsistencies?
- Do tests actually verify the behavior? Are they testing the right things?

### 2. Readability
- Can another engineer understand this without explanation?
- Are names descriptive and consistent with project conventions?
- Is the control flow straightforward (no deeply nested logic)?
- Could this be done in fewer lines? Are abstractions earning their complexity?

### 3. Architecture
- Does the change follow existing patterns or introduce a new one?
- Are module boundaries maintained? Any circular dependencies?
- Is the abstraction level appropriate (not over-engineered, not too coupled)?
- Dependencies flowing in the right direction?

### 4. Security
- Is user input validated and sanitized at system boundaries?
- Are secrets kept out of code, logs, and version control?
- Is authentication/authorization checked where needed?
- Are queries parameterized? Is output encoded?
- Is data from external sources treated as untrusted?

### 5. Performance
- Any N+1 query patterns or unbounded loops?
- Any synchronous operations that should be async?
- Any missing pagination on list endpoints?
- Any unnecessary re-computation?
{spec_context}
## Artifacts to Review

{artifacts_text}

## Expected Output

Categorize each finding as:
- **critical** — Must fix before merge (security vuln, data loss, broken functionality)
- **high** — Should fix before merge (missing test, wrong abstraction, poor error handling)
- **medium** — Consider for improvement (naming, code style, optimization)
- **low** — Minor suggestion (optional refactoring)
- **info** — Observation, no action needed

Also include what's done well (at least one positive observation).

Respond with a JSON object:

{{
  "verdict": "approve" | "request_changes",
  "summary": "1-2 sentences summarizing the overall assessment",
  "done_well": ["Specific positive observation 1", "..."],
  "findings": [
    {{
      "finding_id": "cr-1",
      "severity": "critical" | "high" | "medium" | "low" | "info",
      "confidence": 0.9,
      "dimension": "correctness" | "readability" | "architecture" | "security" | "performance",
      "title": "Short actionable title",
      "description": "What's wrong and why it matters",
      "file_path": "path/to/file.ext",
      "line_range": [start_line, end_line],
      "suggestion": "Specific code fix recommendation"
    }}
  ]
}}

## Rules
- Every Critical and Important finding MUST include a specific fix recommendation
- Don't approve code with Critical issues
- Be specific: include file paths and line numbers where possible
- Acknowledge what's done well
- If uncertain, say so and suggest investigation rather than guessing

Respond ONLY with the JSON object. No markdown fences, no explanation."""


def _parse_findings(response_content: str, source_node: str) -> tuple[list[Finding], dict[str, Any]]:
    """Parse LLM response into Finding objects and metadata.

    Returns (findings, review_metadata) where metadata includes verdict, summary, done_well.
    """
    # Strip markdown fences if present
    content = response_content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines)

    parsed = json.loads(content)
    findings: list[Finding] = []

    for f in parsed.get("findings", []):
        line_range = None
        if "line_range" in f and f["line_range"] is not None:
            lr = f["line_range"]
            line_range = (int(lr[0]), int(lr[1]))

        findings.append(
            Finding(
                finding_id=f["finding_id"],
                source_node=source_node,
                severity=IssueSeverity(f["severity"]),
                confidence=float(f.get("confidence", 0.5)),
                title=f["title"],
                description=f["description"],
                file_path=f.get("file_path"),
                line_range=line_range,
                suggestion=f.get("suggestion"),
            ),
        )

    metadata = {
        "verdict": parsed.get("verdict", "request_changes"),
        "summary": parsed.get("summary", ""),
        "done_well": parsed.get("done_well", []),
    }

    return findings, metadata


def _render_review_markdown(
    findings: list[Finding], metadata: dict[str, Any]
) -> str:
    """Render review as markdown following code-reviewer agent template."""
    verdict_emoji = "✅ APPROVE" if metadata["verdict"] == "approve" else "❌ REQUEST CHANGES"
    lines: list[str] = []

    lines.append(f"# Code Review\n")
    lines.append(f"## Verdict: {verdict_emoji}\n")
    lines.append(f"**Overview:** {metadata.get('summary', 'Review complete.')}\n")
    lines.append("---\n")

    # Group findings by severity
    critical = [f for f in findings if f.severity == IssueSeverity.CRITICAL]
    high = [f for f in findings if f.severity == IssueSeverity.HIGH]
    medium = [f for f in findings if f.severity == IssueSeverity.MEDIUM]
    low = [f for f in findings if f.severity in (IssueSeverity.LOW, IssueSeverity.INFO)]

    # Critical
    lines.append("## Critical Issues\n")
    if critical:
        for i, f in enumerate(critical, 1):
            lines.append(f"### {i}. {f.title}")
            if f.file_path:
                loc = f"`{f.file_path}"
                if f.line_range:
                    loc += f":{f.line_range[0]}-{f.line_range[1]}"
                loc += "`"
                lines.append(f"- **File:** {loc}")
            lines.append(f"- **Problem:** {f.description}")
            if f.suggestion:
                lines.append(f"- **Fix:** {f.suggestion}")
            lines.append("")
    else:
        lines.append("None.\n")

    # Important (high severity)
    lines.append("## Important Issues\n")
    if high:
        for i, f in enumerate(high, 1):
            lines.append(f"### {i}. {f.title}")
            if f.file_path:
                loc = f"`{f.file_path}"
                if f.line_range:
                    loc += f":{f.line_range[0]}-{f.line_range[1]}"
                loc += "`"
                lines.append(f"- **File:** {loc}")
            lines.append(f"- **Problem:** {f.description}")
            if f.suggestion:
                lines.append(f"- **Fix:** {f.suggestion}")
            lines.append("")
    else:
        lines.append("None.\n")

    # Suggestions (medium/low)
    lines.append("## Suggestions\n")
    suggestions = medium + low
    if suggestions:
        for i, f in enumerate(suggestions, 1):
            lines.append(f"### {i}. {f.title}")
            if f.file_path:
                lines.append(f"- **File:** `{f.file_path}`")
            lines.append(f"- {f.description}")
            if f.suggestion:
                lines.append(f"- **Recommendation:** {f.suggestion}")
            lines.append("")
    else:
        lines.append("None.\n")

    # What's done well
    lines.append("## What's Done Well\n")
    for item in metadata.get("done_well", ["Code follows project conventions."]):
        lines.append(f"- {item}")
    lines.append("")

    # Action items table
    lines.append("## Action Items\n")
    lines.append("| # | Priority | Issue | Target |")
    lines.append("|---|----------|-------|--------|")
    all_findings = critical + high + medium + low
    for i, f in enumerate(all_findings, 1):
        priority = f.severity.value.capitalize()
        target = "hotfix" if f.severity == IssueSeverity.CRITICAL else "backlog"
        lines.append(f"| {i} | {priority} | {f.title} | {target} |")
    lines.append("")

    return "\n".join(lines)


def _commit_review_to_repo(
    findings: list[Finding], metadata: dict[str, Any], state: GraphState
) -> None:
    """Write review markdown to ``<workdir>/docs/reviews/`` and commit to git."""
    from flowforge.nodes._workspace import get_workdir

    workdir = get_workdir(state)
    cwd = str(workdir)

    review_dir = workdir / "docs" / "reviews"
    review_dir.mkdir(parents=True, exist_ok=True)

    # Determine next checkpoint number
    existing = list(review_dir.glob("code-review-checkpoint-*.md"))
    next_num = len(existing) + 1
    review_path = review_dir / f"code-review-checkpoint-{next_num}.md"

    markdown = _render_review_markdown(findings, metadata)
    review_path.write_text(markdown, encoding="utf-8")

    # Git add and commit
    try:
        rel = review_path.relative_to(workdir)
        subprocess.run(
            ["git", "add", str(rel)],
            cwd=cwd, capture_output=True, text=True, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", f"docs: add code review checkpoint {next_num}"],
            cwd=cwd, capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass


def _create_github_issues(findings: list[Finding], state: GraphState) -> None:
    """Create GitHub issues for each finding using gh CLI.

    Labels issues with 'issue-by-code-review'. Skips silently if gh is unavailable.
    Runs from ``state.workdir`` so issues target the project's repo.
    """
    from flowforge.nodes._workspace import get_workdir

    cwd = str(get_workdir(state))

    # Ensure label exists
    try:
        subprocess.run(
            [
                "gh", "label", "create", "issue-by-code-review",
                "--color", "D93F0B",
                "--description", "Issue identified during code review",
            ],
            cwd=cwd, capture_output=True, text=True,
        )
    except FileNotFoundError:
        return  # gh CLI not available

    for finding in findings:
        # Only create issues for actionable findings
        if finding.severity == IssueSeverity.INFO:
            continue

        title = f"[{finding.severity.value.upper()}] {finding.title}"

        body_parts = [
            f"**Source:** code_review_node ({finding.finding_id})",
            f"**Severity:** {finding.severity.value}",
            f"**Confidence:** {finding.confidence:.0%}",
        ]
        if finding.file_path:
            loc = finding.file_path
            if finding.line_range:
                loc += f":{finding.line_range[0]}-{finding.line_range[1]}"
            body_parts.append(f"**File:** `{loc}`")
        body_parts.append(f"\n**Problem:**\n{finding.description}")
        if finding.suggestion:
            body_parts.append(f"\n**Fix:**\n{finding.suggestion}")

        body = "\n".join(body_parts)

        try:
            subprocess.run(
                [
                    "gh", "issue", "create",
                    "--label", "issue-by-code-review",
                    "--title", title,
                    "--body", body,
                ],
                cwd=cwd, capture_output=True, text=True, check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue  # Skip if issue creation fails


def code_review_node(
    state: GraphState,
    *,
    llm: LLMProtocol,
) -> dict[str, Any]:
    """Run five-axis code review on task artifacts.

    Produces structured findings, commits review to docs/reviews/,
    and creates GitHub issues for each finding.
    """
    prompt = _build_prompt(state)
    response = llm.invoke(prompt)

    content = response.content if hasattr(response, "content") else str(response)
    findings, metadata = _parse_findings(content, "code_review_node")

    # Commit review markdown to repo
    _commit_review_to_repo(findings, metadata, state)

    # Create GitHub issues for findings
    _create_github_issues(findings, state)

    return {"review_findings": findings}
