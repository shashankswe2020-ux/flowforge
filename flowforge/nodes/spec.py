"""Spec node — spec-driven development: write structured specifications before code.

Follows the spec agent workflow:
1. Understand requirements from the clarified request
2. Draft a structured spec (objective, architecture, testing strategy, security, boundaries)
3. Produce verifiable acceptance criteria

Every feature must have acceptance criteria. Every criterion must be verifiable.
The spec is the shared source of truth — defines what to build, why, and how to verify.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Protocol, cast

from flowforge.config.deep_agents import resolve_deep_agents_enabled
from flowforge.deep_agents import AgentRole
from flowforge.deep_agents.adapters import materialize_files
from flowforge.deep_agents.factory import build_deep_agent, run_deep_agent_bounded
from flowforge.nodes._workspace import get_workdir
from flowforge.state.models import (
    DeepAgentTrace,
    GraphState,
    RunStatus,
    SpecOutput,
    ToolInvocationRecord,
)

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel


class LLMProtocol(Protocol):
    """Minimal LLM interface for spec node."""

    def invoke(self, prompt: str) -> Any: ...  # noqa: ANN401


class ClarificationIncompleteError(Exception):
    """Raised when spec_node is invoked before clarification is complete.

    Provides a user-friendly message (not technical diagnostics).
    """


def _validate_clarification(state: GraphState) -> None:
    """Ensure clarification is complete before spec generation.

    Raises:
        ClarificationIncompleteError: With plain-language guidance.
    """
    if state.clarified_request is None:
        raise ClarificationIncompleteError(
            "Cannot generate a spec yet — the project scope has not been clarified. "
            "Please complete the clarification step first.",
        )

    if not state.ambiguity_status.is_complete:
        unresolved = state.ambiguity_status.unresolved_dimensions
        if unresolved:
            dims = ", ".join(unresolved)
            raise ClarificationIncompleteError(
                "Cannot generate a spec yet — some aspects of the project "
                f"still need clarification: {dims}. "
                "Please resolve these before proceeding.",
            )
        raise ClarificationIncompleteError(
            "Cannot generate a spec yet — the clarification step "
            "has not been marked as complete. "
            "Please finish clarifying the project scope first.",
        )


def _build_prompt(state: GraphState) -> str:
    """Build the spec generation prompt following spec-driven development methodology.

    Produces a comprehensive specification covering:
    - The six core spec areas from spec-driven-development skill
    - Assumption surfacing, success criteria reframing
    - Verifiable acceptance criteria
    - Security considerations and boundaries (always/ask_first/never)
    """
    cr = state.clarified_request
    assert cr is not None  # validated above

    return f"""You are a senior engineer practicing spec-driven development. The spec is the
shared source of truth — it defines what we're building, why, and how we'll know it's done.
You do NOT write code — you produce specifications.

Code without a spec is guessing. A 15-minute spec prevents hours of rework.

## Methodology: Spec-Driven Development

Follow these principles from the spec-driven-development skill:

1. **Surface assumptions immediately.** Don't silently fill in ambiguous requirements.
   The spec's purpose is to surface misunderstandings BEFORE code gets written.

2. **Reframe vague requirements as success criteria.** Translate "make it fast" into
   measurable targets like "page loads in < 2s on 4G."

3. **Cover the six core areas:**
   - Objective — what and why, user stories, success definition
   - Commands — full executable build/test/lint/dev commands
   - Project Structure — where source, tests, and docs live
   - Code Style — naming conventions, formatting, example snippets
   - Testing Strategy — framework, levels, coverage, mocking
   - Boundaries — always/ask_first/never tiers

4. **Success criteria must be specific and testable** — not "works well" but
   "passes all unit tests" or "API responds in < 200ms."

## Clarified Requirements

- **Project summary**: {cr.summary}
- **Solution type**: {cr.solution_type}
- **Scope**: {cr.scope_size}
- **Target users**: {cr.target_users}
- **Must-have features**: {', '.join(cr.must_have)}
- **Nice-to-have features**: {', '.join(cr.nice_to_have)}
- **Constraints**: {', '.join(cr.constraints)}
- **Success criteria**: {', '.join(cr.success_criteria)}
- **Tech preferences**: {', '.join(cr.tech_preferences)}

## Your Task

Generate a structured specification document. Think deeply about architecture,
security, testability, and developer experience. Surface any assumptions you're making.

Respond with a single JSON object containing these fields:

1. "artifact_path": string — file path for the spec (e.g. "docs/spec/feature-name.md")

2. "summary": string — one paragraph describing the full project scope

3. "objective": string — what we're building, why it matters, who it's for, and what
   success looks like. Include user stories if appropriate.

4. "target_users": string — who will use this, their technical level, and their primary
   use cases

5. "tech_stack": list of strings — specific technologies with versions/ranges. Include:
   language, framework, key dependencies, build tools, test framework.
   Example: ["Python >=3.12", "FastAPI 0.100+", "Pydantic v2", "pytest + pytest-cov"]

6. "commands": object with keys "build", "test", "lint", "dev" — full executable
   commands (not just tool names). Example: {{"build": "npm run build", "test": "pytest --cov=src"}}

7. "project_structure": list of strings — directory layout with descriptions.
   Example: ["src/ → Application source code", "tests/ → Unit and integration tests"]

8. "code_style": list of strings — naming conventions, formatting rules, and key patterns.
   Example: ["Files: kebab-case.py", "Classes: PascalCase", "Functions: snake_case",
   "Always use type hints", "Named exports only"]

9. "acceptance_criteria": list of strings — VERIFIABLE success criteria. Minimum 5.
   Each must be testable via a command, test, or measurable check.
   Format: "GIVEN <context> WHEN <action> THEN <expected result>" or
   "VERIFY THAT <specific measurable condition>"

10. "assumptions": list of strings — explicit assumptions you're making about the
    environment, dependencies, user behavior, or requirements. Surface these prominently.
    Example: ["Database is PostgreSQL (not specified, assuming based on constraints)",
    "Auth uses session cookies (not JWT)"]

11. "open_questions": list of strings — unresolved items that need human input before
    or during implementation

12. "security_considerations": list of strings — authentication, authorization, input
    validation, secrets management, data handling, transport security

13. "testing_strategy": list of strings — test framework, test hierarchy
    (unit > integration > e2e), where tests live, coverage targets, mocking approach,
    what to test at each level.
    Example: ["Unit tests for all business logic (>80% coverage)",
    "Integration tests for API endpoints", "Mock external services with vi.fn()",
    "Tests mirror src/ structure in tests/ directory"]

14. "boundaries": object with three keys:
    - "always": list — things to always do. Be specific and actionable.
      Example: ["Run tests before commits", "Validate all user input with Zod",
      "Use parameterized queries for DB access"]
    - "ask_first": list — things requiring approval before doing.
      Example: ["Adding new runtime dependencies", "Database schema changes",
      "Changing CI/CD configuration"]
    - "never": list — hard prohibitions.
      Example: ["Commit secrets or API keys", "Remove failing tests without approval",
      "Use `any` type", "Skip input validation"]

## Quality Gate — Your spec MUST pass these checks:

- [ ] Every acceptance criterion is verifiable (runnable test or command)
- [ ] Assumptions are surfaced explicitly, not hidden in the spec
- [ ] Boundaries are specific and actionable (not "be careful" or "write good code")
- [ ] Tech stack includes specific versions or version ranges
- [ ] Testing strategy specifies levels, coverage targets, and mocking approach
- [ ] Security section covers auth, validation, secrets, and data handling
- [ ] Commands are full and executable (not just "run tests")
- [ ] Project structure maps directories to responsibilities

