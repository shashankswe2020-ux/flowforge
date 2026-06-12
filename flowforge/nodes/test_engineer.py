"""Test engineer node — QA analysis following test-engineer agent methodology.

Evaluates test quality across:
1. Coverage gaps — untested functions, missing edge cases
2. Test levels — right test at the right level (unit > integration > e2e)
3. Test quality — independence, single-concept, behavior-focused
4. Prove-It pattern — bugs need failing tests before fixes
5. Scenario coverage — happy path, empty, boundary, errors, concurrency

Produces findings, proposes additional test tasks,
commits test report to docs/test-reports/, and creates GitHub issues.
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
    CapabilityType,
    DeepAgentTrace,
    Finding,
    GraphState,
    IssueSeverity,
    TaskDefinition,
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
    """Build test engineer prompt following test-engineer agent methodology."""
    # Gather artifact details including content
    source_artifacts: list[str] = []
    test_artifacts: list[str] = []
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
                if art.artifact_type == "test":
                    test_artifacts.append(section)
                else:
                    source_artifacts.append(section)

    source_text = "\n\n".join(source_artifacts) if source_artifacts else "(no source artifacts)"
    test_text = "\n\n".join(test_artifacts) if test_artifacts else "(no test artifacts)"

    # Get spec testing strategy if available
    testing_context = ""
    if state.spec:
        if state.spec.testing_strategy:
            testing_context = f"""
## Spec Testing Requirements
{chr(10).join(f'- {t}' for t in state.spec.testing_strategy)}
"""
        if state.spec.acceptance_criteria:
            testing_context += f"""
## Acceptance Criteria (must be tested)
{chr(10).join(f'- {c}' for c in state.spec.acceptance_criteria[:8])}
"""

    # Prior test report awareness
    prior_reports_section = ""
    workdir = get_workdir(state) if state.workdir else None
    if workdir is not None:
        report_dir = workdir / "docs" / "test-reports"
        if report_dir.is_dir():
            prior = sorted(report_dir.glob("test-report-*.md"))
            if prior:
                names = ", ".join(p.name for p in prior[-5:])
                prior_reports_section = (
                    f"\n## Prior Test Reports\n"
                    f"The repo contains {len(prior)} previous report(s): {names}. "
                    f"Read the most recent and re-flag any unresolved Critical/High coverage gaps "
                    f"that still apply.\n"
                )

    return f"""You are an experienced QA Engineer focused on test strategy and quality assurance.
Your role is to evaluate test coverage, identify gaps, and ensure code changes are verified.

## Process

1. **Gather context first.** Read the spec acceptance criteria, the
   source and test artifacts, and any prior test reports listed below.
   If you have tools available (`run_tests`, `read_file`,
   `list_files`), run the existing suite and quote real coverage
   numbers in your findings rather than guessing.
2. **Apply the test-level decision tree** before recommending any new
   test (see below).
3. **Use the Prove-It pattern** for any suspected bug — describe the
   exact failing test that would prove the bug, then the expected vs
   actual behavior, before suggesting a fix.

## Methodology: Test Engineering

### Test Level Decision Tree

```
Pure logic, no I/O                    → Unit test          (small, milliseconds)
Crosses one boundary (DB, HTTP, FS)   → Integration test   (medium, seconds)
Critical end-to-end user flow         → E2E test           (large, minutes)
```

Test at the LOWEST level that captures the behavior. Don't use E2E
tests for unit-testable logic. Target pyramid: ~80% unit, ~15%
integration, ~5% E2E.

### Required Scenario Coverage

For every public function/component, verify these are tested:

| Scenario       | What to check                                            |
|----------------|----------------------------------------------------------|
| Happy path     | Valid input produces expected output                     |
| Empty input    | Empty string, empty array, null, undefined               |
| Boundary values| Min, max, zero, negative, off-by-one                     |
| Error paths    | Invalid input, network failure, timeout                  |
| Concurrency    | Rapid calls, out-of-order responses (where applicable)   |

### Test Quality Rules

1. Test BEHAVIOR (state), not implementation details (interactions).
2. Each test verifies ONE concept; one logical assertion per test.
3. Tests are independent — no shared mutable state, no order coupling.
4. Mock at system boundaries (network, DB), not internal functions.
   Prefer real > fake > stub > mock.
