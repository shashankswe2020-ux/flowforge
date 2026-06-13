"""Real task_node implementation — executes tasks via LLM and writes generated
source files into ``state.workdir`` so downstream review/security/test gates
analyze actual code instead of an empty workspace.

When ``FLOWFORGE_DEEP_AGENTS=1`` (T9), each task is dispatched through the
``implementer`` Deep Agent (with ``refactorer`` and ``doc_writer`` sub-agents).
After each task the wrapper scans the generated diff for high-confidence
secrets and blocks the run if any are found (spec §13.14).
"""

from __future__ import annotations

import difflib
import json
import subprocess
from typing import TYPE_CHECKING, Any, Final, cast

from flowforge.config.deep_agents import resolve_deep_agents_enabled
from flowforge.deep_agents import AgentRole
from flowforge.deep_agents.adapters import (
    PathTraversalError,
    _safe_resolve,
    materialize_files,
    persist_files,
)
from flowforge.deep_agents.errors import DeepAgentLimitError
from flowforge.deep_agents.factory import build_deep_agent, run_deep_agent_bounded
from flowforge.deep_agents.secret_scanner import (
    SecretFinding,
    has_blocking_secret,
    scan_diff,
)
from flowforge.nodes._workspace import get_workdir
from flowforge.nodes.capability import LLMProtocol, TaskExecutionResult
from flowforge.nodes.task_executor import execute_task
from flowforge.state.models import (
    DeepAgentTrace,
    GraphState,
    ImplementationPlan,
    RunStatus,
    Task,
    TaskDefinition,
    TaskStatus,
    ToolInvocationRecord,
)

