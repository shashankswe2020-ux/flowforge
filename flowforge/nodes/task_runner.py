"""Real task_node implementation — executes tasks via LLM and writes generated
source files into ``state.workdir`` so downstream review/security/test gates
analyze actual code instead of an empty workspace.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from flowforge.nodes._workspace import get_workdir
from flowforge.nodes.capability import LLMProtocol, TaskExecutionResult
from flowforge.nodes.task_executor import execute_task
from flowforge.state.models import GraphState, Task, TaskStatus

MAX_TASK_ATTEMPTS = 3


def _execute_with_retry(task: Task, *, llm: LLMProtocol) -> TaskExecutionResult:
    """Run ``execute_task`` up to ``MAX_TASK_ATTEMPTS`` times until it succeeds.

    A task is retried when the executor returns ``TaskStatus.FAILED`` or raises.
    Returns the last result; if every attempt failed, the result carries
    ``status=FAILED`` and the most recent ``error_message``.
    """
    last_result: TaskExecutionResult | None = None
    last_exc: Exception | None = None
    for _ in range(MAX_TASK_ATTEMPTS):
        try:
            result = execute_task(task, llm=llm)
        except Exception as exc:  # noqa: BLE001 — surface as failure, retry
            last_exc = exc
            continue
        last_result = result
        last_exc = None
        if result.status != TaskStatus.FAILED:
            return result
    if last_result is not None:
        return last_result
    return TaskExecutionResult(
        task_id=task.task_id,
        status=TaskStatus.FAILED,
        artifacts=[],
        verification_evidence=[],
        error_message=str(last_exc) if last_exc else "unknown error",
        idempotency_key=None,
    )


def task_node(state: GraphState, *, llm: LLMProtocol) -> dict[str, Any]:
    """Execute every task in the implementation plan via the LLM.

    For each task definition produced by ``plan_node``:
      1. wraps it in a ``Task`` runtime object
      2. calls ``execute_task`` (with up to ``MAX_TASK_ATTEMPTS`` retries on
         FAILED) which routes to the right capability executor
      3. writes each artifact's ``content`` to ``<workdir>/<artifact.path>``
      4. records the populated ``Task`` (with artifacts) on ``state.tasks``

    The returned ``state.tasks`` is what ``code_review_node``,
    ``security_audit_node``, and ``test_engineer_node`` read to build their
    review prompts, so populating ``artifacts`` here is what allows the gates
    to analyze real code.
    """
    plan = state.implementation_plan
    if plan is None or not plan.dag.tasks:
        return {"tasks": []}

    workdir = get_workdir(state)
    completed: list[Task] = []
    written_paths: list[Path] = []

    for definition in plan.dag.tasks:
        task = Task(task_id=definition.task_id, definition=definition)
        result = _execute_with_retry(task, llm=llm)

        for artifact in result.artifacts:
            if not artifact.content:
                continue
            target = (workdir / artifact.path).resolve()
            try:
                target.relative_to(workdir.resolve())
            except ValueError:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(artifact.content)
            written_paths.append(target)

        completed.append(
            Task(
                task_id=task.task_id,
                definition=task.definition,
                status=result.status,
                artifacts=list(result.artifacts),
                verification_evidence=list(result.verification_evidence),
                error_message=result.error_message,
                idempotency_key=result.idempotency_key,
            ),
        )

    if written_paths:
        _commit_artifacts(workdir, written_paths)

    return {"tasks": completed}


def _commit_artifacts(workdir: Path, paths: list[Path]) -> None:
    """Stage and commit generated source files in the target repo."""
    if not (workdir / ".git").exists():
        return
    rels = [str(p.relative_to(workdir)) for p in paths]
    try:
        subprocess.run(
            ["git", "add", *rels], cwd=str(workdir), check=True, capture_output=True
        )
        subprocess.run(
            [
                "git",
                "commit",
                "-m",
                f"feat: implement {len(paths)} task artifact(s)",
                "--quiet",
            ],
            cwd=str(workdir),
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        pass