5. Test names read like specifications
   (`should return 404 when user not found`).
6. Follow Arrange-Act-Assert structure; DAMP > DRY in tests.
7. A test that never fails is as useless as one that always fails.

### Prove-It Pattern (for bugs)

If code has a potential bug:
1. **Write the test that would FAIL** with current code.
2. **Confirm it fails** (RED).
3. State expected vs actual behavior.
4. Implement the fix; confirm the test passes (GREEN).
5. Run the full suite to ensure no regressions.

### Anti-Patterns to Flag

- Implementation-detail tests that break on refactor.
- Time-based / flaky tests (`setTimeout`, real clock, network races).
- Snapshot tests without behavioral assertions.
- Over-mocking — mocking internal modules instead of seams.
- Tests that test the framework rather than your code.
- Single-assertion-per-line bloat that obscures intent.
{testing_context}{prior_reports_section}
## Source Code Artifacts

{source_text}

## Existing Test Artifacts

{test_text}

## Your Task

Analyze the code and tests. Identify:
1. **Coverage gaps** — untested functions, missing edge cases, uncovered paths.
2. **Test quality issues** — implementation-coupled tests, flaky patterns, shared state, over-mocking.
3. **Missing test levels** — logic tested only at integration/E2E that should have unit tests.
4. **Prove-It opportunities** — potential bugs that need failing tests before fixes.

Severity guidance:
- **critical** — Untested data-loss / security path, or test that masks a real bug.
- **high** — Untested core business logic; flaky test in CI.
- **medium** — Missing edge cases on a tested function.
- **low** — Naming or structure issues in tests.
- **info** — Optional improvements.

Respond with a JSON object:

{{
  "summary": "Overall test quality assessment (1-2 sentences)",
  "coverage_assessment": {{
    "tested_functions": ["list of functions with tests"],
    "untested_functions": ["list of functions missing tests"],
    "estimated_coverage_percent": 75,
    "coverage_target_percent": 80,
    "critical_paths_covered": true
  }},
  "findings": [
    {{
      "finding_id": "test-1",
      "severity": "critical" | "high" | "medium" | "low" | "info",
      "confidence": 0.9,
      "category": "coverage_gap" | "test_quality" | "missing_level" | "prove_it_bug" | "anti_pattern",
      "title": "Short actionable title",
      "description": "What's missing and why it matters",
      "file_path": "path/to/file.ext",
      "line_range": [start_line, end_line],
      "suggestion": "Specific test code or approach to fix the gap",
      "prove_it": {{
        "failing_test_sketch": "Required for category=prove_it_bug",
        "expected_behavior": "What should happen",
        "actual_behavior": "What happens today"
      }}
    }}
  ],
  "proposed_tasks": [
    {{
      "task_id": "test-task-1",
      "title": "Write unit tests for [component]",
      "description": "What tests to write and what they verify",
      "acceptance_checks": ["All edge cases covered", "Tests pass independently"],
      "estimated_complexity": "s",
      "capability_type": "agent_only",
      "verification_step": "pytest tests/test_component.py -v",
      "test_level": "unit" | "integration" | "e2e"
    }}
  ]
}}

## Quality Gate

- [ ] Every untested public function appears in `coverage_assessment.untested_functions` AND has a finding or proposed task.
- [ ] Each missing edge case scenario is identified with a `coverage_gap` finding.
- [ ] Test level is set on every proposed task.
- [ ] Proposed tasks have clear acceptance criteria and a runnable `verification_step`.
- [ ] Prove-It pattern applied to any suspected bug (`category=prove_it_bug` MUST include `prove_it` block).
- [ ] At least one anti-pattern flagged if any tests show implementation-coupling, flakiness, or over-mocking.

