"""Unit tests for clarification_node implementation."""

from __future__ import annotations

from datetime import UTC, datetime

from src.nodes.clarification import (
    REQUIRED_DIMENSIONS,
    clarification_node,
)
from src.state.models import (
    AmbiguityStatus,
    ClarificationQA,
    ClarificationTranscript,
    ClarifiedRequest,
    GraphState,
    RunStatus,
)
from tests.mocks import MockLLM


def _initial_state(request: str = "Build a REST API for user management") -> GraphState:
    """Minimal state for clarification tests."""
    return GraphState(
        request=request,
        run_status=RunStatus.RUNNING,
    )


def _state_with_answers(
    answers: dict[str, str],
    request: str = "Build a REST API",
) -> GraphState:
    """State with some dimensions already answered in transcript."""
    exchanges = [
        ClarificationQA(
            question=f"What is the {dim}?",
            answer=answer,
            dimension=dim,
            timestamp=datetime(2026, 6, 5, tzinfo=UTC),
        )
        for dim, answer in answers.items()
    ]
    return GraphState(
        request=request,
        run_status=RunStatus.RUNNING,
        clarification_transcript=ClarificationTranscript(exchanges=exchanges),
        ambiguity_status=AmbiguityStatus(
            unresolved_dimensions=[d for d in REQUIRED_DIMENSIONS if d not in answers],
            score=1.0 - (len(answers) / len(REQUIRED_DIMENSIONS)),
        ),
    )


def _fully_answered_state() -> GraphState:
    """State where all dimensions are resolved."""
    return _state_with_answers(
        {
            "solution_type": "web_app",
            "scope_size": "production-ready system",
            "target_users": "internal engineering team",
            "delivery_boundaries": "must have: auth, CRUD; nice to have: analytics",
            "constraints": "Python, PostgreSQL, deploy to AWS",
            "success_criteria": "handles 1000 concurrent users",
        },
    )


class TestRequiredDimensions:
    """Clarification covers all 6 required dimensions."""

    def test_required_dimensions_has_six_entries(self) -> None:
        """Exactly 6 required dimensions per spec."""
        assert len(REQUIRED_DIMENSIONS) == 6

    def test_required_dimensions_match_spec(self) -> None:
        """Dimensions match those in the spec."""
        expected = {
            "solution_type",
            "scope_size",
            "target_users",
            "delivery_boundaries",
            "constraints",
            "success_criteria",
        }
        assert set(REQUIRED_DIMENSIONS) == expected


class TestWaitingForInput:
    """Sets waiting_for_input when dimensions are unresolved."""

    def test_initial_request_produces_waiting_for_input(self) -> None:
        """Fresh request with no answers transitions to waiting_for_input."""
        llm = MockLLM(
            responses=[
                '{"question": "What type of application do you want to build?", '
                '"dimension": "solution_type"}',
            ],
        )
        state = _initial_state()
        result = clarification_node(state, llm=llm)
        assert result["run_status"] == RunStatus.WAITING_FOR_INPUT

    def test_partial_answers_still_waiting(self) -> None:
        """With some dimensions answered but not all, stay in waiting_for_input."""
        llm = MockLLM(
            responses=[
                '{"question": "Who are the target users?", "dimension": "target_users"}',
            ],
        )
        state = _state_with_answers({"solution_type": "web_app", "scope_size": "prototype"})
        result = clarification_node(state, llm=llm)
        assert result["run_status"] == RunStatus.WAITING_FOR_INPUT

    def test_produces_follow_up_question(self) -> None:
        """Result contains a plain-language follow-up question."""
        llm = MockLLM(
            responses=[
                '{"question": "Do you want a browser app, a CLI, or an API?", '
                '"dimension": "solution_type"}',
            ],
        )
        state = _initial_state()
        result = clarification_node(state, llm=llm)
        transcript = result["clarification_transcript"]
        assert len(transcript.exchanges) >= 1
        assert "?" in transcript.exchanges[-1].question


