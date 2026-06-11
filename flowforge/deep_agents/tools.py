"""FlowForge Deep Agent tool library + safety policy.

Lands in T2. The T1 scaffold declares the ``PathTraversalError`` type
and a ``_safe_path`` stub so the tool layer can be type-checked and
imported by ``factory.py`` without circular dependencies.

See spec §6 for the full tool inventory.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class PathTraversalError(ValueError):
    """Raised when a tool argument resolves outside the agent ``workdir``.

    Tools must never operate on paths that escape the per-run workdir
    via ``..``, absolute paths, or symlinks. Enforced centrally by
    ``_safe_path`` so individual tools stay narrow.
    """


def _safe_path(workdir: Path, candidate: str | Path) -> Path:
    """Resolve ``candidate`` and confirm it lives inside ``workdir``.

    Args:
        workdir: Per-run agent workdir; the only writeable root.
        candidate: User-supplied path (relative or absolute).

    Returns:
        The resolved, real path — guaranteed to be inside ``workdir``.

    Raises:
        PathTraversalError: If the resolved path escapes ``workdir``.
        NotImplementedError: T1 scaffold; implementation lands in T2.
    """

    raise NotImplementedError("_safe_path lands in T2")


__all__ = ["PathTraversalError", "_safe_path"]
