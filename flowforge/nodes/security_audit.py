"""Security audit node — vulnerability detection following security-auditor agent methodology.

Evaluates changes across five security dimensions:
1. Input Handling — injection, XSS, file uploads, URL redirects
2. Authentication & Authorization — sessions, IDOR, rate limiting
3. Data Protection — secrets, PII, encryption
4. Infrastructure — headers, CORS, error messages, least privilege
5. Third-Party Integrations — API keys, webhooks, OAuth

Produces classified findings (Critical/High/Medium/Low/Info),
commits audit report to docs/security-audits/, and creates GitHub issues.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Protocol, cast

from flowforge.config.deep_agents import resolve_deep_agents_enabled
from flowforge.deep_agents import AgentRole
from flowforge.deep_agents.adapters import (
    extract_findings,
    materialize_files,
)
from flowforge.deep_agents.factory import build_deep_agent, run_deep_agent_bounded
from flowforge.nodes._workspace import get_workdir
from flowforge.state.models import (
    DeepAgentTrace,
    Finding,
    GraphState,
    IssueSeverity,
    ToolInvocationRecord,
)

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel


class LLMProtocol(Protocol):
    """Minimal LLM interface."""

    def invoke(self, prompt: str) -> Any: ...  # noqa: ANN401


_PROMPT_ARTIFACT_CHAR_LIMIT: Final[int] = 3_000
"""Per-artifact content limit when embedding in a prompt string."""


def _build_prompt(state: GraphState) -> str:
    """Build security audit prompt following security-auditor agent methodology."""
    # Gather artifact details including content
    artifact_sections: list[str] = []
    for task in state.tasks:
        if task.artifacts:
            for art in task.artifacts:
                section = f"### {art.path} ({art.artifact_type})"
                if hasattr(art, "content") and art.content:
                    body = art.content
                    if len(body) > _PROMPT_ARTIFACT_CHAR_LIMIT:
                        body = body[:_PROMPT_ARTIFACT_CHAR_LIMIT] + f"\n... [{len(art.content) - _PROMPT_ARTIFACT_CHAR_LIMIT} chars truncated]"
                    section += f"\n```\n{body}\n```"
                else:
                    section += f"\n  fingerprint: {art.fingerprint}"
                artifact_sections.append(section)

    artifacts_text = "\n\n".join(artifact_sections) if artifact_sections else "(no artifacts to audit)"

    # Get spec security context if available
    security_context = ""
    if state.spec:
        if state.spec.security_considerations:
            security_context = f"""
## Spec Security Requirements
{chr(10).join(f'- {s}' for s in state.spec.security_considerations)}
"""
        if state.spec.boundaries:
            never = state.spec.boundaries.get("never", [])
            if never:
                security_context += f"\n## Hard Prohibitions\n{chr(10).join(f'- {n}' for n in never)}\n"

    # Prior audit awareness
    prior_audits_section = ""
    workdir = get_workdir(state) if state.workdir else None
    if workdir is not None:
        audit_dir = workdir / "docs" / "security-audits"
        if audit_dir.is_dir():
            prior = sorted(audit_dir.glob("security-audit-*.md"))
            if prior:
                names = ", ".join(p.name for p in prior[-5:])
                prior_audits_section = (
                    f"\n## Prior Audit Reports\n"
                    f"The repo contains {len(prior)} previous audit(s): {names}. "
                    f"Read the most recent one(s) and re-flag any unresolved Critical/High items "
                    f"that still apply.\n"
                )

    return f"""You are an experienced Security Engineer conducting a security audit. Focus on
practical, exploitable vulnerabilities rather than theoretical risks. Your goal is to
identify real attack vectors and provide specific, actionable fixes.

## Process

1. **Gather context first.** Read the spec, the artifacts, and any prior
   audit reports listed below. If you have tools available
   (`git_diff`, `git_status`, `web_search`, `read_file`, `list_files`),
   use them to inspect the change set, scan for secret patterns
   (`gh[ops]_`, `sk-`, `AKIA`, `-----BEGIN.*PRIVATE KEY-----`), and
   verify dependency hygiene before writing findings.
2. **Map every finding to OWASP A01–A10** by populating `owasp_category`.
   If a finding doesn't fit a Top-10 category, use `"none"`.
3. **Be exploit-focused.** Theoretical risks belong in `info`. Critical
   and High findings must include a concrete proof-of-concept (request,
   payload, or attack chain).

## Methodology: Five-Dimension Security Audit

Evaluate ALL artifacts across these five dimensions:

