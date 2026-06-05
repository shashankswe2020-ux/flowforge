"""Clarification node — conversational scope resolution.

Asks plain-language questions across 6 required dimensions, tracks
ambiguity, and produces a ClarifiedRequest when complete.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Protocol

from src.state.models import (
    AmbiguityStatus,
    ClarificationQA,
    ClarificationTranscript,
    ClarifiedRequest,
    GraphState,
    RunStatus,
)

# The 6 required dimensions per spec
REQUIRED_DIMENSIONS: tuple[str, ...] = (
    "solution_type",
    "scope_size",
    "target_users",
    "delivery_boundaries",
    "constraints",
    "success_criteria",
)

# Ambiguity threshold — above this, clarification is not complete
_AMBIGUITY_THRESHOLD = 0.0


class LLMProtocol(Protocol):
    """Minimal LLM interface for clarification node."""

    def invoke(self, prompt: str) -> Any: ...  # noqa: ANN401


def _build_prompt(state: GraphState, unresolved: list[str]) -> str:
    """Build prompt for the LLM to generate a clarifying question."""
    existing_answers = ""
    if state.clarification_transcript.exchanges:
        existing_answers = "\n".join(
            f"- {e.dimension}: {e.answer}"
            for e in state.clarification_transcript.exchanges
            if e.answer
        )

    return (
        "You are a scope clarifier for a software project. "
        "Ask ONE plain-language clarifying question to the user.\n\n"
        f"Original request: {state.request}\n\n"
        f"Already resolved:\n{existing_answers or '(none)'}\n\n"
        f"Still unresolved dimensions: {', '.join(unresolved)}\n\n"
        "Pick the most important unresolved dimension and ask a clear, "
        "non-technical question. Respond with JSON: "
        '{"question": "...", "dimension": "..."}'
    )


def _build_summary_prompt(state: GraphState) -> str:
    """Build prompt for the LLM to summarize and confirm scope."""
    answers = "\n".join(
        f"- {e.dimension}: {e.answer}" for e in state.clarification_transcript.exchanges if e.answer
    )
    return (
        "Summarize the following project scope in one paragraph. "
        "Respond with JSON: "
        '{"summary": "...", "confirmed": true}\n\n'
        f"Original request: {state.request}\n\n"
        f"Resolved dimensions:\n{answers}"
    )


def _get_resolved_dimensions(state: GraphState) -> set[str]:
    """Determine which dimensions have been answered."""
    return {e.dimension for e in state.clarification_transcript.exchanges if e.answer is not None}


def _extract_answers_by_dimension(state: GraphState) -> dict[str, str]:
    """Extract the latest answer for each dimension."""
    answers: dict[str, str] = {}
    for exchange in state.clarification_transcript.exchanges:
        if exchange.answer is not None:
            answers[exchange.dimension] = exchange.answer
    return answers


def _build_clarified_request(
    answers: dict[str, str],
    summary: str,
) -> ClarifiedRequest:
    """Construct ClarifiedRequest from resolved dimension answers."""
    # Parse delivery_boundaries into must_have/nice_to_have
    boundaries = answers.get("delivery_boundaries", "")
    must_have: list[str] = []
    nice_to_have: list[str] = []
    if "must have:" in boundaries.lower():
        parts = boundaries.split(";")
        for part in parts:
            lower = part.strip().lower()
            if lower.startswith("must have:"):
                must_have = [x.strip() for x in part.split(":")[1].split(",")]
            elif lower.startswith("nice to have:"):
                nice_to_have = [x.strip() for x in part.split(":")[1].split(",")]
    elif boundaries:
        must_have = [boundaries]

    # Parse constraints into list
    constraints_raw = answers.get("constraints", "")
    constraints = [c.strip() for c in constraints_raw.split(",") if c.strip()]

    # Parse success_criteria
    criteria_raw = answers.get("success_criteria", "")
    success_criteria = [c.strip() for c in criteria_raw.split(",") if c.strip()]

    return ClarifiedRequest(
        solution_type=answers.get("solution_type", ""),
        scope_size=answers.get("scope_size", ""),
        target_users=answers.get("target_users", ""),
        must_have=must_have,
        nice_to_have=nice_to_have,
        constraints=constraints,
        success_criteria=success_criteria,
        tech_preferences=[c for c in constraints if c],
        summary=summary,
    )


def _build_auto_clarify_prompt(request: str) -> str:
    """Build prompt to resolve all 6 dimensions in one LLM call (CLI auto mode)."""
    return f"""You are clarifying a software project request. Given the brief request below,
