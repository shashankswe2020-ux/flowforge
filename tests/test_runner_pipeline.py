"""Tests for the pipeline runner."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from flowforge.runner.pipeline import PipelineRunner, PipelineResult, extract_json
from flowforge.state.models import RunStatus


class MockLLMResponse:
    """Mock LLM response with .content attribute."""

    def __init__(self, content: str) -> None:
        self.content = content


class MockLLM:
    """Mock LLM that returns predefined JSON responses."""

    def __init__(self) -> None:
        self._call_count = 0
        self._responses: list[str] = []

    def set_responses(self, responses: list[dict[str, Any]]) -> None:
        import json
        self._responses = [json.dumps(r) for r in responses]

    def invoke(self, prompt: str) -> MockLLMResponse:
        if self._call_count < len(self._responses):
            resp = self._responses[self._call_count]
        else:
            resp = '{"error": "no more responses"}'
        self._call_count += 1
        return MockLLMResponse(resp)


class TestExtractJson:
    def test_plain_json(self) -> None:
        result = extract_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_markdown_fenced_json(self) -> None:
        text = '```json\n{"key": "value"}\n```'
        result = extract_json(text)
        assert result == {"key": "value"}

    def test_json_with_surrounding_text(self) -> None:
        text = 'Here is the result:\n{"key": "value"}\nDone.'
        result = extract_json(text)
        assert result == {"key": "value"}


class TestPipelineRunner:
    def _make_llm_with_responses(self) -> MockLLM:
        """Create a mock LLM with full pipeline responses."""
        llm = MockLLM()
        llm.set_responses([
            # clarification
            {
                "solution_type": "cli",
                "scope_size": "small",
                "target_users": "developers",
                "must_have": ["core feature"],
                "nice_to_have": [],
                "constraints": ["python"],
                "success_criteria": ["works"],
                "tech_preferences": ["python"],
                "summary": "A simple CLI tool",
            },
            # spec
            {
                "artifact_path": "docs/spec.md",
                "summary": "A CLI tool that does X",
                "acceptance_criteria": ["runs without error"],
                "assumptions": [],
                "open_questions": [],
            },
            # plan
            {
                "phases": ["implementation"],
                "tasks": [
                    {
                        "task_id": "t1",
                        "title": "Create main script",
                        "description": "Write main.py",
                        "acceptance_checks": ["file exists"],
                        "estimated_complexity": "s",
                        "capability_type": "agent_only",
                        "verification_step": "run python main.py",
                    }
                ],
                "edges": [],
            },
            # task execution
            {
                "files": [
                    {"path": "main.py", "content": "print('hello world')"}
                ],
                "verification_evidence": ["created main.py"],
            },
            # code review
            {"findings": []},
            # security audit
            {"findings": []},
            # issue triage (not called since no findings)
        ])
        return llm

    def test_run_skip_github(self, tmp_path: Path) -> None:
        llm = self._make_llm_with_responses()
        runner = PipelineRunner(llm, output_dir=tmp_path)
        result = runner.run("Build a hello world script", skip_github=True)

        assert result.succeeded
        assert "main.py" in result.generated_files
        assert (tmp_path / "main.py").read_text() == "print('hello world')"
        assert result.github_result is None

    def test_run_with_pre_answered(self, tmp_path: Path) -> None:
        llm = MockLLM()
        llm.set_responses([
            # spec (clarification skipped due to pre_answered)
            {
                "artifact_path": "docs/spec.md",
                "summary": "A tool",
                "acceptance_criteria": ["works"],
                "assumptions": [],
                "open_questions": [],
            },
            # plan
            {
                "phases": ["impl"],
                "tasks": [
                    {
                        "task_id": "t1",
                        "title": "Create file",
                        "description": "Write it",
                        "acceptance_checks": ["exists"],
                        "estimated_complexity": "s",
                        "capability_type": "agent_only",
                        "verification_step": "check",
                    }
                ],
                "edges": [],
            },
            # task execution
            {
                "files": [{"path": "app.py", "content": "# app"}],
                "verification_evidence": ["done"],
            },
            # code review
            {"findings": []},
            # security
            {"findings": []},
        ])
        runner = PipelineRunner(llm, output_dir=tmp_path)
        result = runner.run(
            "Build something",
            skip_github=True,
            pre_answered={"summary": "A quick tool", "solution_type": "script"},
        )
        assert result.succeeded

    @patch("flowforge.runner.pipeline.ship_to_github")
    def test_run_with_github(self, mock_ship: MagicMock, tmp_path: Path) -> None:
        from flowforge.shipping.github import GitHubResult

        mock_ship.return_value = GitHubResult(
            repo_url="https://github.com/user/test",
            commit_sha="abc123",
            files_committed=["main.py"],
        )

        llm = self._make_llm_with_responses()
        runner = PipelineRunner(llm, output_dir=tmp_path)
        result = runner.run("Build something", repo_name="test")

        assert result.succeeded
        assert result.repo_url == "https://github.com/user/test"
        assert result.github_result is not None
        assert result.github_result.commit_sha == "abc123"