### 1. Input Handling
- Is all user input validated at system boundaries?
- Are there injection vectors (SQL, NoSQL, OS command, path traversal)?
- Is HTML output encoded to prevent XSS?
- Are file uploads restricted by type, size, and content?
- Are URL redirects validated against an allowlist?
- Is deserialization of untrusted data avoided?

### 2. Authentication & Authorization
- Are credentials hashed with strong algorithms (bcrypt, scrypt, argon2)?
- Are sessions managed securely (httpOnly, secure, sameSite)?
- Is authorization checked on every protected endpoint?
- Can users access resources belonging to other users (IDOR)?
- Are tokens time-limited and single-use where appropriate?
- Is rate limiting applied to authentication endpoints?

### 3. Data Protection
- Are secrets in environment variables (not hardcoded in source)?
- Are sensitive fields excluded from API responses and logs?
- Are `console.log` / `print` / logger calls scrubbed of tokens, PII, headers?
- Is data encrypted in transit (HTTPS/TLS)?
- Is PII minimized and handled per regulations?
- Are credentials or tokens stored with restricted file permissions?
- Does `.gitignore` cover `.env`, key files, and credential dirs?

### 4. Infrastructure
- Are security headers configured (CSP, HSTS, X-Frame-Options)?
- Is CORS restricted to specific origins (not wildcard)?
- Are error messages generic (no stack traces to users)?
- Is the principle of least privilege applied?
- Are debug modes disabled in production?

### 5. Third-Party Integrations
- Are API keys and tokens stored securely?
- Are webhook payloads verified (signature validation)?
- Are OAuth flows using state parameters and PKCE?
- Are third-party dependencies audited for known CVEs (`npm audit`,
  `pip-audit`)? Surface unreachable vulnerable code as `info`, but
  always flag exploitable paths as Critical/High.

## OWASP Top 10 Checklist (minimum baseline)
- A01: Broken Access Control
- A02: Cryptographic Failures
- A03: Injection
- A04: Insecure Design
- A05: Security Misconfiguration
- A06: Vulnerable & Outdated Components
- A07: Identification & Authentication Failures
- A08: Software & Data Integrity Failures
- A09: Security Logging & Monitoring Failures
- A10: SSRF

## Severity → Action Timeline
- **critical** — Exploitable remotely, leads to data breach or full
  compromise. Required action: **Fix immediately, block release.**
- **high** — Exploitable with some conditions, significant data
  exposure. Required action: **Fix before release.**
- **medium** — Limited impact or requires authenticated access.
  Required action: **Fix in current sprint.**
- **low** — Theoretical risk or defense-in-depth improvement. Required
  action: **Schedule for next sprint.**
- **info** — Best practice recommendation, no current risk.
{security_context}{prior_audits_section}
## Artifacts to Audit

{artifacts_text}

## Expected Output

Respond with a JSON object:

{{
  "summary": "Overall security posture assessment (1-2 sentences)",
  "positive_observations": ["Security practices done well — at least one"],
  "findings": [
    {{
      "finding_id": "sec-1",
      "severity": "critical" | "high" | "medium" | "low" | "info",
      "confidence": 0.9,
      "dimension": "input_handling" | "auth" | "data_protection" | "infrastructure" | "third_party",
      "owasp_category": "A01" | "A02" | "A03" | "A04" | "A05" | "A06" | "A07" | "A08" | "A09" | "A10" | "none",
      "title": "Short actionable title",
      "description": "What the vulnerability is and why it matters",
      "impact": "What an attacker could do if this is exploited",
      "file_path": "path/to/file.ext",
      "line_range": [start_line, end_line],
      "proof_of_concept": "How to exploit this (REQUIRED for critical/high)",
      "suggestion": "Specific fix with code example",
      "required_action": "Fix immediately | Fix before release | Fix in current sprint | Schedule for next sprint"
    }}
  ]
}}

## Rules
- Focus on EXPLOITABLE vulnerabilities, not theoretical risks
- Every finding MUST include a specific, actionable fix recommendation
- Critical/High findings MUST include a concrete proof_of_concept
- Every finding MUST set `owasp_category` (use `"none"` only if truly out of scope)
- Acknowledge good security practices (positive_observations) — at least one
- Never suggest disabling security controls as a "fix"