class TestClarificationComplete:
    """Produces clarifiedRequest when all dimensions resolved."""

    def test_all_dimensions_resolved_produces_clarified_request(self) -> None:
        """When all 6 dimensions are answered, produces ClarifiedRequest."""
        llm = MockLLM(
            responses=[
                '{"summary": "A production-ready REST API for user management, '
                "targeting internal engineers, with auth and CRUD, "
                'deployed on AWS with PostgreSQL.", "confirmed": true}',
            ],
        )
        state = _fully_answered_state()
        result = clarification_node(state, llm=llm)
        assert result["clarified_request"] is not None
        assert isinstance(result["clarified_request"], ClarifiedRequest)

    def test_complete_transitions_to_running(self) -> None:
        """Completed clarification keeps run_status as RUNNING."""
        llm = MockLLM(
            responses=[
                '{"summary": "REST API", "confirmed": true}',
            ],
        )
        state = _fully_answered_state()
        result = clarification_node(state, llm=llm)
        assert result["run_status"] == RunStatus.RUNNING

    def test_ambiguity_marked_complete(self) -> None:
        """AmbiguityStatus is_complete=True when all resolved."""
        llm = MockLLM(
            responses=[
                '{"summary": "REST API", "confirmed": true}',
            ],
        )
        state = _fully_answered_state()
        result = clarification_node(state, llm=llm)
        assert result["ambiguity_status"].is_complete is True
        assert result["ambiguity_status"].score == 0.0

    def test_clarified_request_has_correct_fields(self) -> None:
        """ClarifiedRequest populated from answered dimensions."""
        llm = MockLLM(
            responses=[
                '{"summary": "REST API for engineers", "confirmed": true}',
            ],
        )
        state = _fully_answered_state()
        result = clarification_node(state, llm=llm)
        cr = result["clarified_request"]
        assert cr.solution_type == "web_app"
        assert cr.target_users == "internal engineering team"
        assert cr.summary == "REST API for engineers"


class TestTranscriptTracking:
    """Clarification transcript is maintained correctly."""

    def test_new_question_appended_to_transcript(self) -> None:
        """Each invocation appends to clarification_transcript."""
        llm = MockLLM(
            responses=[
                '{"question": "What type of app?", "dimension": "solution_type"}',
            ],
        )
        state = _initial_state()
        result = clarification_node(state, llm=llm)
        transcript = result["clarification_transcript"]
        assert len(transcript.exchanges) == 1
        assert transcript.exchanges[0].dimension == "solution_type"

    def test_existing_transcript_preserved(self) -> None:
        """Previous exchanges are preserved when new questions are asked."""
        llm = MockLLM(
            responses=[
                '{"question": "Who are the users?", "dimension": "target_users"}',
            ],
        )
        state = _state_with_answers({"solution_type": "web_app"})
        result = clarification_node(state, llm=llm)
        transcript = result["clarification_transcript"]
        # Original exchange + new question
        assert len(transcript.exchanges) == 2


class TestAmbiguityStatus:
    """AmbiguityStatus tracking is accurate."""

    def test_initial_unresolved_dimensions(self) -> None:
        """Fresh state has all 6 dimensions unresolved."""
        llm = MockLLM(
            responses=[
                '{"question": "What type?", "dimension": "solution_type"}',
            ],
        )
        state = _initial_state()
        result = clarification_node(state, llm=llm)
        ambiguity = result["ambiguity_status"]
        assert len(ambiguity.unresolved_dimensions) == 6

    def test_score_decreases_as_dimensions_resolve(self) -> None:
        """Ambiguity score is proportional to unresolved dimensions."""
        llm = MockLLM(
            responses=[
                '{"question": "Who are users?", "dimension": "target_users"}',
            ],
        )
        # 2 of 6 resolved
        state = _state_with_answers({"solution_type": "web_app", "scope_size": "prototype"})
        result = clarification_node(state, llm=llm)
        ambiguity = result["ambiguity_status"]
        # Still 4 unresolved out of 6
        assert len(ambiguity.unresolved_dimensions) == 4
        assert ambiguity.score == pytest.approx(4.0 / 6.0, abs=0.01)


class TestLLMInteraction:
    """Node correctly invokes the LLM."""

    def test_llm_receives_request_context(self) -> None:
        """LLM prompt includes the original request."""
        llm = MockLLM(
            responses=[
                '{"question": "What type?", "dimension": "solution_type"}',
            ],
        )
        state = _initial_state("Build a CLI tool for data migration")
        clarification_node(state, llm=llm)
        assert "Build a CLI tool for data migration" in llm.call_history[0]

    def test_llm_called_once_per_invocation(self) -> None:
        """Each node invocation makes exactly one LLM call."""
        llm = MockLLM(
            responses=[
                '{"question": "What type?", "dimension": "solution_type"}',
            ],
        )
        state = _initial_state()
        clarification_node(state, llm=llm)
        assert llm.call_count == 1


# Need pytest import for approx
import pytest  # noqa: E402
