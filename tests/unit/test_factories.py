"""Tests for StateFactory and mock infrastructure."""

from __future__ import annotations

import pytest

from flowforge.state.models import (
    GraphState,
    RunStatus,
    TaskStatus,
)
from tests.factories import make_state
from tests.mocks import (
    MockLLM,
    make_clarification_llm,
    make_plan_llm,
    make_review_llm,
    make_spec_llm,
)
from tests.mocks.external import MockFileSystem, MockGitClient


class TestMakeState:
    """Verify StateFactory produces valid states at every stage."""

    @pytest.mark.parametrize(
        "stage",
        [
            "start",
            "clarification",
            "spec",
            "plan",
            "task_execution",
            "quality_gate",
            "issue_triage",
            "shipping",
            "complete",
        ],
    )
    def test_all_stages_produce_valid_state(self, stage: str) -> None:
        state = make_state(stage=stage)
        assert isinstance(state, GraphState)
        # Round-trip validates schema
        json_str = state.model_dump_json()
        restored = GraphState.model_validate_json(json_str)
        assert restored.run_status == state.run_status

    def test_start_stage(self) -> None:
        state = make_state("start")
        assert state.run_status == RunStatus.PENDING
        assert state.request != ""
        assert state.clarified_request is None
        assert state.spec is None

    def test_clarification_stage(self) -> None:
        state = make_state("clarification")
        assert state.run_status == RunStatus.RUNNING
        assert state.clarified_request is not None
        assert state.ambiguity_status.is_complete is True

    def test_spec_stage(self) -> None:
        state = make_state("spec")
        assert state.spec is not None
        assert len(state.spec.acceptance_criteria) > 0

    def test_plan_stage(self) -> None:
        state = make_state("plan")
        assert state.implementation_plan is not None
        assert len(state.implementation_plan.dag.tasks) > 0

    def test_task_execution_stage(self) -> None:
        state = make_state("task_execution")
        assert len(state.tasks) > 0
        assert all(t.status == TaskStatus.SUCCEEDED for t in state.tasks)

    def test_quality_gate_stage(self) -> None:
        state = make_state("quality_gate")
        assert len(state.review_findings) > 0
        assert len(state.security_findings) > 0
        assert len(state.test_findings) > 0

    def test_issue_triage_stage(self) -> None:
        state = make_state("issue_triage")
        assert len(state.triaged_issues) > 0

    def test_shipping_stage(self) -> None:
        state = make_state("shipping")
        assert state.shipping_readiness.is_ready is True

    def test_complete_stage(self) -> None:
        state = make_state("complete")
        assert state.run_status == RunStatus.SUCCEEDED
        assert state.shipping_result.shipped is True

    def test_overrides_applied(self) -> None:
        state = make_state("start", overrides={"request": "custom request"})
        assert state.request == "custom request"

    def test_overrides_override_stage_defaults(self) -> None:
        state = make_state("complete", overrides={"run_status": RunStatus.FAILED})
        assert state.run_status == RunStatus.FAILED

    def test_invalid_stage_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown stage"):
            make_state("nonexistent_stage")

    def test_all_stages_have_run_metadata(self) -> None:
        for stage in [
            "start",
            "clarification",
            "spec",
            "plan",
            "task_execution",
            "quality_gate",
            "issue_triage",
            "shipping",
            "complete",
        ]:
            state = make_state(stage)
            assert state.run_metadata is not None
            assert state.run_metadata.correlation_id != ""

    def test_all_stages_have_model_config(self) -> None:
        for stage in [
            "start",
            "clarification",
            "spec",
            "plan",
            "task_execution",
            "quality_gate",
            "issue_triage",
            "shipping",
            "complete",
        ]:
            state = make_state(stage)
            assert state.default_model_config is not None


class TestMockLLM:
    """Verify MockLLM provides deterministic responses."""

    def test_single_response(self) -> None:
        llm = MockLLM(responses=["hello"])
        result = llm.invoke("prompt")
        assert result.content == "hello"

    def test_cycles_through_responses(self) -> None:
        llm = MockLLM(responses=["a", "b", "c"])
        assert llm.invoke("1").content == "a"
        assert llm.invoke("2").content == "b"
        assert llm.invoke("3").content == "c"
        assert llm.invoke("4").content == "a"  # wraps

    def test_call_count(self) -> None:
        llm = MockLLM(responses=["x"])
        assert llm.call_count == 0
        llm.invoke("p")
        assert llm.call_count == 1

    def test_call_history(self) -> None:
        llm = MockLLM(responses=["x"])
        llm.invoke("first")
        llm.invoke("second")
        assert llm.call_history == ["first", "second"]

    def test_reset(self) -> None:
        llm = MockLLM(responses=["x"])
        llm.invoke("p")
        llm.reset()
        assert llm.call_count == 0
        assert llm.call_history == []

    def test_response_metadata(self) -> None:
        llm = MockLLM(responses=["x"], model_id="gpt-4o", provider="openai")
        result = llm.invoke("p")
        assert result.model_id == "gpt-4o"
        assert result.provider == "openai"
        assert result.input_tokens == 100
        assert result.stop_reason == "end_turn"


class TestPrebuiltMocks:
    """Verify pre-built mock LLM instances return valid JSON."""

    def test_clarification_llm(self) -> None:
        llm = make_clarification_llm()
        assert llm.call_count == 0
        r1 = llm.invoke("start")
        assert "solution_type" in r1.content

    def test_spec_llm(self) -> None:
        llm = make_spec_llm()
        r = llm.invoke("spec")
        assert "acceptance_criteria" in r.content

    def test_plan_llm(self) -> None:
        llm = make_plan_llm()
        r = llm.invoke("plan")
        assert "task_id" in r.content

    def test_review_llm(self) -> None:
        llm = make_review_llm()
        r = llm.invoke("review")
        assert "findings" in r.content


class TestMockFileSystem:
    """Verify MockFileSystem works as expected."""

    def test_write_and_read(self) -> None:
        fs = MockFileSystem()
        fs.write("src/main.py", "print('hello')")
        assert fs.read("src/main.py") == "print('hello')"

    def test_read_nonexistent(self) -> None:
        fs = MockFileSystem()
        assert fs.read("no-file") is None

    def test_exists(self) -> None:
        fs = MockFileSystem()
        fs.write("a.py", "")
        assert fs.exists("a.py") is True
        assert fs.exists("b.py") is False

    def test_list_dir(self) -> None:
        fs = MockFileSystem()
        fs.write("src/a.py", "")
        fs.write("src/b.py", "")
        fs.write("tests/c.py", "")
        assert sorted(fs.list_dir("src/")) == ["src/a.py", "src/b.py"]

    def test_write_log(self) -> None:
        fs = MockFileSystem()
        fs.write("a.py", "x")
        fs.write("b.py", "y")
        assert len(fs.write_log) == 2


class TestMockGitClient:
    """Verify MockGitClient works as expected."""

    def test_commit(self) -> None:
        git = MockGitClient()
        sha = git.commit("feat: add thing", ["src/a.py"])
        assert sha.startswith("fake-sha-")
        assert len(git.commits) == 1

    def test_diff(self) -> None:
        git = MockGitClient(diff_output="+ new line")
        assert git.diff() == "+ new line"

    def test_branch(self) -> None:
        git = MockGitClient(current_branch="feature/x")
        assert git.get_branch() == "feature/x"