Respond ONLY with the JSON object. No markdown fences, no explanation."""


def _parse_response(response_content: str) -> tuple[list[Finding], list[TaskDefinition], dict[str, Any]]:
    """Parse LLM response into findings, proposed tasks, and metadata."""
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

    # Parse findings
    findings: list[Finding] = []
    for f in parsed.get("findings", []):
        line_range = None
        if "line_range" in f and f["line_range"] is not None:
            lr = f["line_range"]
            line_range = (int(lr[0]), int(lr[1]))

        findings.append(
            Finding(
                finding_id=f["finding_id"],
                source_node="test_engineer_node",
                severity=IssueSeverity(f.get("severity", "medium")),
                confidence=float(f.get("confidence", 0.5)),
                title=f["title"],
                description=f["description"],
                file_path=f.get("file_path"),
                line_range=line_range,
                suggestion=f.get("suggestion"),
            ),
        )

    # Parse proposed tasks
    tasks: list[TaskDefinition] = []
    for t in parsed.get("proposed_tasks", []):
        complexity = t.get("estimated_complexity", "s").lower()
        if complexity not in ("xs", "s", "m", "l"):
            complexity = "s"
        capability = t.get("capability_type", "agent_only")
        if capability not in ("agent_only", "agent_with_tools", "direct_tool"):
            capability = "agent_only"

        tasks.append(
            TaskDefinition(
                task_id=t.get("task_id", f"test-task-{len(tasks)+1}"),
                title=t["title"],
                description=t["description"],
                acceptance_checks=t.get("acceptance_checks", []),
                estimated_complexity=complexity,
                capability_type=capability,
                verification_step=t.get("verification_step", "pytest"),
            ),
        )

    metadata = {
        "summary": parsed.get("summary", ""),
        "coverage_assessment": parsed.get("coverage_assessment", {}),
    }

    return findings, tasks, metadata


def _render_test_report_markdown(
    findings: list[Finding], tasks: list[TaskDefinition], metadata: dict[str, Any]
) -> str:
    """Render test report as markdown following test-engineer agent output format."""
    lines: list[str] = []

    lines.append("# Test Coverage & Quality Report\n")
    lines.append(f"> **Analyst:** Test Engineer Agent (QA Engineer)\n")
    lines.append("---\n")

    # Summary
    if metadata.get("summary"):
        lines.append(f"## Summary\n\n{metadata['summary']}\n")

    # Coverage assessment
    coverage = metadata.get("coverage_assessment", {})
    if coverage:
        lines.append("## Coverage Assessment\n")
        if coverage.get("estimated_coverage_percent"):
            lines.append(f"- **Estimated coverage:** {coverage['estimated_coverage_percent']}%")
        if coverage.get("tested_functions"):
            lines.append(f"- **Tested:** {len(coverage['tested_functions'])} functions")
        if coverage.get("untested_functions"):
            lines.append(f"- **Untested:** {len(coverage['untested_functions'])} functions")
            lines.append("\n### Untested Functions\n")
            for fn in coverage["untested_functions"]:
                lines.append(f"- ⚠️ `{fn}`")
        lines.append("")

    # Findings
    lines.append("## Findings\n")
    if not findings:
        lines.append("No test quality issues found.\n")
    else:
        # Group by category
        for f in findings:
            severity_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}.get(
                f.severity.value, "ℹ️"
            )
            lines.append(f"### {severity_icon} [{f.severity.value.upper()}] {f.title}\n")
            if f.file_path:
                loc = f"`{f.file_path}"
                if f.line_range:
                    loc += f":{f.line_range[0]}-{f.line_range[1]}"
                loc += "`"
                lines.append(f"- **File:** {loc}")
            lines.append(f"- **Issue:** {f.description}")
            if f.suggestion:
                lines.append(f"- **Recommendation:** {f.suggestion}")
            lines.append("")

    # Proposed tasks
    if tasks:
        lines.append("## Recommended Test Tasks\n")
        lines.append("| # | Task | Complexity | Verification |")
        lines.append("|---|------|-----------|-------------|")
        for i, t in enumerate(tasks, 1):
            lines.append(f"| {i} | {t.title} | {t.estimated_complexity} | `{t.verification_step}` |")
        lines.append("")

        for t in tasks:
            lines.append(f"### {t.task_id}: {t.title}\n")
            lines.append(f"{t.description}\n")
            lines.append("**Acceptance checks:**")
            for check in t.acceptance_checks:
                lines.append(f"- [ ] {check}")
            lines.append("")

    return "\n".join(lines)


def _commit_report_to_repo(
    findings: list[Finding], tasks: list[TaskDefinition], metadata: dict[str, Any],
    state: GraphState,
) -> None:
    """Write test report to ``<workdir>/docs/test-reports/`` and commit to git."""
    from flowforge.nodes._workspace import get_workdir

    workdir = get_workdir(state)
    cwd = str(workdir)

    report_dir = workdir / "docs" / "test-reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    # Determine next report number
    existing = list(report_dir.glob("test-report-*.md"))
    next_num = len(existing) + 1
    report_path = report_dir / f"test-report-{next_num}.md"

    markdown = _render_test_report_markdown(findings, tasks, metadata)
    report_path.write_text(markdown, encoding="utf-8")

    # Git add and commit
    try:
        rel = report_path.relative_to(workdir)
        subprocess.run(
            ["git", "add", str(rel)],
            cwd=cwd, capture_output=True, text=True, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", f"docs: add test coverage report #{next_num}"],
            cwd=cwd, capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass


def _create_github_issues(findings: list[Finding], state: GraphState) -> None:
    """Create GitHub issues for each finding using gh CLI.

    Labels issues with 'testing' and 'issue-by-test-engineer'.
    Skips silently if gh is unavailable.
    Runs from ``state.workdir`` so issues target the project's repo.
    """
    from flowforge.nodes._workspace import get_workdir

    cwd = str(get_workdir(state))

    # Ensure labels exist
    try:
        subprocess.run(
            ["gh", "label", "create", "testing",
             "--color", "0E8A16",
             "--description", "Test coverage or quality issue"],
            cwd=cwd, capture_output=True, text=True,
        )
        subprocess.run(
            ["gh", "label", "create", "issue-by-test-engineer",
             "--color", "FBCA04",
             "--description", "Issue identified by test engineer"],
            cwd=cwd, capture_output=True, text=True,
        )
    except FileNotFoundError:
        return  # gh CLI not available

    for finding in findings:
        # Only create issues for actionable findings
        if finding.severity == IssueSeverity.INFO:
            continue

        title = f"[TEST] [{finding.severity.value.upper()}] {finding.title}"

        body_parts = [
            f"**Source:** test_engineer_node ({finding.finding_id})",
            f"**Severity:** {finding.severity.value.upper()}",
            f"**Confidence:** {finding.confidence:.0%}",
        ]
        if finding.file_path:
            loc = finding.file_path
            if finding.line_range:
                loc += f":{finding.line_range[0]}-{finding.line_range[1]}"
            body_parts.append(f"**File:** `{loc}`")
        body_parts.append(f"\n**Problem:**\n{finding.description}")
        if finding.suggestion:
            body_parts.append(f"\n**Recommended Test:**\n{finding.suggestion}")

        body = "\n".join(body_parts)

        try:
            subprocess.run(
                [
                    "gh", "issue", "create",
                    "--label", "testing",
                    "--label", "issue-by-test-engineer",
                    "--title", title,
                    "--body", body,
                ],
                cwd=cwd, capture_output=True, text=True, check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue


def test_engineer_node(
    state: GraphState,
    *,
    llm: LLMProtocol,
) -> dict[str, Any]:
    """Evaluate test quality following test-engineer agent methodology.

    Analyzes coverage gaps, test quality, level appropriateness,
    and potential bugs needing the Prove-It pattern.
    Commits test report to docs/test-reports/ and creates GitHub issues.
    When the ``FLOWFORGE_DEEP_AGENTS`` flag is enabled, dispatches through
    the Deep Agent factory; otherwise runs the legacy single-shot path.
    """
    if resolve_deep_agents_enabled():
        return _run_via_deep_agent(state, llm)

    prompt = _build_prompt(state)
    response = llm.invoke(prompt)

    content = response.content if hasattr(response, "content") else str(response)
    findings, proposed_tasks, metadata = _parse_response(content)

    # Commit test report to repo
    _commit_report_to_repo(findings, proposed_tasks, metadata, state)

    # Create GitHub issues for findings
    _create_github_issues(findings, state)

    return {
        "test_findings": findings,
        "proposed_tasks": proposed_tasks,
    }


test_engineer_node.__test__ = False  # type: ignore[attr-defined]


def _run_via_deep_agent(state: GraphState, llm: LLMProtocol) -> dict[str, Any]:
    """Deep Agent variant of ``test_engineer_node`` (T7)."""
    workdir = get_workdir(state)
    files = materialize_files(state)
    graph = build_deep_agent(
        role=AgentRole.TESTER,
        llm=cast("BaseChatModel", llm),
        workdir=workdir,
    )
    rubric = _build_prompt(state)
    user_message = (
        "Evaluate test quality on the artifacts in your VFS using the"
        " rubric below. Use your tools (`run_tests`, `read_file`,"
        " `list_files`) to run the existing suite and quote real"
        " coverage numbers in your findings. After producing the JSON,"
        " also save it verbatim to vfs:/findings/test.json, write the"
        " proposed tasks array to vfs:/context/proposed_tasks.json, and"
        " write a markdown report at"
        " vfs:/docs/test-reports/test-report.md.\n\n"
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
        role=AgentRole.TESTER,
        node_name="test_engineer_node",
        invocation_sink=invocations,
    )

    findings = [
        f.model_copy(update={"source_node": "test_engineer_node"})
        for f in extract_findings(result)
    ]
    existing_task_ids = {t.task_id for t in state.tasks}
    proposed_tasks = _extract_proposed_tasks(result, existing_task_ids)

    raw_files = result.get("files")
    vfs_keys: list[str] = (
        sorted(k for k in raw_files if isinstance(k, str))
        if isinstance(raw_files, dict) else []
    )
    raw_messages = result.get("messages")
    messages: list[dict[str, object]] = (
        [m for m in raw_messages if isinstance(m, dict)]
        if isinstance(raw_messages, list) else []
    )

    # Recover summary / coverage_assessment from the agent's final
    # assistant message; fall back to defaults if not parseable.
    metadata: dict[str, Any] = {
        "summary": "Deep-agent test review run.",
        "coverage_assessment": {},
    }
    for m in reversed(messages):
        content = m.get("content")
        if not isinstance(content, str):
            continue
        try:
            _, _, parsed_meta = _parse_response(content)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        metadata.update(parsed_meta)
        break

    trace = DeepAgentTrace(
        role=AgentRole.TESTER,
        messages_digest=DeepAgentTrace.digest_messages(messages),
        vfs_keys=vfs_keys,
        tool_invocations=invocations,
    )

    _commit_report_to_repo(
        findings,
        proposed_tasks,
        metadata,
        state,
    )
    _create_github_issues(findings, state)

    return {
        "test_findings": findings,
        "proposed_tasks": proposed_tasks,
        "deep_agent_traces": {"test_engineer_node": trace},
    }


def _extract_proposed_tasks(
    result: dict[str, object],
    existing_task_ids: set[str] | None = None,
) -> list[TaskDefinition]:
    """Parse ``vfs:/context/proposed_tasks.json`` from agent VFS.

    Tolerant of missing or malformed entries: skips invalid items and
    duplicates of any ID in ``existing_task_ids``.
    """
    raw_files = result.get("files")
    if not isinstance(raw_files, dict):
        return []
    body = raw_files.get("vfs:/context/proposed_tasks.json")
    if not isinstance(body, str):
        return []
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []

    seen: set[str] = set(existing_task_ids or ())
    tasks: list[TaskDefinition] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        complexity = str(item.get("estimated_complexity", "s")).lower()
        if complexity not in ("xs", "s", "m", "l"):
            complexity = "s"
        capability_raw = str(item.get("capability_type", "agent_only"))
        try:
            capability = CapabilityType(capability_raw)
        except ValueError:
            capability = CapabilityType.AGENT_ONLY
        ac_raw = item.get("acceptance_checks", [])
        acceptance = (
            [str(x) for x in ac_raw if isinstance(x, str)]
            if isinstance(ac_raw, list) else []
        )
        task_id = str(item.get("task_id", f"test-task-{len(tasks) + 1}"))
        if task_id in seen:
            continue
        try:
            tasks.append(
                TaskDefinition(
                    task_id=task_id,
                    title=str(item["title"]),
                    description=str(item["description"]),
                    acceptance_checks=acceptance,
                    estimated_complexity=complexity,
                    capability_type=capability,
                    verification_step=str(item.get("verification_step", "pytest")),
                ),
            )
            seen.add(task_id)
        except (KeyError, TypeError, ValueError):
            continue
    return tasks

