"""FlowForge configuration management.

Stores user preferences at ~/.flowforge/config.json.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
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

    def save(self) -> None:
        """Persist config to disk."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        # Secure the config file
        os.chmod(CONFIG_FILE, 0o600)

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
