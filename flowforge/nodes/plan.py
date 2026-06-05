"""Plan node — decomposes spec into verifiable tasks with dependency ordering.

Follows the planning-and-task-breakdown skill methodology:
1. Read-only planning mode (no code changes)
2. Identify dependency graph between components
3. Slice work vertically (complete feature paths, not horizontal layers)
4. Write tasks with acceptance criteria, verification commands, and file lists
5. Order by dependencies with checkpoints between phases
6. Commit plan to docs/plans/

Every task must be small enough to implement, test, and verify in one session.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Protocol

from src.dag.validator import validate_dag
from src.state.models import (
    GraphState,
    ImplementationPlan,
    RunStatus,
    TaskDAG,
    TaskDefinition,
    TaskDependency,
)


class LLMProtocol(Protocol):
    """Minimal LLM interface for plan node."""

    def invoke(self, prompt: str) -> Any: ...  # noqa: ANN401


class SpecMissingError(Exception):
    """Raised when plan_node is invoked without a spec."""

    def __init__(self) -> None:
        super().__init__(
            "Cannot generate an implementation plan — no spec has been produced yet. "
            "Please complete the spec step before planning.",
        )


def _build_prompt(state: GraphState) -> str:
    """Build plan generation prompt following planning-and-task-breakdown skill."""
    spec = state.spec
    assert spec is not None

    # Build rich context from all available spec fields
    spec_context_parts = [
        f"**Summary**: {spec.summary}",
        f"**Objective**: {spec.objective}" if spec.objective else "",
        f"**Target users**: {spec.target_users}" if spec.target_users else "",
        f"**Tech stack**: {', '.join(spec.tech_stack)}" if spec.tech_stack else "",
        f"**Acceptance criteria**: {'; '.join(spec.acceptance_criteria)}",
    ]
    if spec.commands:
        spec_context_parts.append(
            f"**Commands**: {', '.join(f'{k}: `{v}`' for k, v in spec.commands.items())}"
        )
    if spec.project_structure:
        spec_context_parts.append(
            f"**Project structure**: {'; '.join(spec.project_structure)}"
        )
    if spec.testing_strategy:
        spec_context_parts.append(
            f"**Testing strategy**: {'; '.join(spec.testing_strategy)}"
        )
    if spec.security_considerations:
        spec_context_parts.append(
            f"**Security**: {'; '.join(spec.security_considerations)}"
        )
    if spec.assumptions:
        spec_context_parts.append(
            f"**Assumptions**: {'; '.join(spec.assumptions)}"
        )
    if spec.boundaries:
        boundary_parts = []
        for tier, items in spec.boundaries.items():
            boundary_parts.append(f"{tier}: {', '.join(items)}")
        spec_context_parts.append(f"**Boundaries**: {'; '.join(boundary_parts)}")

    spec_context = "\n".join(p for p in spec_context_parts if p)

    return f"""You are a senior engineer in planning mode. You decompose work into small,
verifiable tasks with explicit acceptance criteria and dependency ordering.
You do NOT write code — you produce plans.

## Methodology: Planning and Task Breakdown

Follow these principles:

1. **Slice vertically, not horizontally.** Each task delivers a complete working feature
   path (e.g., "User can create account: schema + API + UI"), not a horizontal layer
   (e.g., "Build entire database schema").

2. **Task sizing:** Tasks must be completable in a single focused session.
   - XS: 1 file, single function/config change
   - S: 1-2 files, one component or endpoint
   - M: 3-5 files, one feature slice
   - L: 5-8 files, multi-component feature (maximum — prefer breaking further)
   - If a task is L or larger, break it down further.

3. **Every task MUST have:**
   - Clear acceptance criteria (testable conditions)
   - A verification step (runnable command: test, build, lint)
   - Dependency declaration (which tasks must complete first)
   - Estimated file count

4. **Dependency ordering:**
   - Build foundations first (types, schemas, core utilities)
   - Each task leaves the system in a working state
   - High-risk tasks go early (fail fast)
   - No circular dependencies

5. **Checkpoints:** Add verification checkpoints after every 2-3 tasks.
   A checkpoint confirms: all tests pass, build succeeds, core flows work.

6. **Risks:** Identify risks with impact and mitigation strategy.

## Spec

{spec_context}

## Your Task

Generate an implementation plan. Respond with a single JSON object:

