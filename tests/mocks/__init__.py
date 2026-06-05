"""Mock LLM module providing deterministic responses for testing."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MockLLMResponse:
    """A single mock LLM response."""

    content: str
    model_id: str = "test-model"
    provider: str = "test-provider"
    input_tokens: int = 100
    output_tokens: int = 50
    stop_reason: str = "end_turn"


@dataclass
class MockLLM:
    """Deterministic mock LLM that returns pre-configured responses.

    Usage:
        llm = MockLLM(responses=["response 1", "response 2"])
        result = llm.invoke("prompt")  # returns MockLLMResponse with "response 1"
        result = llm.invoke("prompt")  # returns MockLLMResponse with "response 2"
    """

    responses: list[str] = field(default_factory=lambda: ["mock response"])
    model_id: str = "test-model"
    provider: str = "test-provider"
    _call_count: int = field(default=0, init=False)
    _call_history: list[str] = field(default_factory=list, init=False)

    def invoke(self, prompt: str) -> MockLLMResponse:
        """Return next pre-configured response."""
        self._call_history.append(prompt)
        idx = self._call_count % len(self.responses)
        self._call_count += 1
        return MockLLMResponse(
            content=self.responses[idx],
            model_id=self.model_id,
            provider=self.provider,
        )

    @property
    def call_count(self) -> int:
        """Number of times invoke was called."""
        return self._call_count

    @property
    def call_history(self) -> list[str]:
        """All prompts passed to invoke."""
        return list(self._call_history)

    def reset(self) -> None:
        """Reset call count and history."""
        self._call_count = 0
        self._call_history.clear()


# Pre-built mock instances for common node types


def make_clarification_llm() -> MockLLM:
    """Mock LLM for clarification_node testing."""
    return MockLLM(
        responses=[
            '{"question": "What type of application?", "dimension": "solution_type"}',
            '{"question": "Who are the target users?", "dimension": "target_users"}',
            '{"summary": "Web app for internal engineers", "confirmed": true}',
        ],
    )


def make_spec_llm() -> MockLLM:
    """Mock LLM for spec_node testing."""
    return MockLLM(
        responses=[
            '{"artifact_path": "docs/specs/feature.md", "summary": "Feature spec", '
            '"acceptance_criteria": ["works correctly", "handles errors"], '
            '"assumptions": ["Python 3.12+"]}',
        ],
    )


def make_plan_llm() -> MockLLM:
    """Mock LLM for plan_node testing."""
    return MockLLM(
        responses=[
            '{"phases": ["foundation", "core"], "tasks": ['
            '{"task_id": "t1", "title": "Scaffold", "description": "Setup project", '
            '"acceptance_checks": ["files exist"], "estimated_complexity": "s", '
            '"capability_type": "direct_tool", "verification_step": "build passes"},'
            '{"task_id": "t2", "title": "Implement", "description": "Write code", '
            '"acceptance_checks": ["tests pass"], "estimated_complexity": "m", '
            '"capability_type": "agent_with_tools", "verification_step": "pytest passes"}'
            '], "edges": [{"from_task_id": "t1", "to_task_id": "t2"}]}',
        ],
    )


def make_review_llm() -> MockLLM:
    """Mock LLM for code_review_node testing."""
    return MockLLM(
        responses=[
            '{"findings": [{"finding_id": "f1", "severity": "medium", '
            '"confidence": 0.8, "title": "Unused import", '
            '"description": "Module imported but unused", '
            '"file_path": "src/foo.py", "suggestion": "Remove import"}]}',
        ],
    )
