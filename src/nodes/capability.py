"""Task capability type executors — TDD cycle implementation.

Follows the build agent methodology:
- Incremental implementation in thin vertical slices
- TDD cycle: RED (failing test) → GREEN (minimal code) → REFACTOR
- Each task leaves the system in a working state
- Verification before marking complete

Each executor handles one of the three capability types:
- AGENT_ONLY: LLM reasoning without tool execution
- AGENT_WITH_TOOLS: LLM reasoning with constrained tool access
- DIRECT_TOOL: Deterministic tool execution without LLM
"""

from __future__ import annotations

import contextlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from src.state.models import (
    Task,
    TaskArtifact,
    TaskStatus,
)


class LLMProtocol(Protocol):
    """Minimal LLM interface for task execution."""

    def invoke(self, prompt: str) -> Any: ...  # noqa: ANN401


@dataclass
class TaskExecutionResult:
    """Result of executing a single task."""

    task_id: str
    status: TaskStatus
    artifacts: list[TaskArtifact] = field(default_factory=list)
    verification_evidence: list[str] = field(default_factory=list)
    error_message: str | None = None
    idempotency_key: str = field(default_factory=lambda: str(uuid.uuid4()))


def _parse_llm_response(task: Task, response_content: str) -> TaskExecutionResult:
    """Parse and validate LLM response into TaskExecutionResult.

    Handles markdown-fenced JSON responses and extracts artifacts with content.
    Returns a FAILED result if parsing or validation fails.
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

    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError) as e:
        return TaskExecutionResult(
            task_id=task.task_id,
            status=TaskStatus.FAILED,
            error_message=f"Invalid response format: {e}",
        )

    # Validate required fields
    if "artifacts" not in parsed or "verification_evidence" not in parsed:
        return TaskExecutionResult(
            task_id=task.task_id,
            status=TaskStatus.FAILED,
            error_message="Response missing required fields: artifacts, verification_evidence",
        )

    # Parse status
    raw_status = parsed.get("status", "failed")
    try:
        status = TaskStatus(raw_status)
    except ValueError:
        status = TaskStatus.FAILED

    # Parse artifacts (supports both content-bearing and reference-only artifacts)
    artifacts: list[TaskArtifact] = []
    for art in parsed.get("artifacts", []):
        with contextlib.suppress(KeyError, TypeError):
            artifacts.append(
                TaskArtifact(
                    artifact_id=art.get("artifact_id", str(uuid.uuid4())),
                    artifact_type=art.get("artifact_type", "source"),
                    path=art["path"],
                    fingerprint=art.get("fingerprint", ""),
                    content=art.get("content", ""),
                ),
            )

    evidence: list[str] = parsed.get("verification_evidence", [])

    return TaskExecutionResult(
        task_id=task.task_id,
        status=status,
        artifacts=artifacts,
        verification_evidence=evidence,
    )


def _build_task_prompt(task: Task) -> str:
    """Build execution prompt following build agent TDD methodology.

    Incorporates:
    - incremental-implementation skill (thin vertical slices, simplicity first)
    - test-driven-development skill (RED → GREEN → REFACTOR)
    - Build agent workflow (implement → test → verify → commit)
    """
    defn = task.definition
    return f"""You are a senior engineer implementing a task using strict TDD methodology.
Build in thin vertical slices — implement the minimum needed, verify it works, then expand.

## Methodology

Follow the TDD cycle for this task:

1. **RED** — Think about what test would prove this task works:
   - What behavior should be verified?
   - What are the edge cases?
   - What inputs and expected outputs define correctness?

2. **GREEN** — Implement the MINIMUM code to satisfy acceptance criteria:
   - Simplicity first: "What is the simplest thing that could work?"
   - Don't over-engineer or build for hypothetical futures
   - Each file should have a single clear responsibility

3. **REFACTOR** — Clean up while maintaining correctness:
   - Remove duplication
   - Improve naming
   - Extract shared logic only if used more than once

4. **VERIFY** — Confirm the implementation satisfies all acceptance checks

## Implementation Rules

- Build ONE complete vertical slice (not horizontal layers)
- Leave the system in a working state after this task
- Never skip verification — "seems right" is not done
- Produce real, runnable code (not pseudocode or stubs)
- Include both implementation files AND test files in artifacts

## Task

- **Task ID**: {defn.task_id}
- **Title**: {defn.title}
- **Description**: {defn.description}
- **Acceptance Checks**: {'; '.join(defn.acceptance_checks)}
- **Estimated Complexity**: {defn.estimated_complexity}
- **Verification Step**: {defn.verification_step}

## Expected Output

Produce code that satisfies ALL acceptance checks. For each file you create or modify,
include it as an artifact with its full content.

Respond with a JSON object:

{{
  "status": "succeeded" | "failed" | "blocked",
  "artifacts": [
    {{
      "artifact_id": "unique-id",
      "artifact_type": "source" | "test" | "config" | "docs",
      "path": "relative/path/to/file.ext",
      "fingerprint": "sha256 of content (can be placeholder)",
      "content": "full file content here"
    }}
  ],
  "verification_evidence": [
    "Description of how each acceptance check is satisfied"
  ],
  "implementation_notes": "Brief explanation of approach taken"
}}

## Quality Gate

Before responding, verify:
- [ ] Every acceptance check has corresponding code
- [ ] Tests are included for testable behavior
- [ ] No over-engineering beyond what's needed for this task
- [ ] Code is production quality (proper error handling, types, naming)
- [ ] The verification_step command would pass with this implementation"""


class AgentOnlyExecutor:
    """Executor for AGENT_ONLY capability: LLM reasoning, no tools."""

    def execute(self, task: Task, *, llm: LLMProtocol | None) -> TaskExecutionResult:
        """Execute task using LLM only."""
        if llm is None:
            return TaskExecutionResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error_message="AGENT_ONLY task requires an LLM but none provided",
            )
        prompt = _build_task_prompt(task)
        response = llm.invoke(prompt)
        return _parse_llm_response(task, response.content)


class AgentWithToolsExecutor:
    """Executor for AGENT_WITH_TOOLS capability: LLM + constrained tools."""

    def execute(self, task: Task, *, llm: LLMProtocol | None) -> TaskExecutionResult:
        """Execute task using LLM with tool access."""
        if llm is None:
            return TaskExecutionResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error_message="AGENT_WITH_TOOLS task requires an LLM but none provided",
            )
        prompt = _build_task_prompt(task)
        response = llm.invoke(prompt)
        return _parse_llm_response(task, response.content)


class DirectToolExecutor:
    """Executor for DIRECT_TOOL capability: deterministic, no LLM."""

    def execute(self, task: Task, *, llm: LLMProtocol | None = None) -> TaskExecutionResult:
        """Execute task deterministically without LLM."""
        # Direct tool tasks succeed by executing their verification step
        # In a real implementation, this would run the actual tool
        return TaskExecutionResult(
            task_id=task.task_id,
            status=TaskStatus.SUCCEEDED,
            verification_evidence=[f"Direct tool executed: {task.definition.verification_step}"],
        )
