"""Unit tests for spec_node implementation."""

from __future__ import annotations

import json

import pytest

from src.nodes.spec import ClarificationIncompleteError, spec_node
from src.state.models import (
    AmbiguityStatus,
    ClarifiedRequest,
    GraphState,
    RunStatus,
    SpecOutput,
)
from tests.mocks import MockLLM


def _clarified_state(
    *,
    ambiguity_score: float = 0.0,
    is_complete: bool = True,
    unresolved: list[str] | None = None,
) -> GraphState:
    """State with completed clarification."""
    return GraphState(
        request="Build a REST API",
        run_status=RunStatus.RUNNING,
        clarified_request=ClarifiedRequest(
            solution_type="web_app",
            scope_size="production-ready system",
            target_users="internal engineering team",
            must_have=["authentication", "CRUD endpoints"],
            nice_to_have=["analytics dashboard"],
            constraints=["Python", "PostgreSQL"],
            success_criteria=["handles 1000 concurrent users"],
            tech_preferences=["FastAPI"],
            summary="A production REST API for user management",
        ),
        ambiguity_status=AmbiguityStatus(
            score=ambiguity_score,
            is_complete=is_complete,
            unresolved_dimensions=unresolved or [],
        ),
    )


def _spec_llm_response(
    *,
    artifact_path: str = "docs/specs/user-management-api.md",
    summary: str = "REST API spec for user management",
    acceptance_criteria: list[str] | None = None,
    assumptions: list[str] | None = None,
    open_questions: list[str] | None = None,
) -> str:
    """Build a JSON response mimicking LLM spec output."""
    return json.dumps(
        {
            "artifact_path": artifact_path,
            "summary": summary,
            "acceptance_criteria": acceptance_criteria
            or [
                "Users can register and log in",
                "CRUD operations on user profiles",
                "Rate limiting at 1000 req/s",
            ],
            "assumptions": assumptions
            or [
                "Python 3.12+ runtime",
                "PostgreSQL 15+ available",
            ],
            "open_questions": open_questions or [],
        },
    )


class TestSpecProduction:
    """spec_node produces structured spec output."""

    def test_produces_spec_output(self) -> None:
        """Returns a valid SpecOutput with all fields."""
        llm = MockLLM(responses=[_spec_llm_response()])
        state = _clarified_state()
        result = spec_node(state, llm=llm)
        assert result["spec"] is not None
        assert isinstance(result["spec"], SpecOutput)

    def test_spec_has_artifact_path(self) -> None:
        """SpecOutput includes artifact_path."""
        llm = MockLLM(
            responses=[
                _spec_llm_response(
                    artifact_path="docs/specs/my-feature.md",
                ),
            ],
        )
        state = _clarified_state()
        result = spec_node(state, llm=llm)
        assert result["spec"].artifact_path == "docs/specs/my-feature.md"

    def test_spec_has_acceptance_criteria(self) -> None:
        """SpecOutput includes acceptance criteria list."""
        criteria = ["Login works", "Logout works", "Sessions expire"]
        llm = MockLLM(
            responses=[
                _spec_llm_response(
                    acceptance_criteria=criteria,
                ),
            ],
        )
        state = _clarified_state()
        result = spec_node(state, llm=llm)
        assert result["spec"].acceptance_criteria == criteria

    def test_spec_has_assumptions(self) -> None:
        """SpecOutput includes assumptions."""
        assumptions = ["Docker available", "Redis for caching"]
        llm = MockLLM(
            responses=[
                _spec_llm_response(
                    assumptions=assumptions,
                ),
            ],
        )
        state = _clarified_state()
        result = spec_node(state, llm=llm)
        assert result["spec"].assumptions == assumptions

    def test_spec_has_summary(self) -> None:
        """SpecOutput includes a summary."""
        llm = MockLLM(
            responses=[
                _spec_llm_response(
                    summary="API for managing users",
                ),
            ],
        )
        state = _clarified_state()
        result = spec_node(state, llm=llm)
        assert result["spec"].summary == "API for managing users"

    def test_run_status_stays_running(self) -> None:
        """Successful spec production keeps RUNNING status."""
        llm = MockLLM(responses=[_spec_llm_response()])
        state = _clarified_state()
        result = spec_node(state, llm=llm)
        assert result["run_status"] == RunStatus.RUNNING


class TestClarificationValidation:
    """spec_node validates clarification is complete before proceeding."""

    def test_blocks_when_ambiguity_above_threshold(self) -> None:
        """Raises when ambiguity score exceeds threshold."""
        llm = MockLLM(responses=[_spec_llm_response()])
        state = _clarified_state(ambiguity_score=0.5, is_complete=False)
        with pytest.raises(ClarificationIncompleteError) as exc_info:
            spec_node(state, llm=llm)
        assert "clarification" in str(exc_info.value).lower()

    def test_blocks_when_clarification_not_complete(self) -> None:
        """Raises when is_complete is False even with low score."""
        llm = MockLLM(responses=[_spec_llm_response()])
        state = _clarified_state(is_complete=False, ambiguity_score=0.0)
        with pytest.raises(ClarificationIncompleteError):
            spec_node(state, llm=llm)

    def test_blocks_when_no_clarified_request(self) -> None:
        """Raises when clarified_request is None."""
        llm = MockLLM(responses=[_spec_llm_response()])
        state = GraphState(
            request="Build something",
            run_status=RunStatus.RUNNING,
        )
        with pytest.raises(ClarificationIncompleteError):
            spec_node(state, llm=llm)

    def test_blocks_with_unresolved_dimensions(self) -> None:
        """Raises when unresolved dimensions remain."""
        llm = MockLLM(responses=[_spec_llm_response()])
        state = _clarified_state(
            ambiguity_score=0.3,
            is_complete=False,
            unresolved=["constraints", "success_criteria"],
        )
        with pytest.raises(ClarificationIncompleteError):
            spec_node(state, llm=llm)


class TestUserFriendlyErrors:
    """Errors are user-friendly, not technical."""

    def test_error_message_is_plain_language(self) -> None:
        """Error message uses plain language."""
        llm = MockLLM(responses=[_spec_llm_response()])
        state = _clarified_state(is_complete=False, ambiguity_score=0.5)
        with pytest.raises(ClarificationIncompleteError) as exc_info:
            spec_node(state, llm=llm)
        msg = str(exc_info.value)
        # Should not contain stack trace terms or technical jargon
        assert "traceback" not in msg.lower()
        assert "exception" not in msg.lower()

    def test_error_includes_unresolved_dimensions(self) -> None:
        """Error message mentions what's still unresolved."""
        llm = MockLLM(responses=[_spec_llm_response()])
        state = _clarified_state(
            is_complete=False,
            ambiguity_score=0.3,
            unresolved=["constraints"],
        )
        with pytest.raises(ClarificationIncompleteError) as exc_info:
            spec_node(state, llm=llm)
        assert "constraints" in str(exc_info.value)


class TestLLMInteraction:
    """spec_node interacts correctly with the LLM."""

    def test_llm_receives_clarified_request_context(self) -> None:
        """LLM prompt includes clarified request details."""
        llm = MockLLM(responses=[_spec_llm_response()])
        state = _clarified_state()
        spec_node(state, llm=llm)
        prompt = llm.call_history[0]
        assert "user management" in prompt.lower() or "REST API" in prompt

    def test_llm_called_once(self) -> None:
        """Exactly one LLM call per spec_node invocation."""
        llm = MockLLM(responses=[_spec_llm_response()])
        state = _clarified_state()
        spec_node(state, llm=llm)
        assert llm.call_count == 1