Respond ONLY with the JSON object. No markdown fences, no explanation."""


def spec_node(
    state: GraphState,
    *,
    llm: LLMProtocol,
) -> dict[str, Any]:
    """Generate a structured spec following spec-driven development methodology.

    Validates clarification is complete, then invokes LLM to produce
    a comprehensive SpecOutput covering objective, architecture, testing,
    security, and verifiable acceptance criteria.

    Raises:
        ClarificationIncompleteError: If clarification is not complete.
    """
    _validate_clarification(state)

    if resolve_deep_agents_enabled():
        return _run_via_deep_agent(state, llm)

    prompt = _build_prompt(state)
    response = llm.invoke(prompt)

    # Handle response — strip markdown fences if present
    content = response.content if hasattr(response, "content") else str(response)
    content = content.strip()
    if content.startswith("```"):
        # Strip ```json ... ``` wrapping
        lines = content.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines)

    parsed = json.loads(content)

    spec = SpecOutput(
        artifact_path=parsed.get("artifact_path", "docs/spec/spec.md"),
        summary=parsed.get("summary", ""),
        objective=parsed.get("objective", ""),
        target_users=parsed.get("target_users", ""),
        acceptance_criteria=parsed.get("acceptance_criteria", []),
        assumptions=parsed.get("assumptions", []),
        open_questions=parsed.get("open_questions", []),
        tech_stack=parsed.get("tech_stack", []),
        commands=parsed.get("commands", {}),
        project_structure=parsed.get("project_structure", []),
        code_style=parsed.get("code_style", []),
        security_considerations=parsed.get("security_considerations", []),
        testing_strategy=parsed.get("testing_strategy", []),
        boundaries=parsed.get("boundaries", {}),
    )

    # Write spec to docs/spec/ and commit to git
    _commit_spec_to_repo(spec, state)

    return {
        "run_status": RunStatus.RUNNING,
        "spec": spec,
    }


def _render_spec_markdown(spec: SpecOutput) -> str:
    """Render the spec as a markdown document following spec-driven-development format."""
    lines: list[str] = []
    lines.append(f"# Specification\n")
    lines.append(f"## Summary\n\n{spec.summary}\n")

    if spec.objective:
        lines.append(f"## Objective\n\n{spec.objective}\n")

    if spec.target_users:
        lines.append(f"## Target Users\n\n{spec.target_users}\n")

    if spec.tech_stack:
        lines.append("## Tech Stack\n")
        for item in spec.tech_stack:
            lines.append(f"- {item}")
        lines.append("")

    if spec.commands:
        lines.append("## Commands\n")
        lines.append("```bash")
        for cmd_name, cmd_value in spec.commands.items():
            lines.append(f"{cmd_name}: {cmd_value}")
        lines.append("```")
        lines.append("")

    if spec.project_structure:
        lines.append("## Project Structure\n")
        lines.append("```")
        for item in spec.project_structure:
            lines.append(f"{item}")
        lines.append("```")
        lines.append("")

    if spec.code_style:
        lines.append("## Code Style\n")
        for item in spec.code_style:
            lines.append(f"- {item}")
        lines.append("")

    lines.append("## Acceptance Criteria\n")
    for i, criterion in enumerate(spec.acceptance_criteria, 1):
        lines.append(f"{i}. {criterion}")
    lines.append("")

    if spec.assumptions:
        lines.append("## Assumptions\n")
        lines.append("> These assumptions were surfaced during spec generation.")
        lines.append("> Correct them now if they're wrong.\n")
        for item in spec.assumptions:
            lines.append(f"- {item}")
        lines.append("")

    if spec.security_considerations:
        lines.append("## Security Considerations\n")
        for item in spec.security_considerations:
            lines.append(f"- {item}")
        lines.append("")

    if spec.testing_strategy:
        lines.append("## Testing Strategy\n")
        for item in spec.testing_strategy:
            lines.append(f"- {item}")
        lines.append("")

    if spec.boundaries:
        lines.append("## Boundaries\n")
        if spec.boundaries.get("always"):
            lines.append("### Always\n")
            for item in spec.boundaries["always"]:
                lines.append(f"- ✅ {item}")
            lines.append("")
        if spec.boundaries.get("ask_first"):
            lines.append("### Ask First\n")
            for item in spec.boundaries["ask_first"]:
                lines.append(f"- ⚠️ {item}")
            lines.append("")
        if spec.boundaries.get("never"):
            lines.append("### Never\n")
            for item in spec.boundaries["never"]:
                lines.append(f"- 🚫 {item}")
            lines.append("")

    if spec.open_questions:
        lines.append("## Open Questions\n")
        for item in spec.open_questions:
            lines.append(f"- [ ] {item}")
        lines.append("")

    return "\n".join(lines)


def _commit_spec_to_repo(spec: SpecOutput, state: GraphState) -> None:
    """Write spec markdown to ``<workdir>/docs/spec/`` and commit to git.

    Creates the directory if needed. Uses the artifact_path from the spec,
    but ensures it lives under docs/spec/.
    """
    import subprocess
    from pathlib import Path

    from flowforge.nodes._workspace import get_workdir

    workdir = get_workdir(state)
    cwd = str(workdir)

    # Determine file path — always under <workdir>/docs/spec/
    filename = Path(spec.artifact_path).name
    if not filename.endswith(".md"):
        filename = filename + ".md"
    spec_dir = workdir / "docs" / "spec"
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec_path = spec_dir / filename

    # Write the markdown
    markdown = _render_spec_markdown(spec)
    spec_path.write_text(markdown, encoding="utf-8")

    # Git add and commit (no-op if not in a git repo)
    try:
        rel = spec_path.relative_to(workdir)
        subprocess.run(
            ["git", "add", str(rel)],
            cwd=cwd, capture_output=True, text=True, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", f"docs: add specification ({filename})"],
            cwd=cwd, capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Not in a git repo or git not available — skip silently
        pass


# ---------------------------------------------------------------------------
# Deep Agent variant (T8)
# ---------------------------------------------------------------------------


_SPEC_VFS_PATH = "vfs:/context/spec_output.json"


def _build_spec_output(parsed: dict[str, Any]) -> SpecOutput:
    """Build SpecOutput from a parsed JSON payload (legacy + deep share this)."""
    return SpecOutput(
        artifact_path=parsed.get("artifact_path", "docs/spec/spec.md"),
        summary=parsed.get("summary", ""),
        objective=parsed.get("objective", ""),
        target_users=parsed.get("target_users", ""),
        acceptance_criteria=parsed.get("acceptance_criteria", []),
        assumptions=parsed.get("assumptions", []),
        open_questions=parsed.get("open_questions", []),
        tech_stack=parsed.get("tech_stack", []),
        commands=parsed.get("commands", {}),
        project_structure=parsed.get("project_structure", []),
        code_style=parsed.get("code_style", []),
        security_considerations=parsed.get("security_considerations", []),
        testing_strategy=parsed.get("testing_strategy", []),
        boundaries=parsed.get("boundaries", {}),
    )


def _extract_spec(result: dict[str, object]) -> SpecOutput | None:
    """Parse the agent's structured spec output from VFS, if present."""
    files = result.get("files")
    if not isinstance(files, dict):
        return None
    raw = files.get(_SPEC_VFS_PATH)
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return _build_spec_output(parsed)


