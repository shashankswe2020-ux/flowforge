"""GraphState ⇄ Deep Agent (messages + VFS) adapters (T5).

Implements the §5.4 / §8.1 contract. Three exported helpers:

* :func:`materialize_files` — build the VFS dict the Deep Agent is
  invoked with: prior-context snapshots (spec, plan, findings) plus
  every task artifact. Outputs use a ``vfs:/`` prefix to namespace
  framework-managed entries from agent-authored ones.
* :func:`persist_files` — mirror the post-invoke VFS to disk
  (workdir-relative), with diff-aware writes and path-traversal
  rejection. Read-only sentinel namespaces (``findings/``,
  ``context/``, ``subagent/``) are skipped.
* :func:`extract_findings` — parse canonical
  ``vfs:/findings/*.json`` entries into
  :class:`flowforge.state.models.Finding` instances.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Final

from flowforge.state.models import Finding, TaskArtifact

if TYPE_CHECKING:
    from flowforge.state.models import GraphState

__all__ = [
    "PathTraversalError",
    "extract_findings",
    "materialize_files",
    "persist_files",
]

_VFS_PREFIX: Final[str] = "vfs:/"
_FINDINGS_PREFIX: Final[str] = "findings/"
_CONTEXT_PREFIX: Final[str] = "context/"
_SUBAGENT_PREFIX: Final[str] = "subagent/"

_SENTINEL_PREFIXES: Final[tuple[str, ...]] = (
    _FINDINGS_PREFIX,
    _CONTEXT_PREFIX,
    _SUBAGENT_PREFIX,
)

_FINDING_SOURCES: Final[tuple[tuple[str, str], ...]] = (
    ("vfs:/context/findings/review.json", "review_findings"),
    ("vfs:/context/findings/security.json", "security_findings"),
    ("vfs:/context/findings/test.json", "test_findings"),
)

# ~8 000 chars ≈ 2 000 tokens — keeps the full VFS well inside a 12k-token
# prompt limit for small-context models (Copilot gpt-4o-mini = 12 288 tokens).
# Larger models are unaffected since truncation only fires when content exceeds
# the cap.
_VFS_CONTEXT_CHAR_LIMIT: Final[int] = 8_000
_VFS_ARTIFACT_CHAR_LIMIT: Final[int] = 4_000


def _truncate(text: str, limit: int) -> str:
    """Return ``text`` capped at ``limit`` chars with a truncation notice."""
    if len(text) <= limit:
        return text
    kept = text[:limit]
    omitted = len(text) - limit
    return f"{kept}\n... [{omitted} chars truncated — read vfs:/ directly for full content]"


class PathTraversalError(ValueError):
    """Raised when a VFS path resolves outside its workdir."""


def _strip_vfs(path: str) -> str:
    return path[len(_VFS_PREFIX):] if path.startswith(_VFS_PREFIX) else path


def _is_sentinel(rel: str) -> bool:
    return any(rel.startswith(prefix) for prefix in _SENTINEL_PREFIXES)


def _safe_resolve(workdir: Path, rel: str) -> Path:
    candidate = Path(rel)
    if candidate.is_absolute():
        raise PathTraversalError(f"absolute path not allowed: {rel}")
    workdir_resolved = workdir.resolve()
    target = (workdir_resolved / candidate).resolve()
    try:
        target.relative_to(workdir_resolved)
    except ValueError as exc:
        raise PathTraversalError(
            f"path {rel!r} escapes workdir {workdir_resolved!s}",
        ) from exc
    return target


def materialize_files(state: GraphState) -> dict[str, str]:
    """Build the VFS dict to seed the Deep Agent with.

    Layout:

    * ``vfs:/<artifact.path>`` — content of every ``TaskArtifact`` on
      ``state.tasks``.
    * ``vfs:/context/clarified_request.json`` — JSON dump of
      ``state.clarified_request``.
    * ``vfs:/context/spec.json`` — JSON dump of ``state.spec``.
    * ``vfs:/context/plan.json`` — JSON dump of
      ``state.implementation_plan``.
    * ``vfs:/context/findings/{review,security,test}.json`` — JSON
      arrays of prior findings (when non-empty).

    Each context entry is capped at :data:`_VFS_CONTEXT_CHAR_LIMIT`
    characters to avoid overflowing small-context models.

    Raises:
        PathTraversalError: If any artifact path is absolute or
            contains ``..`` segments.
    """
    files: dict[str, str] = {}

    for task in state.tasks:
        for artifact in task.artifacts:
            rel = artifact.path
            parts = Path(rel).parts
            if Path(rel).is_absolute() or ".." in parts:
                raise PathTraversalError(
                    f"artifact path {rel!r} on task {task.task_id!r} "
                    "must be relative without '..' segments",
                )
            # Only pre-load small files; large ones are already on disk and
            # the agent can discover + read them via ls/glob/read_file.
            if len(artifact.content) <= _VFS_ARTIFACT_CHAR_LIMIT:
                files[f"{_VFS_PREFIX}{rel}"] = artifact.content

    if state.clarified_request is not None:
        files["vfs:/context/clarified_request.json"] = _truncate(
            state.clarified_request.model_dump_json(), _VFS_CONTEXT_CHAR_LIMIT,
        )

    if state.spec is not None:
        files["vfs:/context/spec.json"] = _truncate(
            state.spec.model_dump_json(), _VFS_CONTEXT_CHAR_LIMIT,
        )

    if state.implementation_plan is not None:
        files["vfs:/context/plan.json"] = _truncate(
            state.implementation_plan.model_dump_json(), _VFS_CONTEXT_CHAR_LIMIT,
        )

    for vfs_path, attr in _FINDING_SOURCES:
        findings: list[Finding] = getattr(state, attr)
        if findings:
            files[vfs_path] = _truncate(
                json.dumps([f.model_dump(mode="json") for f in findings]),
                _VFS_CONTEXT_CHAR_LIMIT,
            )

    return files


def persist_files(
    result: dict[str, object],
    workdir: Path,
) -> list[TaskArtifact]:
    """Mirror the agent's VFS to ``workdir`` with diff-aware writes.

    Returns the list of ``TaskArtifact`` records for paths whose on-disk
    content was created or changed. Sentinel namespaces (``findings/``,
    ``context/``, ``subagent/``) are read-only and skipped.

    Raises:
        PathTraversalError: If a path is absolute or escapes ``workdir``.
    """
    files = result.get("files") if isinstance(result, dict) else None
    if not isinstance(files, dict):
        return []

    workdir.mkdir(parents=True, exist_ok=True)

    artifacts: list[TaskArtifact] = []
    for raw_path, content in files.items():
        if not isinstance(raw_path, str) or not isinstance(content, str):
            continue
        rel = _strip_vfs(raw_path)
        if _is_sentinel(rel):
            continue

        target = _safe_resolve(workdir, rel)
        if target.exists() and target.read_text(encoding="utf-8") == content:
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        artifacts.append(
            TaskArtifact(
                artifact_id=rel,
                artifact_type="file",
                path=rel,
                fingerprint=str(len(content)),
                content=content,
            ),
        )
    return artifacts


def extract_findings(result: dict[str, object]) -> list[Finding]:
    """Parse ``vfs:/findings/*.json`` entries into :class:`Finding`.

    Each entry must be a JSON array of Finding-shaped dicts. Files
    outside the ``findings/`` prefix are ignored.

    Raises:
        ValueError: If a findings file is not valid JSON or not a list.
    """
    files = result.get("files") if isinstance(result, dict) else None
    if not isinstance(files, dict):
        return []

    findings: list[Finding] = []
    for raw_path in sorted(files):
        if not isinstance(raw_path, str):
            continue
        rel = _strip_vfs(raw_path)
        if not rel.startswith(_FINDINGS_PREFIX):
            continue
        body = files[raw_path]
        if not isinstance(body, str):
            continue
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"invalid JSON in findings file {raw_path!r}: {exc}",
            ) from exc
        if not isinstance(payload, list):
            raise ValueError(
                f"findings file {raw_path!r} must contain a JSON array",
            )
        findings.extend(Finding.model_validate(item) for item in payload)
    return findings
