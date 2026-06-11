"""A/B harness — legacy vs Deep Agent ``task_node`` over 5 fixture tasks (T9).

Runs the same input through both code paths with mocked agents and asserts
shape parity:

* same set of generated file paths
* same final ``TaskStatus``
* deep variant additionally emits ``deep_agent_traces['task_node']``

Each fixture is a small, focused implementation task; the planted secret
fixture is the negative control proving the scanner blocks the deep run
while the legacy executor (which has no scanner) writes the file unchecked
— the harness asserts the asymmetry as part of the contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from flowforge.deep_agents import AgentRole
from flowforge.nodes import task_runner
from flowforge.state.models import (
    CapabilityType,
    GraphState,
    ImplementationPlan,
    RunStatus,
    TaskDAG,
    TaskDefinition,
    TaskStatus,
    ToolInvocationRecord,
)
from tests.mocks import MockLLM

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class FixtureTask:
    name: str
    definition: TaskDefinition
    legacy_artifact_path: str
    legacy_artifact_content: str
    deep_vfs: dict[str, str]
    expects_block: bool = False


def _td(
    task_id: str,
    title: str,
    *,
    capability: CapabilityType = CapabilityType.AGENT_ONLY,
) -> TaskDefinition:
    return TaskDefinition(
        task_id=task_id,
        title=title,
        description=f"Implement {title}",
        acceptance_checks=["acceptance check"],
        estimated_complexity="xs",
        capability_type=capability,
        verification_step="pytest -q",
    )


_FIXTURES: tuple[FixtureTask, ...] = (
    FixtureTask(
        name="cli_greet",
        definition=_td("t1", "CLI greet"),
        legacy_artifact_path="src/greet.py",
        legacy_artifact_content="def greet():\n    return 'hi'\n",
        deep_vfs={"vfs:/src/greet.py": "def greet():\n    return 'hi'\n"},
    ),
    FixtureTask(
        name="parser",
        definition=_td("t2", "JSON parser"),
        legacy_artifact_path="src/parser.py",
        legacy_artifact_content="import json\n\ndef parse(s):\n    return json.loads(s)\n",
        deep_vfs={
            "vfs:/src/parser.py": "import json\n\ndef parse(s):\n    return json.loads(s)\n",
        },
    ),
    FixtureTask(
        name="config_loader",
        definition=_td("t3", "Config loader"),
        legacy_artifact_path="src/config.py",
        legacy_artifact_content="def load(path):\n    return {}\n",
        deep_vfs={"vfs:/src/config.py": "def load(path):\n    return {}\n"},
    ),
    FixtureTask(
        name="multi_file",
        definition=_td("t4", "Helper + test"),
        legacy_artifact_path="src/helper.py",
        legacy_artifact_content="def helper():\n    return 1\n",
        deep_vfs={
            "vfs:/src/helper.py": "def helper():\n    return 1\n",
            "vfs:/tests/test_helper.py": (
                "from src.helper import helper\n\n"
                "def test():\n    assert helper() == 1\n"
            ),
        },
    ),
    FixtureTask(
        name="planted_aws_secret",
        definition=_td("t5", "Config with leaked key"),
        legacy_artifact_path="src/cfg.py",
        legacy_artifact_content='AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n',
        deep_vfs={"vfs:/src/cfg.py": 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n'},
        expects_block=True,
    ),
)


def _state_for(workdir: Path, fixture: FixtureTask) -> GraphState:
    workdir.mkdir(parents=True, exist_ok=True)
    plan = ImplementationPlan(
        phases=["Phase 1"],
        dag=TaskDAG(tasks=[fixture.definition], edges=[]),
    )
    return GraphState(
        request="ab harness",
        run_status=RunStatus.RUNNING,
        workdir=str(workdir),
        implementation_plan=plan,
    )


def _legacy_response(fixture: FixtureTask) -> str:
    return json.dumps(
        {
            "status": "succeeded",
            "artifacts": [
                {
                    "artifact_id": "a1",
                    "artifact_type": "source",
                    "path": fixture.legacy_artifact_path,
                    "fingerprint": "x",
                    "content": fixture.legacy_artifact_content,
                },
            ],
            "verification_evidence": ["legacy ran"],
        },
    )


def _patch_deep(
    monkeypatch: pytest.MonkeyPatch, fixture: FixtureTask,
) -> None:
    monkeypatch.setattr(task_runner, "build_deep_agent", lambda **_: object())
    monkeypatch.setattr(task_runner, "_commit_artifacts", lambda *a, **k: None)

    def fake_run(
        graph: object,  # noqa: ARG001
        payload: dict[str, Any],  # noqa: ARG001
        *,
        role: AgentRole,
        node_name: str,  # noqa: ARG001
        invocation_sink: list[ToolInvocationRecord] | None = None,
        **_: object,
    ) -> dict[str, Any]:
        assert role == AgentRole.IMPLEMENTER
        if invocation_sink is not None:
            invocation_sink.append(
                ToolInvocationRecord(tool="task", ok=True, parent="refactorer"),
            )
        return {
            "messages": [{"role": "assistant", "content": "implementer"}],
            "files": dict(fixture.deep_vfs),
        }

    monkeypatch.setattr(task_runner, "run_deep_agent_bounded", fake_run)


@pytest.mark.parametrize("fixture", _FIXTURES, ids=lambda f: f.name)
class TestImplementerABHarness:
    def test_legacy_path_produces_artifact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixture: FixtureTask,
    ) -> None:
        monkeypatch.delenv("FLOWFORGE_DEEP_AGENTS", raising=False)
        monkeypatch.setattr(task_runner, "_commit_artifacts", lambda *a, **k: None)
        state = _state_for(tmp_path / "legacy", fixture)
        llm = MockLLM(responses=[_legacy_response(fixture)])
        result = task_runner.task_node(state, llm=llm)
        # Legacy executor has no secret scanner — every fixture writes the file.
        assert (tmp_path / "legacy" / fixture.legacy_artifact_path).exists()
        assert result["tasks"][0].status == TaskStatus.SUCCEEDED

    def test_deep_path_matches_or_blocks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixture: FixtureTask,
    ) -> None:
        monkeypatch.setenv("FLOWFORGE_DEEP_AGENTS", "1")
        _patch_deep(monkeypatch, fixture)
        state = _state_for(tmp_path / "deep", fixture)
        result = task_runner.task_node(state, llm=MagicMock())

        if fixture.expects_block:
            assert result["run_status"] == RunStatus.BLOCKED
            assert result["tasks"][0].status == TaskStatus.BLOCKED
        else:
            assert result["tasks"][0].status == TaskStatus.SUCCEEDED
            for vfs_path in fixture.deep_vfs:
                rel = vfs_path[len("vfs:/"):]
                assert (tmp_path / "deep" / rel).exists()
        assert "task_node" in result["deep_agent_traces"]

    def test_legacy_and_deep_emit_comparable_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixture: FixtureTask,
    ) -> None:
        """Both code paths target the same set of repo-relative paths."""
        if fixture.expects_block:
            pytest.skip("blocked deep run does not produce artifacts to compare")

        monkeypatch.delenv("FLOWFORGE_DEEP_AGENTS", raising=False)
        monkeypatch.setattr(task_runner, "_commit_artifacts", lambda *a, **k: None)
        legacy_state = _state_for(tmp_path / "legacy", fixture)
        legacy_result = task_runner.task_node(
            legacy_state, llm=MockLLM(responses=[_legacy_response(fixture)]),
        )
        legacy_paths = {a.path for a in legacy_result["tasks"][0].artifacts}

        monkeypatch.setenv("FLOWFORGE_DEEP_AGENTS", "1")
        _patch_deep(monkeypatch, fixture)
        deep_state = _state_for(tmp_path / "deep", fixture)
        deep_result = task_runner.task_node(deep_state, llm=MagicMock())
        deep_paths = {a.path for a in deep_result["tasks"][0].artifacts}

        # Legacy fixture emits one canonical file; deep may emit more (e.g. tests).
        assert legacy_paths.issubset(deep_paths)
