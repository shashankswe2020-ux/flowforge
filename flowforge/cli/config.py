"""FlowForge configuration management.

Stores user preferences at ~/.flowforge/config.json.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

CONFIG_DIR = Path.home() / ".flowforge"
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class FlowForgeConfig:
    """User configuration for FlowForge."""

    provider: str = "copilot"  # copilot | codex | claude_code
    model: str = "gpt-4o-mini"
    api_base: str = "https://models.inference.ai.azure.com"
    temperature: float = 0.0
    max_tokens: int = 4096
    default_private: bool = True
    langgraph_port: int = 8123
    # Default flipped to True in T14 (Phase 4 rollout). The legacy
    # path remains reachable via ``swe-forge run --no-deep-agents``
    # (deprecated for one minor version per spec §13.15).
    deep_agents: bool = True

    def save(self) -> None:
        """Persist config atomically with mode 0o600 from creation.

        Audit MEDIUM-1: ``Path.write_text`` followed by ``os.chmod``
        leaves a TOCTOU window where the file is briefly readable by
        other users. ``tempfile.mkstemp`` creates with ``0o600``
        directly, and ``os.replace`` is atomic and preserves the source
        permissions, eliminating the window.
        """
        parent = CONFIG_FILE.parent
        parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".config.", suffix=".tmp", dir=parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(asdict(self), fh, indent=2)
            os.replace(tmp_path, CONFIG_FILE)
        except BaseException:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    @classmethod
    def load(cls) -> FlowForgeConfig:
        """Load config from disk, or return defaults."""
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        return cls()

    @classmethod
    def exists(cls) -> bool:
        """Check if config file exists."""
        return CONFIG_FILE.exists()
