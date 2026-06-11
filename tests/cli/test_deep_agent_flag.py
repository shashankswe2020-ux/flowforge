"""Tests for the ``--use-deep-agents`` / ``--no-deep-agents`` CLI flag (T11).

Covers the spec §9 / plan T11 acceptance criteria:

* ``swe-forge run --help`` lists both flags.
* ``FlowForgeConfig`` carries a ``deep_agents`` boolean that survives a
  ``save`` / ``load`` round-trip and is written with mode ``0o600``.
* ``build_live_graph()`` dispatches to
  :func:`flowforge.graph.builder.build_deep_agent_graph` when the
  resolved value is ``True``.
"""

from __future__ import annotations

import os
import stat
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

from flowforge.cli.config import FlowForgeConfig
from flowforge.cli.main import cli
from flowforge.config.deep_agents import DEEP_AGENTS_ENV_VAR

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


class TestRunHelp:
    def test_help_lists_both_flags(self) -> None:
        result = CliRunner().invoke(cli, ["run", "--help"])
        assert result.exit_code == 0, result.output
        assert "--use-deep-agents" in result.output
        assert "--no-deep-agents" in result.output


# ---------------------------------------------------------------------------
# Config file
# ---------------------------------------------------------------------------


class TestFlowForgeConfigDeepAgents:
    def test_default_is_false(self) -> None:
        assert FlowForgeConfig().deep_agents is False

    def test_roundtrip(self) -> None:
        FlowForgeConfig(deep_agents=True).save()
        loaded = FlowForgeConfig.load()
        assert loaded.deep_agents is True

    def test_file_mode_is_0600(self) -> None:
        FlowForgeConfig(deep_agents=True).save()
        from flowforge.cli.config import CONFIG_FILE

        mode = stat.S_IMODE(os.stat(CONFIG_FILE).st_mode)
        assert mode == 0o600

    def test_save_tightens_existing_loose_perms(self) -> None:
        """Audit MEDIUM-1: save() must atomically replace, not create-then-chmod.

        A pre-existing file with looser permissions (e.g. 0o644) must
        end up at 0o600 after ``save()``.
        """
        from flowforge.cli.config import CONFIG_FILE

        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text("{}", encoding="utf-8")
        os.chmod(CONFIG_FILE, 0o644)
        assert stat.S_IMODE(os.stat(CONFIG_FILE).st_mode) == 0o644

        FlowForgeConfig(deep_agents=True).save()

        assert stat.S_IMODE(os.stat(CONFIG_FILE).st_mode) == 0o600


# ---------------------------------------------------------------------------
# build_live_graph dispatch
# ---------------------------------------------------------------------------


class TestBuildLiveGraphDispatch:
    def test_dispatches_to_deep_agent_graph_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv(DEEP_AGENTS_ENV_VAR, "1")

        called: dict[str, object] = {}

        def fake_deep(llm: object) -> str:
            called["deep"] = llm
            return "deep-graph"

        def fake_real(llm: object) -> str:
            called["real"] = llm
            return "real-graph"

        monkeypatch.setattr(
            "flowforge.graph.builder.build_deep_agent_graph", fake_deep,
        )
        monkeypatch.setattr(
            "flowforge.graph.builder.build_real_graph", fake_real,
        )

        from flowforge.graph.builder import build_live_graph

        result = build_live_graph()
        assert result == "deep-graph"
        assert "deep" in called
        assert "real" not in called

    def test_falls_back_to_real_graph_when_disabled(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.delenv(DEEP_AGENTS_ENV_VAR, raising=False)

        called: dict[str, object] = {}

        def fake_deep(llm: object) -> str:
            called["deep"] = llm
            return "deep-graph"

        def fake_real(llm: object) -> str:
            called["real"] = llm
            return "real-graph"

        monkeypatch.setattr(
            "flowforge.graph.builder.build_deep_agent_graph", fake_deep,
        )
        monkeypatch.setattr(
            "flowforge.graph.builder.build_real_graph", fake_real,
        )

        from flowforge.graph.builder import build_live_graph

        result = build_live_graph()
        assert result == "real-graph"
        assert "real" in called
        assert "deep" not in called
