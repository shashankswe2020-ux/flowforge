"""Workspace helpers for nodes that write files and run git/gh commands.

Pipeline-generated artifacts are written into a target project directory
(``state.workdir``) rather than the directory the pipeline runs from, so the
generated project is committed to its own repo.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.state.models import GraphState


def get_workdir(state: GraphState) -> Path:
    """Return the working directory for artifacts. Falls back to cwd."""
    if state.workdir:
        return Path(state.workdir)
    return Path.cwd()


def slugify(text: str, max_words: int = 4) -> str:
    """Convert free text into a kebab-case slug suitable for repo names."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    if not words:
        return "flowforge-project"
    return "-".join(words[:max_words])
