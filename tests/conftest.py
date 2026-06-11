"""Shared test fixtures and configuration for FlowForge test suite."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _isolated_workdir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """Redirect node-helper file writes away from the repo root.

    Several node helpers (``_commit_review_to_repo``,
    ``_commit_audit_to_repo``, ``_commit_report_to_repo`` and friends)
    fall back to ``Path.cwd()`` when ``state.workdir`` is unset and
    write markdown into ``./docs/{reviews,security-audits,test-reports}/``.
    Without this isolation every run pollutes the repo. Chdir'ing into
    a per-test ``tmp_path`` redirects those writes; tests that set
    ``state.workdir`` explicitly are unaffected.
    """
    monkeypatch.chdir(tmp_path)
    yield tmp_path

