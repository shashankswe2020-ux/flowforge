"""Tests for ``flowforge.config.deep_agents.resolve_deep_agents_enabled`` (T11).

Resolution priority (high → low):

1. Explicit CLI value (``cli_value`` argument);
2. ``FLOWFORGE_DEEP_AGENTS`` environment variable;
3. ``deep_agents`` field on the persisted ``FlowForgeConfig``;
4. Hard-coded default (``False``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from flowforge.cli.config import FlowForgeConfig
from flowforge.config.deep_agents import (
    DEEP_AGENTS_ENV_VAR,
    resolve_deep_agents_enabled,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Redirect config file lookups to a tmp dir and clear the env var."""
    config_dir = tmp_path / ".flowforge"
    monkeypatch.setattr(
        "flowforge.cli.config.CONFIG_DIR", config_dir, raising=True,
    )
    monkeypatch.setattr(
        "flowforge.cli.config.CONFIG_FILE",
        config_dir / "config.json",
        raising=True,
    )
    monkeypatch.delenv(DEEP_AGENTS_ENV_VAR, raising=False)


class TestDefault:
    def test_default_is_true(self) -> None:
        # T14 — fallback flipped to True for Phase 4 default-on rollout.
        assert resolve_deep_agents_enabled() is True


class TestEnvVar:
    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on"])
    def test_truthy_values(
        self, monkeypatch: pytest.MonkeyPatch, raw: str,
    ) -> None:
        monkeypatch.setenv(DEEP_AGENTS_ENV_VAR, raw)
        assert resolve_deep_agents_enabled() is True

    @pytest.mark.parametrize("raw", ["0", "false", "FALSE", "no", "off"])
    def test_falsy_values(
        self, monkeypatch: pytest.MonkeyPatch, raw: str,
    ) -> None:
        monkeypatch.setenv(DEEP_AGENTS_ENV_VAR, raw)
        assert resolve_deep_agents_enabled() is False

    def test_invalid_value_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(DEEP_AGENTS_ENV_VAR, "maybe")
        with pytest.raises(ValueError, match=DEEP_AGENTS_ENV_VAR):
            resolve_deep_agents_enabled()


class TestConfigFile:
    def test_config_file_true(self) -> None:
        FlowForgeConfig(deep_agents=True).save()
        assert resolve_deep_agents_enabled() is True

    def test_config_file_false(self) -> None:
        FlowForgeConfig(deep_agents=False).save()
        assert resolve_deep_agents_enabled() is False


class TestPriority:
    def test_cli_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(DEEP_AGENTS_ENV_VAR, "1")
        assert resolve_deep_agents_enabled(cli_value=False) is False

    def test_cli_overrides_config(self) -> None:
        FlowForgeConfig(deep_agents=True).save()
        assert resolve_deep_agents_enabled(cli_value=False) is False

    def test_env_overrides_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        FlowForgeConfig(deep_agents=False).save()
        monkeypatch.setenv(DEEP_AGENTS_ENV_VAR, "1")
        assert resolve_deep_agents_enabled() is True

    def test_config_used_when_env_absent(self) -> None:
        FlowForgeConfig(deep_agents=True).save()
        assert resolve_deep_agents_enabled() is True
