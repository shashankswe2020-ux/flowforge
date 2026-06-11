"""E2E demo run for the canonical ``build tic-tac-toe web app`` prompt (T13).

These tests drive the full :class:`PipelineRunner` against a deterministic
:class:`MockLLM` so the nightly workflow can exercise the whole pipeline
without touching the real LLM. The acceptance criteria from
``docs/plans/task-1-deep-agents-enhancement.md`` (T13):

* the canonical demo prompt completes with ``run_status`` of
  ``succeeded`` or ``blocked``;
* both the legacy and deep-flag-on configurations produce the same
  generated artifacts;
* setting ``OPENAI_API_KEY=invalid`` does not cause a real LLM call —
  the run still succeeds on the mocked responses (spec §13.12).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from flowforge.runner.pipeline import PipelineRunner
from flowforge.state.models import RunStatus

if TYPE_CHECKING:
    from pathlib import Path


_DEMO_PROMPT = "build tic-tac-toe web app"


class _MockLLMResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _MockLLM:
    """Returns canned JSON responses in declaration order."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = [json.dumps(r) for r in responses]
        self._call_count = 0

    def invoke(self, _prompt: str) -> _MockLLMResponse:
        idx = self._call_count
        self._call_count += 1
        if idx >= len(self._responses):
            return _MockLLMResponse('{"findings": []}')
        return _MockLLMResponse(self._responses[idx])

    @property
    def call_count(self) -> int:
        return self._call_count


def _demo_responses() -> list[dict[str, Any]]:
    """Canned responses for the tic-tac-toe demo prompt.

    Mirrors the response order consumed by ``PipelineRunner``:
    clarification → spec → plan → task execution × N → code review
    → security audit → (triage runs only when there are findings).
    """
    return [
        # 1. clarification
        {
            "solution_type": "web_app",
            "scope_size": "small",
            "target_users": "casual players",
            "must_have": ["3x3 grid", "two-player turn taking", "win detection"],
            "nice_to_have": ["score history"],
            "constraints": ["browser-only", "vanilla JS"],
            "success_criteria": ["X and O alternate", "winner announced"],
            "tech_preferences": ["html", "css", "javascript"],
            "summary": "A static HTML/CSS/JS tic-tac-toe game playable in a browser.",
        },
        # 2. spec
        {
            "artifact_path": "docs/spec.md",
            "summary": "Two-player tic-tac-toe in a single static page.",
            "acceptance_criteria": [
                "render 3x3 grid",
                "alternate X and O on click",
                "detect win and announce result",
                "reset button restarts the game",
            ],
            "assumptions": ["no backend"],
            "open_questions": [],
        },
        # 3. plan
        {
            "phases": ["scaffold", "logic", "polish"],
            "tasks": [
                {
                    "task_id": "t1",
                    "title": "Scaffold static page",
                    "description": "Create index.html with the 3x3 grid markup.",
                    "acceptance_checks": ["index.html renders 9 cells"],
                    "estimated_complexity": "s",
                    "capability_type": "agent_only",
                    "verification_step": "open index.html in a browser",
                },
                {
                    "task_id": "t2",
                    "title": "Game logic",
                    "description": "Implement turn taking and win detection in app.js.",
                    "acceptance_checks": ["X and O alternate", "winner announced"],
                    "estimated_complexity": "m",
                    "capability_type": "agent_only",
                    "verification_step": "click through a winning sequence",
                },
            ],
            "edges": [],
        },
        # 4. task t1 execution
        {
            "files": [
                {
                    "path": "index.html",
                    "content": (
                        "<!doctype html><html><head>"
                        "<link rel=\"stylesheet\" href=\"style.css\"></head>"
                        "<body><h1>Tic-Tac-Toe</h1>"
                        "<div id=\"board\"></div>"
                        "<button id=\"reset\">Reset</button>"
                        "<script src=\"app.js\"></script></body></html>"
                    ),
                },
                {
                    "path": "style.css",
                    "content": "#board{display:grid;grid-template-columns:repeat(3,80px);}\n",
                },
            ],
            "verification_evidence": ["index.html opens, 3x3 grid visible"],
        },
        # 5. task t2 execution
        {
            "files": [
                {
                    "path": "app.js",
                    "content": (
                        "// turn-taking and win detection for tic-tac-toe\n"
                        "const cells = Array(9).fill('');\n"
                        "let turn = 'X';\n"
                        "function check(){/* … */}\n"
                    ),
                },
            ],
            "verification_evidence": ["clicked through to a winning row"],
        },
        # 6. code review
        {"findings": []},
        # 7. security audit
        {"findings": []},
    ]


@pytest.fixture
def _demo_llm() -> _MockLLM:
    return _MockLLM(_demo_responses())


def _run_demo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    deep_flag: bool,
) -> PipelineRunner:
    monkeypatch.setenv("FLOWFORGE_DEEP_AGENTS", "1" if deep_flag else "0")
    llm = _MockLLM(_demo_responses())
    runner = PipelineRunner(llm, output_dir=tmp_path)
    runner.run(_DEMO_PROMPT, skip_github=True)
    return runner


class TestCanonicalDemoRun:
    """The tic-tac-toe demo must succeed on the mocked pipeline."""

    @pytest.mark.parametrize("deep_flag", [False, True], ids=["legacy", "deep"])
    def test_demo_run_completes_with_terminal_status(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        deep_flag: bool,
    ) -> None:
        runner = _run_demo(tmp_path, monkeypatch, deep_flag=deep_flag)

        # Acceptance: status is succeeded or blocked (per T13 — a
        # `blocked` run is still a valid demo outcome because the
        # quality gates may surface findings on a more realistic run).
        assert runner.state.run_status in {RunStatus.SUCCEEDED, RunStatus.BLOCKED}

    @pytest.mark.parametrize("deep_flag", [False, True], ids=["legacy", "deep"])
    def test_demo_run_writes_expected_artifacts(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        deep_flag: bool,
    ) -> None:
        runner = _run_demo(tmp_path, monkeypatch, deep_flag=deep_flag)
        assert runner.state.run_status in {RunStatus.SUCCEEDED, RunStatus.BLOCKED}

        # Three files from two tasks → all on disk.
        for rel in ("index.html", "style.css", "app.js"):
            assert (tmp_path / rel).exists(), f"missing artifact: {rel}"


class TestNoRealLLMCallsWithInvalidKey:
    """Spec §13.12 — invalid OPENAI_API_KEY must not produce a real call."""

    def test_invalid_api_key_does_not_block_mocked_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "invalid")
        monkeypatch.setenv("FLOWFORGE_DEEP_AGENTS", "0")

        # If anything in the pipeline tried to construct a live
        # ChatOpenAI from the env var, a 401 would surface long
        # before the run completed. The MockLLM short-circuits all
        # provider calls, so completion proves no real network I/O.
        llm = _MockLLM(_demo_responses())
        runner = PipelineRunner(llm, output_dir=tmp_path)
        runner.run(_DEMO_PROMPT, skip_github=True)

        assert runner.state.run_status == RunStatus.SUCCEEDED
        # All clarification/spec/plan/task/review/audit calls came
        # from the mock, never the real provider.
        assert llm.call_count >= 7
