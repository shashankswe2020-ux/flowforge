"""Tests for T6: ``DeepAgentTrace`` + ``GraphState`` extension.

Covers spec §8.1:

* :class:`DeepAgentTrace` carries the seven trace fields and serialises
  through Pydantic's ``model_dump`` / ``model_validate`` round-trip.
* ``GraphState.deep_agent_traces`` defaults to ``{}`` and preserves
  per-node trace entries across a checkpointer round-trip.
* ``messages_digest`` is a deterministic sha256 over the canonical-JSON
  message list (insensitive to dict key ordering, sensitive to content).
"""

from __future__ import annotations

import hashlib
import json

import pytest

from flowforge.deep_agents import AgentRole
from flowforge.state.models import (
    DeepAgentTrace,
    GraphState,
    Todo,
    ToolInvocationRecord,
)
from tests.factories import make_state


def _trace(**overrides: object) -> DeepAgentTrace:
    base: dict[str, object] = {
        "role": AgentRole.REVIEWER,
        "messages_digest": "0" * 64,
    }
    base.update(overrides)
    return DeepAgentTrace.model_validate(base)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestTodo:
    def test_defaults_to_pending(self) -> None:
        todo = Todo(content="Write tests")
        assert todo.status == "pending"

    def test_rejects_unknown_status(self) -> None:
        with pytest.raises(ValueError, match="status"):
            Todo.model_validate({"content": "x", "status": "wat"})


class TestToolInvocationRecord:
    def test_minimal_fields(self) -> None:
        rec = ToolInvocationRecord(tool="run_tests", ok=True)
        assert rec.duration_ms == 0
        assert rec.parent is None
        assert rec.error is None

    def test_failure_carries_error(self) -> None:
        rec = ToolInvocationRecord(tool="run_lint", ok=False, error="boom")
        assert rec.error == "boom"


class TestDeepAgentTrace:
    def test_required_fields(self) -> None:
        trace = _trace()
        assert trace.role is AgentRole.REVIEWER
        assert trace.todos == []
        assert trace.vfs_keys == []
        assert trace.tool_invocations == []
        assert trace.duration_ms == 0
        assert trace.recursion_depth == 0

    def test_roundtrip_via_model_dump(self) -> None:
        trace = _trace(
            todos=[Todo(content="x", status="completed")],
            vfs_keys=["vfs:/foo.py"],
            duration_ms=12,
            recursion_depth=3,
            tool_invocations=[
                ToolInvocationRecord(tool="run_tests", ok=True, duration_ms=4),
                ToolInvocationRecord(
                    tool="run_lint", ok=False, parent="researcher", error="nope",
                ),
            ],
        )
        restored = DeepAgentTrace.model_validate(trace.model_dump(mode="json"))
        assert restored == trace


# ---------------------------------------------------------------------------
# messages_digest
# ---------------------------------------------------------------------------


class TestDigestMessages:
    def test_deterministic_sha256_of_canonical_json(self) -> None:
        msgs = [{"role": "user", "content": "hi"}]
        digest = DeepAgentTrace.digest_messages(msgs)
        canonical = json.dumps(msgs, sort_keys=True, separators=(",", ":"))
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert digest == expected
        assert len(digest) == 64

    def test_insensitive_to_dict_key_order(self) -> None:
        a = [{"role": "user", "content": "hi"}]
        b = [{"content": "hi", "role": "user"}]
        assert DeepAgentTrace.digest_messages(a) == DeepAgentTrace.digest_messages(b)

    def test_sensitive_to_content(self) -> None:
        a = [{"role": "user", "content": "hi"}]
        b = [{"role": "user", "content": "bye"}]
        assert DeepAgentTrace.digest_messages(a) != DeepAgentTrace.digest_messages(b)

    def test_empty_list_has_stable_digest(self) -> None:
        digest = DeepAgentTrace.digest_messages([])
        assert digest == hashlib.sha256(b"[]").hexdigest()


# ---------------------------------------------------------------------------
# GraphState extension
# ---------------------------------------------------------------------------


class TestGraphStateDeepAgentTraces:
    def test_default_is_empty_dict(self) -> None:
        state = make_state("start")
        assert state.deep_agent_traces == {}

    def test_roundtrip_preserves_traces(self) -> None:
        state = make_state("start")
        state.deep_agent_traces = {
            "code_review_node": _trace(
                todos=[Todo(content="t", status="in_progress")],
                vfs_keys=["vfs:/findings/review.json"],
            ),
        }
        restored = GraphState.model_validate(state.model_dump(mode="json"))
        assert restored.deep_agent_traces == state.deep_agent_traces

    def test_traces_keyed_by_node_name(self) -> None:
        state = GraphState(
            deep_agent_traces={
                "code_review_node": _trace(),
                "security_audit_node": _trace(role=AgentRole.AUDITOR),
            },
        )
        assert set(state.deep_agent_traces) == {
            "code_review_node",
            "security_audit_node",
        }