{{
  "plan_summary": "One paragraph overview of the implementation approach",
  "architecture_decisions": ["Key decision 1 and rationale", "Key decision 2..."],
  "phases": ["Phase 1: Foundation", "Phase 2: Core Features", "Phase 3: Polish"],
  "tasks": [
    {{
      "task_id": "t1",
      "title": "Short descriptive title",
      "description": "One paragraph: what this task accomplishes and why",
      "acceptance_checks": [
        "Specific testable condition 1",
        "Specific testable condition 2"
      ],
      "verification_step": "Full runnable command (e.g., pytest tests/test_auth.py)",
      "estimated_complexity": "s",
      "capability_type": "agent_only",
      "files_touched": ["src/path/file.py", "tests/path/test_file.py"],
      "phase": "Phase 1: Foundation"
    }}
  ],
  "edges": [
    {{"from_task_id": "t1", "to_task_id": "t2"}}
  ],
  "checkpoints": [
    {{
      "after_tasks": ["t1", "t2", "t3"],
      "name": "Foundation checkpoint",
      "criteria": ["All tests pass", "Build succeeds", "Core types importable"]
    }}
  ],
  "risks": [
    {{
      "risk": "Description of risk",
      "impact": "high",
      "mitigation": "How to address it"
    }}
  ],
  "parallelization": ["t2 and t3 can run in parallel", "t5 depends on t4"],
  "plan_revision": 1
}}

## Quality Gate — Plan MUST satisfy:

- [ ] Every task has acceptance criteria AND a verification step
- [ ] Tasks are vertically sliced (complete feature paths, not layers)
- [ ] No task touches more than 5 files (prefer 1-3)
- [ ] estimated_complexity is xs, s, m, or l (no XL tasks — break them down)
- [ ] capability_type is one of: agent_only, agent_with_tools, direct_tool
- [ ] Dependencies form an acyclic graph
- [ ] Checkpoints exist between major phases
- [ ] High-risk items are scheduled early
- [ ] Task IDs are deterministic and unique (t1, t2, t3...)