infer reasonable defaults for ALL 6 dimensions. Make pragmatic assumptions a senior engineer
would make for a typical implementation.

## Request
{request}

## Required Dimensions
1. **solution_type**: web app, CLI, library, API, mobile app, etc.
2. **scope_size**: small/medium/large with brief justification
3. **target_users**: who will use this (e.g., "end users", "developers", "internal team")
4. **delivery_boundaries**: format as "Must have: X, Y, Z; Nice to have: A, B"
5. **constraints**: comma-separated tech/operational constraints (e.g., "browser-based, no backend")
6. **success_criteria**: comma-separated measurable outcomes (e.g., "loads under 2s, all operations work, mobile-friendly")

## Response Format
Respond with a single JSON object:
{{
  "solution_type": "...",
  "scope_size": "...",
  "target_users": "...",
  "delivery_boundaries": "Must have: ...; Nice to have: ...",
  "constraints": "...",
  "success_criteria": "...",
  "summary": "One-paragraph summary of the clarified request"
}}
"""


def clarification_node(
    state: GraphState,
    *,
    llm: LLMProtocol,
) -> dict[str, Any]:
    """Run clarification — interactive Q&A or auto-resolve all dimensions.

    - If state.auto_clarify=True: makes ONE LLM call to resolve all dimensions.
    - If dimensions are unresolved: asks a question, returns waiting_for_input.
    - If all dimensions resolved: summarizes, produces ClarifiedRequest, stays RUNNING.
    """
    # Auto-clarify mode (CLI / non-interactive flow)
    if state.auto_clarify and not state.clarified_request:
        prompt = _build_auto_clarify_prompt(state.request)
        response = llm.invoke(prompt)
        content = response.content if hasattr(response, "content") else str(response)

        # Strip markdown fences if present
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content = "\n".join(lines)

        parsed = json.loads(content)
        summary = parsed.get("summary", "")

        answers = {
            d: parsed.get(d, "")
            for d in REQUIRED_DIMENSIONS
        }

        # Build transcript with auto-resolved exchanges
        now = datetime.now(tz=UTC)
        exchanges = [
            ClarificationQA(
                question=f"[auto] {dim}",
                answer=answers[dim],
                dimension=dim,
                timestamp=now,
            )
            for dim in REQUIRED_DIMENSIONS
        ]

        clarified_request = _build_clarified_request(answers, summary)

        return {
            "run_status": RunStatus.RUNNING,
            "clarified_request": clarified_request,
            "clarification_transcript": ClarificationTranscript(exchanges=exchanges),
            "ambiguity_status": AmbiguityStatus(
                score=0.0,
                unresolved_dimensions=[],
                deferred_dimensions=[],
                is_complete=True,
            ),
        }

    resolved = _get_resolved_dimensions(state)
    unresolved = [d for d in REQUIRED_DIMENSIONS if d not in resolved]

    # All dimensions resolved — produce summary and complete
    if not unresolved:
        prompt = _build_summary_prompt(state)
        response = llm.invoke(prompt)
        parsed = json.loads(response.content)
        summary = parsed.get("summary", "")

        answers = _extract_answers_by_dimension(state)
        clarified_request = _build_clarified_request(answers, summary)

        return {
            "run_status": RunStatus.RUNNING,
            "clarified_request": clarified_request,
            "ambiguity_status": AmbiguityStatus(
                score=0.0,
                unresolved_dimensions=[],
                deferred_dimensions=list(state.ambiguity_status.deferred_dimensions),
                is_complete=True,
            ),
            "clarification_transcript": state.clarification_transcript,
        }

    # Still unresolved — ask a question
    prompt = _build_prompt(state, unresolved)
    response = llm.invoke(prompt)
    parsed = json.loads(response.content)

    question = parsed["question"]
    dimension = parsed["dimension"]

    # Append new question to transcript
    new_exchange = ClarificationQA(
        question=question,
        answer=None,
        dimension=dimension,
        timestamp=datetime.now(tz=UTC),
    )
    updated_exchanges = list(state.clarification_transcript.exchanges) + [new_exchange]
    updated_transcript = ClarificationTranscript(exchanges=updated_exchanges)

    # Compute updated ambiguity
    ambiguity_score = len(unresolved) / len(REQUIRED_DIMENSIONS)

    return {
        "run_status": RunStatus.WAITING_FOR_INPUT,
        "clarification_transcript": updated_transcript,
        "ambiguity_status": AmbiguityStatus(
            score=ambiguity_score,
            unresolved_dimensions=unresolved,
            deferred_dimensions=list(state.ambiguity_status.deferred_dimensions),
            is_complete=False,
        ),
    }