Respond ONLY with the JSON object. No markdown fences, no explanation."""


def _parse_findings(response_content: str) -> tuple[list[Finding], dict[str, Any]]:
    """Parse LLM response into Finding objects and audit metadata."""
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
                source_node="security_audit_node",
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
        "summary": parsed.get("summary", ""),
        "positive_observations": parsed.get("positive_observations", []),
    }

    return findings, metadata


def _render_audit_markdown(findings: list[Finding], metadata: dict[str, Any]) -> str:
    """Render audit report as markdown following security-auditor agent template."""
    lines: list[str] = []

    lines.append("# Security Audit Report\n")
    lines.append(f"> **Auditor:** Security Auditor Agent (Security Engineer)")
    lines.append(f"> **Scope:** Automated security audit of task artifacts\n")
    lines.append("---\n")

    # Summary table
    lines.append("## Summary\n")
    if metadata.get("summary"):
        lines.append(f"{metadata['summary']}\n")

    severity_counts = {s: 0 for s in ["critical", "high", "medium", "low", "info"]}
    for f in findings:
        severity_counts[f.severity.value] = severity_counts.get(f.severity.value, 0) + 1

    lines.append("| Severity | Count |")
    lines.append("|----------|-------|")
    for sev, count in severity_counts.items():
        lines.append(f"| {sev.capitalize()} | {count} |")
    lines.append("\n---\n")

    # Findings
    lines.append("## Findings\n")
    if not findings:
        lines.append("No security issues found.\n")
    else:
        for f in findings:
            prefix = f"[{f.severity.value.upper()}-{f.finding_id}]"
            lines.append(f"### {prefix} {f.title}\n")
            if f.file_path:
                loc = f"`{f.file_path}"
                if f.line_range:
                    loc += f":{f.line_range[0]}-{f.line_range[1]}"
                loc += "`"
                lines.append(f"- **Location:** {loc}")
            lines.append(f"- **Description:** {f.description}")
            lines.append(f"- **Confidence:** {f.confidence:.0%}")
            if f.suggestion:
                lines.append(f"- **Recommendation:** {f.suggestion}")
            lines.append("")

    # Positive observations
    lines.append("---\n")
    lines.append("## Positive Observations\n")
    for obs in metadata.get("positive_observations", ["Code follows basic security practices."]):
        lines.append(f"- ✅ {obs}")
    lines.append("")

    # Action items
    lines.append("---\n")
    lines.append("## Action Items (Priority Order)\n")
    lines.append("| # | Severity | Finding | Recommendation |")
    lines.append("|---|----------|---------|----------------|")
    actionable = [f for f in findings if f.severity != IssueSeverity.INFO]
    for i, f in enumerate(actionable, 1):
        suggestion = (f.suggestion or "See finding details")[:60]
        lines.append(f"| {i} | {f.severity.value.capitalize()} | {f.title} | {suggestion} |")
    lines.append("")

    return "\n".join(lines)


def _commit_audit_to_repo(
    findings: list[Finding], metadata: dict[str, Any], state: GraphState
) -> None:
    """Write audit report to ``<workdir>/docs/security-audits/`` and commit to git."""
    from flowforge.nodes._workspace import get_workdir

    workdir = get_workdir(state)
    cwd = str(workdir)

    audit_dir = workdir / "docs" / "security-audits"
    audit_dir.mkdir(parents=True, exist_ok=True)

    # Determine next audit number
    existing = list(audit_dir.glob("security-audit-*.md"))
    next_num = len(existing) + 1
    audit_path = audit_dir / f"security-audit-{next_num}.md"

    markdown = _render_audit_markdown(findings, metadata)
    audit_path.write_text(markdown, encoding="utf-8")

    # Git add and commit
    try:
        rel = audit_path.relative_to(workdir)
        subprocess.run(
            ["git", "add", str(rel)],
            cwd=cwd, capture_output=True, text=True, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", f"docs: add security audit report #{next_num}"],
            cwd=cwd, capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass


def _create_github_issues(findings: list[Finding], state: GraphState) -> None:
    """Create GitHub issues for each finding using gh CLI.

    Labels issues with 'security' and 'issue-by-code-review'.
    Skips silently if gh is unavailable.
    Runs from ``state.workdir`` so issues target the project's repo.
    """
    from flowforge.nodes._workspace import get_workdir

    cwd = str(get_workdir(state))

    # Ensure labels exist
    try:
        subprocess.run(
            ["gh", "label", "create", "security",
             "--color", "B60205",
             "--description", "Security vulnerability or hardening"],
            cwd=cwd, capture_output=True, text=True,
        )
        subprocess.run(
            ["gh", "label", "create", "issue-by-code-review",
             "--color", "D93F0B",
             "--description", "Issue identified during code review"],
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
            f"**Source:** security_audit_node ({finding.finding_id})",
            f"**Severity:** {finding.severity.value.upper()}",
            f"**Confidence:** {finding.confidence:.0%}",
        ]
        if finding.file_path:
            loc = finding.file_path
            if finding.line_range:
                loc += f":{finding.line_range[0]}-{finding.line_range[1]}"
            body_parts.append(f"**Location:** `{loc}`")
        body_parts.append(f"\n**Problem:**\n{finding.description}")
        if finding.suggestion:
            body_parts.append(f"\n**Fix:**\n{finding.suggestion}")

        # Timeline based on severity
        timeline_map = {
            IssueSeverity.CRITICAL: "Fix immediately, block release",
            IssueSeverity.HIGH: "Fix before release",
            IssueSeverity.MEDIUM: "Fix in current sprint",
            IssueSeverity.LOW: "Schedule for next sprint",
        }
        timeline = timeline_map.get(finding.severity, "")
        if timeline:
            body_parts.append(f"\n**Required Action:** {timeline}")

        body = "\n".join(body_parts)

        try:
            subprocess.run(
                [
                    "gh", "issue", "create",
                    "--label", "security",
                    "--label", "issue-by-code-review",
                    "--title", title,
                    "--body", body,
                ],
                cwd=cwd, capture_output=True, text=True, check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue


def security_audit_node(
    state: GraphState,
    *,
    llm: LLMProtocol,
) -> dict[str, Any]:
    """Run security audit on task artifacts following security-auditor methodology.

    Produces classified findings, commits audit report to docs/security-audits/,
    and creates GitHub issues with 'security' label for each finding. When the
    ``FLOWFORGE_DEEP_AGENTS`` flag is enabled, dispatches through the Deep
    Agent factory; otherwise runs the legacy single-shot path.
    """
    if resolve_deep_agents_enabled():
        return _run_via_deep_agent(state, llm)

    prompt = _build_prompt(state)
    response = llm.invoke(prompt)

    content = response.content if hasattr(response, "content") else str(response)
    findings, metadata = _parse_findings(content)

    # Commit audit report to repo
    _commit_audit_to_repo(findings, metadata, state)

    # Create GitHub issues for findings
    _create_github_issues(findings, state)

    return {"security_findings": findings}


def _run_via_deep_agent(state: GraphState, llm: LLMProtocol) -> dict[str, Any]:
    """Deep Agent variant of ``security_audit_node`` (T7)."""
    workdir = get_workdir(state)
    files = materialize_files(state)
    graph = build_deep_agent(
        role=AgentRole.AUDITOR,
        llm=cast("BaseChatModel", llm),
        workdir=workdir,
    )
    rubric = _build_prompt(state)
    user_message = (
        "Conduct a five-dimension security audit using the rubric below."
        " Use your tools (`git_diff`, `web_search`, `read_file`,"
        " `list_files`) to gather evidence — inspect the diff, scan for"
        " secret patterns, and check dependency hygiene before writing"
        " findings. After producing the JSON, also save it verbatim to"
        " vfs:/findings/security.json and write a markdown report at"
        " vfs:/docs/security-audits/security-audit.md.\n\n"
        + rubric
    )
    payload: dict[str, Any] = {
        "messages": [{"role": "user", "content": user_message}],
        "files": files,
    }
    invocations: list[ToolInvocationRecord] = []
    result = run_deep_agent_bounded(
        graph,
        payload,
        role=AgentRole.AUDITOR,
        node_name="security_audit_node",
        invocation_sink=invocations,
    )

    findings = [
        f.model_copy(update={"source_node": "security_audit_node"})
        for f in extract_findings(result)
    ]

    # Recover summary / positive_observations from the agent's final
    # assistant message; fall back to defaults if not parseable.
    metadata: dict[str, Any] = {"summary": "", "positive_observations": []}
    raw_messages = result.get("messages")
    messages: list[dict[str, object]] = (
        [m for m in raw_messages if isinstance(m, dict)]
        if isinstance(raw_messages, list) else []
    )
    for m in reversed(messages):
        content = m.get("content")
        if not isinstance(content, str):
            continue
        try:
            _, parsed_meta = _parse_findings(content)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        metadata.update(parsed_meta)
        break

    raw_files = result.get("files")
    vfs_keys: list[str] = (
        sorted(k for k in raw_files if isinstance(k, str))
        if isinstance(raw_files, dict) else []
    )
    trace = DeepAgentTrace(
        role=AgentRole.AUDITOR,
        messages_digest=DeepAgentTrace.digest_messages(messages),
        vfs_keys=vfs_keys,
        tool_invocations=invocations,
    )

    _commit_audit_to_repo(findings, metadata, state)
    _create_github_issues(findings, state)

    return {
        "security_findings": findings,
        "deep_agent_traces": {"security_audit_node": trace},
    }