Respond ONLY with the JSON object. No markdown fences, no explanation."""


def plan_node(
    state: GraphState,
    *,
    llm: LLMProtocol,
) -> dict[str, Any]:
    """Generate an implementation plan from the spec.

    Follows planning-and-task-breakdown skill:
    - Vertical slicing (complete feature paths)
    - Acceptance criteria + verification on every task
    - Dependency ordering with checkpoints
    - Commits plan markdown to docs/plans/

    Raises:
        SpecMissingError: If no spec is available.
        CyclicDAGError: If the produced DAG contains cycles.
    """
    if state.spec is None:
        raise SpecMissingError()

    prompt = _build_prompt(state)
    response = llm.invoke(prompt)

    # Handle response — strip markdown fences if present
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

    # Build TaskDefinition list
    tasks = []
    for t in parsed["tasks"]:
        # Normalize complexity values
        complexity = _normalize_complexity(t.get("estimated_complexity", "s"))
        capability = _normalize_capability(t.get("capability_type", "agent_only"))

        tasks.append(
            TaskDefinition(
                task_id=t["task_id"],
                title=t["title"],
                description=t["description"],
                acceptance_checks=t["acceptance_checks"],
                estimated_complexity=complexity,
                capability_type=capability,
                verification_step=t.get("verification_step", ""),
            )
        )

    # Build edges
    edges = [
        TaskDependency(
            from_task_id=e["from_task_id"],
            to_task_id=e["to_task_id"],
        )
        for e in parsed.get("edges", [])
    ]

    dag = TaskDAG(
        tasks=tasks,
        edges=edges,
        plan_revision=parsed.get("plan_revision", 1),
    )

    # Validate acyclicity
    validate_dag(dag)

    plan = ImplementationPlan(
        phases=parsed["phases"],
        dag=dag,
        plan_revision=dag.plan_revision,
    )

    # Commit plan to docs/plans/
    _commit_plan_to_repo(parsed, plan, state)

    return {
        "run_status": RunStatus.RUNNING,
        "implementation_plan": plan,
    }


def _normalize_complexity(value: str) -> str:
    """Normalize LLM complexity response to xs/s/m/l."""
    mapping = {
        "xs": "xs", "extra-small": "xs", "extra_small": "xs",
        "s": "s", "small": "s", "low": "s",
        "m": "m", "medium": "m", "med": "m",
        "l": "l", "large": "l", "high": "l",
    }
    return mapping.get(value.lower().strip(), "m")


def _normalize_capability(value: str) -> str:
    """Normalize capability type to valid enum value."""
    mapping = {
        "agent_only": "agent_only",
        "agent_with_tools": "agent_with_tools",
        "direct_tool": "direct_tool",
        "agent-only": "agent_only",
        "agent-with-tools": "agent_with_tools",
        "direct-tool": "direct_tool",
    }
    return mapping.get(value.lower().strip(), "agent_only")


def _render_plan_markdown(parsed: dict[str, Any], plan: ImplementationPlan) -> str:
    """Render the plan as a markdown document following plan agent format."""
    lines: list[str] = []

    lines.append("# Implementation Plan\n")

    if parsed.get("plan_summary"):
        lines.append(f"## Overview\n\n{parsed['plan_summary']}\n")

    if parsed.get("architecture_decisions"):
        lines.append("## Architecture Decisions\n")
        for decision in parsed["architecture_decisions"]:
            lines.append(f"- {decision}")
        lines.append("")

    # Tasks grouped by phase
    lines.append("## Task List\n")
    phase_tasks: dict[str, list[dict[str, Any]]] = {}
    for t in parsed["tasks"]:
        phase = t.get("phase", "Unassigned")
        phase_tasks.setdefault(phase, []).append(t)

    for phase in plan.phases:
        lines.append(f"### {phase}\n")
        for t in phase_tasks.get(phase, []):
            lines.append(f"- [ ] **{t['task_id']}**: {t['title']}")
            lines.append(f"  - Description: {t['description']}")
            lines.append(f"  - Acceptance: {'; '.join(t['acceptance_checks'])}")
            lines.append(f"  - Verify: `{t.get('verification_step', 'N/A')}`")
            lines.append(f"  - Complexity: {t.get('estimated_complexity', '?')}")
            if t.get("files_touched"):
                lines.append(f"  - Files: {', '.join(t['files_touched'])}")
            lines.append("")

    # Checkpoints
    if parsed.get("checkpoints"):
        lines.append("## Checkpoints\n")
        for cp in parsed["checkpoints"]:
            lines.append(f"### {cp.get('name', 'Checkpoint')}")
            lines.append(f"After tasks: {', '.join(cp.get('after_tasks', []))}\n")
            for criterion in cp.get("criteria", []):
                lines.append(f"- [ ] {criterion}")
            lines.append("")

    # Dependency graph
    if parsed.get("edges"):
        lines.append("## Dependency Graph\n")
        lines.append("```")
        for edge in parsed["edges"]:
            lines.append(f"{edge['from_task_id']} → {edge['to_task_id']}")
        lines.append("```")
        lines.append("")

    # Parallelization
    if parsed.get("parallelization"):
        lines.append("## Parallelization Opportunities\n")
        for item in parsed["parallelization"]:
            lines.append(f"- {item}")
        lines.append("")

    # Risks
    if parsed.get("risks"):
        lines.append("## Risks and Mitigations\n")
        lines.append("| Risk | Impact | Mitigation |")
        lines.append("|------|--------|------------|")
        for risk in parsed["risks"]:
            lines.append(
                f"| {risk.get('risk', '')} | {risk.get('impact', '')} | {risk.get('mitigation', '')} |"
            )
        lines.append("")

    return "\n".join(lines)


def _commit_plan_to_repo(
    parsed: dict[str, Any], plan: ImplementationPlan, state: GraphState
) -> None:
    """Write plan markdown to ``<workdir>/docs/plans/`` and commit to git."""
    from src.nodes._workspace import get_workdir

    workdir = get_workdir(state)
    cwd = str(workdir)

    plan_dir = workdir / "docs" / "plans"
    plan_dir.mkdir(parents=True, exist_ok=True)

    # Generate filename from first task or spec
    plan_name = "implementation-plan"
    if state.spec and state.spec.artifact_path:
        stem = Path(state.spec.artifact_path).stem
        if stem and stem != "spec":
            plan_name = stem

    plan_path = plan_dir / f"{plan_name}.md"
    markdown = _render_plan_markdown(parsed, plan)
    plan_path.write_text(markdown, encoding="utf-8")

    # Git add and commit
    try:
        rel = plan_path.relative_to(workdir)
        subprocess.run(
            ["git", "add", str(rel)],
            cwd=cwd, capture_output=True, text=True, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", f"docs: add implementation plan ({plan_path.name})"],
            cwd=cwd, capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