def _run_via_deep_agent(
    state: GraphState, llm: LLMProtocol,
) -> dict[str, Any]:
    """Deep Agent variant of ``spec_node`` (T8)."""
    workdir = get_workdir(state)
    files = materialize_files(state)
    graph = build_deep_agent(
        role=AgentRole.SPEC_AUTHOR,
        llm=cast("BaseChatModel", llm),
        workdir=workdir,
    )
    payload: dict[str, Any] = {
        "messages": [
            {
                "role": "user",
                "content": (
                    "Produce a structured specification document for the "
                    "clarified request stored at "
                    "vfs:/context/clarified_request.json and the project "
                    "state. Write the final SpecOutput JSON to "
                    f"{_SPEC_VFS_PATH}. Use the spec-author six-area "
                    "methodology and ensure every acceptance criterion is "
                    "verifiable."
                ),
            },
        ],
        "files": files,
    }
    invocations: list[ToolInvocationRecord] = []
    result = run_deep_agent_bounded(
        graph,
        payload,
        role=AgentRole.SPEC_AUTHOR,
        node_name="spec_node",
        invocation_sink=invocations,
    )

    spec = _extract_spec(result)
    if spec is None:
        # Fallback: re-run the legacy single-shot path so callers always
        # see a valid SpecOutput when clarification is complete.
        return _legacy_spec(state, llm)

    raw_files = result.get("files")
    vfs_keys: list[str] = (
        sorted(k for k in raw_files if isinstance(k, str))
        if isinstance(raw_files, dict)
        else []
    )
    raw_messages = result.get("messages")
    messages: list[dict[str, object]] = (
        [m for m in raw_messages if isinstance(m, dict)]
        if isinstance(raw_messages, list)
        else []
    )
    trace = DeepAgentTrace(
        role=AgentRole.SPEC_AUTHOR,
        messages_digest=DeepAgentTrace.digest_messages(messages),
        vfs_keys=vfs_keys,
        tool_invocations=invocations,
    )

    _commit_spec_to_repo(spec, state)

    return {
        "run_status": RunStatus.RUNNING,
        "spec": spec,
        "deep_agent_traces": {"spec_node": trace},
    }


def _legacy_spec(state: GraphState, llm: LLMProtocol) -> dict[str, Any]:
    """Legacy single-shot fallback (extracted for fallback reuse)."""
    prompt = _build_prompt(state)
    response = llm.invoke(prompt)
    content = response.content if hasattr(response, "content") else str(response)
    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines)
    parsed = json.loads(content)
    spec = _build_spec_output(parsed)
    _commit_spec_to_repo(spec, state)
    return {"run_status": RunStatus.RUNNING, "spec": spec}
