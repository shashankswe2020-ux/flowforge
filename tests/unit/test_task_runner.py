"""Tests for flowforge.nodes.task_runner — real task execution + file writes."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from flowforge.nodes.task_runner import task_node
from flowforge.state.models import (
    CapabilityType,
    GraphState,
    ImplementationPlan,
    TaskDAG,
    TaskDefinition,
    TaskStatus,
)


def _state_with_plan(workdir: Path, *, capability: CapabilityType = CapabilityType.AGENT_ONLY) -> GraphState:
    defn = TaskDefinition(
        task_id="t1",
        title="Add greet",
        description="Implement greet()",
        acceptance_checks=["greet returns 'hi'"],
        estimated_complexity="xs",
        capability_type=capability,
        verification_step="pytest tests/",
    )
    plan = ImplementationPlan(
        phases=["Phase 1"],
        dag=TaskDAG(tasks=[defn], edges=[]),
    )
    return GraphState(request="x", workdir=str(workdir), implementation_plan=plan)


def _llm_response(payload: dict) -> MagicMock:
    fake = MagicMock()
    fake.content = json.dumps(payload)
    response = MagicMock()
    response.content = fake.content
    return response


def test_task_node_writes_artifact_files_to_workdir(tmp_path: Path) -> None:
    state = _state_with_plan(tmp_path)
    llm = MagicMock()
    llm.invoke.return_value = _llm_response({
        "status": "succeeded",
        "artifacts": [
            {
                "artifact_id": "a1",
                "artifact_type": "source",
                "path": "src/greet.py",
                "fingerprint": "x",
                "content": "def greet():\n    return 'hi'\n",
            },
            {
                "artifact_id": "a2",
                "artifact_type": "test",
                "path": "tests/test_greet.py",
                "fingerprint": "y",
                "content": "from src.greet import greet\n\ndef test(): assert greet() == 'hi'\n",
            },
        ],
        "verification_evidence": ["pytest passes"],
    })

    result = task_node(state, llm=llm)

    assert (tmp_path / "src" / "greet.py").read_text().startswith("def greet")
    assert (tmp_path / "tests" / "test_greet.py").exists()
    assert len(result["tasks"]) == 1
    task = result["tasks"][0]
    assert task.status == TaskStatus.SUCCEEDED
    assert len(task.artifacts) == 2
    assert task.artifacts[0].content.startswith("def greet")


def test_task_node_returns_empty_when_plan_missing(tmp_path: Path) -> None:
    state = GraphState(request="x", workdir=str(tmp_path))
    llm = MagicMock()
    assert task_node(state, llm=llm) == {"tasks": []}
    llm.invoke.assert_not_called()


def test_task_node_rejects_path_traversal(tmp_path: Path) -> None:
    state = _state_with_plan(tmp_path)
    llm = MagicMock()
    llm.invoke.return_value = _llm_response({
        "status": "succeeded",
        "artifacts": [
            {
                "artifact_id": "a1",
                "artifact_type": "source",
                "path": "../escape.py",
                "fingerprint": "x",
                "content": "evil",
            },
        ],
        "verification_evidence": [],
    })

    task_node(state, llm=llm)

    assert not (tmp_path.parent / "escape.py").exists()


def test_task_node_skips_artifacts_with_empty_content(tmp_path: Path) -> None:
    state = _state_with_plan(tmp_path)
    llm = MagicMock()
    llm.invoke.return_value = _llm_response({
        "status": "succeeded",
        "artifacts": [
            {"artifact_id": "a1", "artifact_type": "source", "path": "empty.py", "fingerprint": "x", "content": ""},
        ],
        "verification_evidence": [],
    })

    task_node(state, llm=llm)

    assert not (tmp_path / "empty.py").exists()


@pytest.mark.parametrize("capability", [CapabilityType.AGENT_ONLY, CapabilityType.AGENT_WITH_TOOLS])
def test_task_node_handles_agent_capabilities(tmp_path: Path, capability: CapabilityType) -> None:
    state = _state_with_plan(tmp_path, capability=capability)
    llm = MagicMock()
    llm.invoke.return_value = _llm_response({
        "status": "succeeded",
        "artifacts": [
            {"artifact_id": "a1", "artifact_type": "source", "path": "ok.py", "fingerprint": "x", "content": "x = 1\n"},
        ],
        "verification_evidence": [],
    })

    result = task_node(state, llm=llm)

    assert (tmp_path / "ok.py").exists()
    assert result["tasks"][0].status == TaskStatus.SUCCEEDED


def test_task_node_retries_failed_tasks(tmp_path: Path) -> None:
    """Failed task is retried up to MAX_TASK_ATTEMPTS times."""
    from unittest.mock import patch

    state = _state_with_plan(tmp_path)
    llm = MagicMock()

    failed = MagicMock()
    failed.status = TaskStatus.FAILED
    failed.artifacts = []
    failed.verification_evidence = []
    failed.error_message = "transient error"
    failed.idempotency_key = None

    succeeded = MagicMock()
    succeeded.status = TaskStatus.SUCCEEDED
    succeeded.artifacts = []
    succeeded.verification_evidence = []
    succeeded.error_message = None
    succeeded.idempotency_key = None

    # First attempt fails, second succeeds
    with patch(
        "flowforge.nodes.task_runner.execute_task",
        side_effect=[failed, succeeded],
    ) as mock_exec:
        result = task_node(state, llm=llm)

    assert mock_exec.call_count == 2
    assert result["tasks"][0].status == TaskStatus.SUCCEEDED


def test_task_node_gives_up_after_max_attempts(tmp_path: Path) -> None:
    """After MAX_TASK_ATTEMPTS failures, surface the last failed result."""
    from unittest.mock import patch

    from flowforge.nodes.task_runner import MAX_TASK_ATTEMPTS

    state = _state_with_plan(tmp_path)
    llm = MagicMock()

    failed = MagicMock()
    failed.status = TaskStatus.FAILED
    failed.artifacts = []
    failed.verification_evidence = []
    failed.error_message = "still broken"
    failed.idempotency_key = None

    with patch(
        "flowforge.nodes.task_runner.execute_task",
        return_value=failed,
    ) as mock_exec:
        result = task_node(state, llm=llm)

    assert mock_exec.call_count == MAX_TASK_ATTEMPTS
    assert result["tasks"][0].status == TaskStatus.FAILED
    assert result["tasks"][0].error_message == "still broken"


def test_task_node_recovers_from_executor_exception(tmp_path: Path) -> None:
    """Exceptions raised by the executor count as a retry-eligible failure."""
    from unittest.mock import patch

    state = _state_with_plan(tmp_path)
    llm = MagicMock()

    succeeded = MagicMock()
    succeeded.status = TaskStatus.SUCCEEDED
    succeeded.artifacts = []
    succeeded.verification_evidence = []
    succeeded.error_message = None
    succeeded.idempotency_key = None

    with patch(
        "flowforge.nodes.task_runner.execute_task",
        side_effect=[RuntimeError("boom"), succeeded],
    ) as mock_exec:
        result = task_node(state, llm=llm)

    assert mock_exec.call_count == 2
    assert result["tasks"][0].status == TaskStatus.SUCCEEDED