if TYPE_CHECKING:
    from pathlib import Path

    from langchain_core.language_models import BaseChatModel

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

    Two execution modes:

    * **Per-task (Option A — Send fan-out):** when ``state.current_task_id``
      is set (the conditional ``task_fanout_router`` edge sets it via
      ``Send("task_node", {"current_task_id": ...})``), this node runs
      exactly that one task. The graph router re-evaluates the DAG after
      every wave so independent tasks run in parallel and dependents
      wait for their predecessors.
    * **Legacy (all-at-once):** when ``current_task_id`` is ``None``
      (direct unit-test calls), iterates every task in plan order. Kept
      for backward compatibility — the dynamic dispatcher does not use
      this branch.
    """
    if isinstance(state, dict):
        state = GraphState(**state)
    if state.current_task_id is not None:
        return _execute_one_task(state, state.current_task_id, llm=llm)

    plan = state.implementation_plan
    if plan is None or not plan.dag.tasks:
        return {"tasks": []}

    if resolve_deep_agents_enabled():
        deep_result = _run_via_deep_agent(state, llm)
        if deep_result is not None:
            return deep_result
        # Deep path returned None — fall through to legacy execution.

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
        subprocess.run(["git", "add", *rels], cwd=str(workdir), check=True, capture_output=True)
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


# ---------------------------------------------------------------------------
# Per-task execution (Option A — dynamic DAG dispatch via Send)
# ---------------------------------------------------------------------------


def _predecessor_skip_decision(
    plan: ImplementationPlan,
    current_tasks: list[Task],
    task_id: str,
) -> Task | None:
    """If any predecessor terminated unsuccessfully, return a SKIPPED task.

    A predecessor counts as a blocker when its status is FAILED, BLOCKED,
    CANCELLED, or SKIPPED — anything that didn't reach SUCCEEDED. The
    returned ``Task`` records which predecessors blocked the dispatch in
    its ``error_message`` so the reviewer / triager nodes can pick it up.
    """
    by_id: dict[str, Task] = {t.task_id: t for t in current_tasks}
    blockers: list[str] = []
    for edge in plan.dag.edges:
        if edge.to_task_id != task_id:
            continue
        pred = by_id.get(edge.from_task_id)
        if pred is None:
            continue
        if pred.status in {
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
            TaskStatus.SKIPPED,
        }:
            blockers.append(edge.from_task_id)
    if not blockers:
        return None
    definition = next(d for d in plan.dag.tasks if d.task_id == task_id)
    return Task(
        task_id=task_id,
        definition=definition,
        status=TaskStatus.SKIPPED,
        artifacts=[],
        verification_evidence=[],
        error_message=(f"skipped: predecessor task(s) {', '.join(blockers)} did not succeed"),
        idempotency_key=None,
    )


def _execute_one_task(
    state: GraphState,
    task_id: str,
    *,
    llm: LLMProtocol,
) -> dict[str, Any]:
    """Execute a single task identified by ``task_id``.

    Used by the dynamic ``task_fanout_router`` Send fan-out so each
    runnable task becomes its own ``task_node`` invocation. Returns a
    state delta containing exactly one entry in ``tasks``; the
    ``_merge_tasks`` reducer integrates it into the parent state.
    """
    plan = state.implementation_plan
    if plan is None:
        return {"tasks": []}

    definition = next(
        (d for d in plan.dag.tasks if d.task_id == task_id),
        None,
    )
    if definition is None:
        return {"tasks": []}

    skipped = _predecessor_skip_decision(plan, state.tasks, task_id)
    if skipped is not None:
        return {"tasks": [skipped]}

    if resolve_deep_agents_enabled():
        deep_result = _execute_one_via_deep(definition, state, llm)
        if deep_result is not None:
            return deep_result
        # fall through to legacy single-task executor

    return _execute_one_via_legacy(definition, state, llm=llm)


def _execute_one_via_legacy(
    definition: TaskDefinition,
    state: GraphState,
    *,
    llm: LLMProtocol,
) -> dict[str, Any]:
    """Single-task version of the legacy in-process executor."""
    workdir = get_workdir(state)
    task = Task(task_id=definition.task_id, definition=definition)
    result = _execute_with_retry(task, llm=llm)

    written_paths: list[Path] = []
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
    if written_paths:
        _commit_artifacts(workdir, written_paths)

    completed = Task(
        task_id=task.task_id,
        definition=task.definition,
        status=result.status,
        artifacts=list(result.artifacts),
        verification_evidence=list(result.verification_evidence),
        error_message=result.error_message,
        idempotency_key=result.idempotency_key,
    )
    return {"tasks": [completed]}


def _execute_one_via_deep(
    definition: TaskDefinition,
    state: GraphState,
    llm: LLMProtocol,
) -> dict[str, Any] | None:
    """Single-task version of the deep-agent executor.

    Returns ``None`` when the agent produced no usable VFS output, so
    the caller can fall back to the legacy executor.
    """
    workdir = get_workdir(state)
    seed_files = materialize_files(state)
    graph = build_deep_agent(
        role=AgentRole.IMPLEMENTER,
        llm=cast("BaseChatModel", llm),
        workdir=workdir,
    )
    payload: dict[str, Any] = {
        "messages": [
            {"role": "user", "content": _build_task_prompt(definition)},
        ],
        "files": dict(seed_files),
    }
    invocations: list[ToolInvocationRecord] = []
    try:
        result = run_deep_agent_bounded(
            graph,
            payload,
            role=AgentRole.IMPLEMENTER,
            node_name="task_node",
            invocation_sink=invocations,
        )
    except DeepAgentLimitError as exc:
        failed = Task(
            task_id=definition.task_id,
            definition=definition,
            status=TaskStatus.FAILED,
            artifacts=[],
            verification_evidence=[],
            error_message=str(exc),
            idempotency_key=None,
        )
        return {
            "tasks": [failed],
            "deep_agent_traces": {f"task_node:{definition.task_id}": exc.partial_trace},
        }

    result_files = result.get("files") if isinstance(result, dict) else None
    if not isinstance(result_files, dict) or not any(
        isinstance(k, str)
        and k.startswith("vfs:/")
        and not k[len("vfs:/") :].startswith(
            ("findings/", "context/", "subagent/"),
        )
        for k in result_files
    ):
        return None

    raw_messages = result.get("messages")
    messages = (
        [m for m in raw_messages if isinstance(m, dict)] if isinstance(raw_messages, list) else []
    )
    vfs_keys = sorted(k for k in result_files if isinstance(k, str))
    trace = DeepAgentTrace(
        role=AgentRole.IMPLEMENTER,
        messages_digest=DeepAgentTrace.digest_messages(messages),
        vfs_keys=vfs_keys,
        tool_invocations=invocations,
    )

    findings = _scan_files_for_secrets(
        {k: v for k, v in result_files.items() if isinstance(v, str)},
        workdir,
    )
    if has_blocking_secret(findings):
        offending = next(f for f in findings if f.severity.value == "high")
        blocked = Task(
            task_id=definition.task_id,
            definition=definition,
            status=TaskStatus.BLOCKED,
            artifacts=[],
            verification_evidence=[],
            error_message=(
                f"secret_scanner blocked run: {offending.pattern_name} on line {offending.line}"
            ),
            idempotency_key=None,
        )
        return {
            "run_status": RunStatus.BLOCKED,
            "tasks": [blocked],
            "deep_agent_traces": {f"task_node:{definition.task_id}": trace},
        }

    artifacts = persist_files(result, workdir)
    written = [(workdir / a.path).resolve() for a in artifacts]
    if written:
        _commit_artifacts(workdir, written)
    evidence = _extract_verification_evidence(result)
    completed = Task(
        task_id=definition.task_id,
        definition=definition,
        status=TaskStatus.SUCCEEDED,
        artifacts=list(artifacts),
        verification_evidence=evidence,
        error_message=None,
        idempotency_key=None,
    )
    return {
        "tasks": [completed],
        "deep_agent_traces": {f"task_node:{definition.task_id}": trace},
    }


# ---------------------------------------------------------------------------
# Deep Agent variant (T9, spec §13.14) — legacy all-at-once path
# ---------------------------------------------------------------------------


_IMPLEMENTER_VFS_PATH = "vfs:/context/implementer_output.json"
# Cap on-disk reads during pre-persist scanning to bound memory and avoid
# hangs on FIFOs/devices the agent could not normally produce, but a
# pre-existing workdir might contain.
_SCAN_READ_BYTES_CAP: Final[int] = 2 * 1024 * 1024  # 2 MiB


def _build_task_prompt(task_definition: Any) -> str:  # noqa: ANN401 -- TaskDefinition
    acceptance = "\n".join(f"- {c}" for c in task_definition.acceptance_checks) or "- (none)"
    return (
        f"Implement task {task_definition.task_id!r}: {task_definition.title}.\n\n"
        f"Description:\n{task_definition.description}\n\n"
        f"Acceptance checks:\n{acceptance}\n\n"
        f"Verification step: {task_definition.verification_step}\n\n"
        "Follow the TDD cycle. Write the source + test files at their "
        "canonical paths under vfs:/, then write the summary to "
        f"{_IMPLEMENTER_VFS_PATH}. Do not write secrets \u2014 the wrapper "
        "blocks the run if a high-confidence credential pattern appears in "
        "any added line."
    )


def _scan_files_for_secrets(
    files: dict[str, str],
    workdir: Path,
) -> list[SecretFinding]:
    """Scan a unified-diff between disk and the agent's emitted VFS files.

    For each ``vfs:/<rel>`` entry, compute the diff vs. the existing
    on-disk content (empty if the file is new). The scanner only flags
    *added* lines, so unchanged content cannot trip the regex.
    """
    findings: list[SecretFinding] = []
    for raw_path, new_content in files.items():
        if not isinstance(raw_path, str) or not isinstance(new_content, str):
            continue
        if not raw_path.startswith("vfs:/"):
            continue
        rel = raw_path[len("vfs:/") :]
        if rel.startswith(("findings/", "context/", "subagent/")):
            continue
        # Reject traversal *before* any filesystem read.
        target: Path | None
        try:
            target = _safe_resolve(workdir, rel)
        except PathTraversalError:
            # The agent emitted a path that escapes the workdir.
            # Treat it as if there's nothing on disk; persist_files
            # will reject the same path and abort the write.
            target = None
        old_content = ""
        if target is not None and target.is_file() and not target.is_symlink():
            try:
                with target.open("rb") as fh:
                    raw = fh.read(_SCAN_READ_BYTES_CAP)
                old_content = raw.decode("utf-8", errors="replace")
            except OSError:
                old_content = ""
        diff = "\n".join(
            difflib.unified_diff(
                old_content.splitlines(),
                new_content.splitlines(),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
                lineterm="",
            ),
        )
        findings.extend(scan_diff(diff))
    return findings


def _extract_verification_evidence(result: dict[str, Any]) -> list[str]:
    """Read ``vfs:/context/implementer_output.json`` from the result.

    Returns the ``verification_evidence`` list when present and shaped
    correctly; an empty list otherwise. The summary file lives under
    the read-only ``context/`` namespace so ``persist_files`` would
    skip it — the wrapper consumes it in-memory instead.
    """
    files = result.get("files")
    if not isinstance(files, dict):
        return []
    raw = files.get(_IMPLEMENTER_VFS_PATH)
    if not isinstance(raw, str):
        return []
    try:
        parsed = json.loads(raw, strict=False)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, dict):
        return []
    evidence = parsed.get("verification_evidence", [])
    if not isinstance(evidence, list):
        return []
    return [str(e) for e in evidence]


def _run_via_deep_agent(
    state: GraphState,
    llm: LLMProtocol,
) -> dict[str, Any] | None:
    """Deep Agent variant of ``task_node`` (T9).

    Iterates plan tasks, dispatching each to the ``implementer`` agent.
    For every task the wrapper:

    1. Computes a real ``unified_diff`` between disk and the emitted
       VFS files and runs the secret scanner on the added lines.
    2. **Refuses to persist** any file when a HIGH-confidence secret
       is found — the offending artifact never touches disk.
    3. Otherwise, mirrors the VFS to the workdir and continues.

    Returns ``None`` when a task produces no usable output, signalling
    the caller to fall back to the legacy single-shot executor.
    """
    plan = state.implementation_plan
    if plan is None or not plan.dag.tasks:
        return {"tasks": []}

    workdir = get_workdir(state)
    completed: list[Task] = []
    written_paths: list[Path] = []
    aggregate_invocations: list[ToolInvocationRecord] = []
    aggregate_messages: list[dict[str, object]] = []
    aggregate_vfs_keys: set[str] = set()
    seed_files = materialize_files(state)

    def _build_trace() -> DeepAgentTrace:
        return DeepAgentTrace(
            role=AgentRole.IMPLEMENTER,
            messages_digest=DeepAgentTrace.digest_messages(aggregate_messages),
            vfs_keys=sorted(aggregate_vfs_keys),
            tool_invocations=aggregate_invocations,
        )

    def _absorb(result: dict[str, Any], invocations: list[ToolInvocationRecord]) -> None:
        aggregate_invocations.extend(invocations)
        messages_obj = result.get("messages")
        if isinstance(messages_obj, list):
            aggregate_messages.extend(m for m in messages_obj if isinstance(m, dict))
        files_obj = result.get("files")
        if isinstance(files_obj, dict):
            aggregate_vfs_keys.update(k for k in files_obj if isinstance(k, str))

    for definition in plan.dag.tasks:
        graph = build_deep_agent(
            role=AgentRole.IMPLEMENTER,
            llm=cast("BaseChatModel", llm),
            workdir=workdir,
        )
        payload: dict[str, Any] = {
            "messages": [
                {"role": "user", "content": _build_task_prompt(definition)},
            ],
            "files": dict(seed_files),
        }
        invocations: list[ToolInvocationRecord] = []
        result = run_deep_agent_bounded(
            graph,
            payload,
            role=AgentRole.IMPLEMENTER,
            node_name="task_node",
            invocation_sink=invocations,
        )

        result_files = result.get("files") if isinstance(result, dict) else None
        if not isinstance(result_files, dict) or not any(
            isinstance(k, str)
            and k.startswith("vfs:/")
            and not k[len("vfs:/") :].startswith(
                ("findings/", "context/", "subagent/"),
            )
            for k in result_files
        ):
            return None

        # Pre-flight: scan the diff BEFORE persisting so a planted
        # secret never touches disk.
        findings = _scan_files_for_secrets(
            {k: v for k, v in result_files.items() if isinstance(v, str)},
            workdir,
        )
        if has_blocking_secret(findings):
            offending = next(f for f in findings if f.severity.value == "high")
            blocked_task = Task(
                task_id=definition.task_id,
                definition=definition,
                status=TaskStatus.BLOCKED,
                artifacts=[],
                verification_evidence=[],
                error_message=(
                    f"secret_scanner blocked run: {offending.pattern_name} on line {offending.line}"
                ),
                idempotency_key=None,
            )
            completed.append(blocked_task)
            _absorb(result, invocations)
            # Commit prior successful tasks so partial progress is
            # not orphaned in the workdir.
            if written_paths:
                _commit_artifacts(workdir, written_paths)
            return {
                "run_status": RunStatus.BLOCKED,
                "tasks": completed,
                "deep_agent_traces": {"task_node": _build_trace()},
            }

        artifacts = persist_files(result, workdir)
        for artifact in artifacts:
            target = (workdir / artifact.path).resolve()
            written_paths.append(target)

        evidence = _extract_verification_evidence(result)
        completed.append(
            Task(
                task_id=definition.task_id,
                definition=definition,
                status=TaskStatus.SUCCEEDED,
                artifacts=list(artifacts),
                verification_evidence=evidence,
                error_message=None,
                idempotency_key=None,
            ),
        )
        _absorb(result, invocations)

    if written_paths:
        _commit_artifacts(workdir, written_paths)

    return {
        "tasks": completed,
        "deep_agent_traces": {"task_node": _build_trace()},
    }
