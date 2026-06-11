"""Contract tests: legacy single-shot vs. Deep Agent equivalence (T12).

Drives the same ``GraphState`` input through both implementations of each
agentic node and asserts artifact-shape equivalence:

* same top-level state-delta keys (modulo ``deep_agent_traces``),
* same ``Finding`` schema and ``source_node`` attribution,
* same finding-count band,
* deep variant additionally populates ``state.deep_agent_traces[node]``.

T7 ships the 3 read-only Finding-shape cases (review / audit / tester).
T8 adds 4 generative cases covered by ``TestGenerativeContract`` whose
primary output is a structured artifact (ClarifiedRequest, SpecOutput,
ImplementationPlan, list[Issue]) rather than a Finding.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Any

import pytest

from flowforge.nodes import clarification as clar_module
from flowforge.nodes import code_review as cr_module
from flowforge.nodes import issue_orchestrator as iorch_module
from flowforge.nodes import plan as plan_module
from flowforge.nodes import security_audit as sa_module
from flowforge.nodes import spec as spec_module
from flowforge.nodes import task_runner as task_module
from flowforge.nodes import test_engineer as te_module
from flowforge.nodes.clarification import REQUIRED_DIMENSIONS, clarification_node
from flowforge.nodes.code_review import code_review_node
from flowforge.nodes.issue_orchestrator import (
    compute_fingerprint,
    issue_orchestrator_node,
)
from flowforge.nodes.plan import plan_node
from flowforge.nodes.security_audit import security_audit_node
from flowforge.nodes.spec import spec_node
from flowforge.nodes.task_runner import task_node
from flowforge.nodes.test_engineer import test_engineer_node
from flowforge.state.models import (
    AmbiguityStatus,
    CapabilityType,
    ClarificationTranscript,
    ClarifiedRequest,
    DeepAgentTrace,
    Finding,
    GraphState,
    ImplementationPlan,
    Issue,
    IssueSeverity,
    RunStatus,
    SpecOutput,
    Task,
    TaskArtifact,
    TaskDAG,
    TaskDefinition,
    TaskStatus,
)
from tests.mocks import MockLLM

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from types import ModuleType


# ---------------------------------------------------------------------------
# Fixture state
# ---------------------------------------------------------------------------


def _state(workdir: str) -> GraphState:
    """A minimal ``quality_gate``-stage state with one completed task."""
    task_def = TaskDefinition(
        task_id="t1",
        title="Implement auth",
        description="Add auth module",
        acceptance_checks=["login works"],
        estimated_complexity="m",
        capability_type=CapabilityType.AGENT_WITH_TOOLS,
        verification_step="pytest",
    )
    task = Task(
        task_id="t1",
        definition=task_def,
        status=TaskStatus.SUCCEEDED,
        artifacts=[
            TaskArtifact(
                artifact_id="a1",
                artifact_type="code",
                path="src/auth.py",
                fingerprint="sha256:abc",
                content="def login(): ...\n",
            ),
        ],
    )
    return GraphState(
        request="Build API",
        run_status=RunStatus.RUNNING,
        tasks=[task],
        workdir=workdir,
    )


# ---------------------------------------------------------------------------
# Recorded responses — legacy single-shot LLM JSON + deep-agent VFS
# ---------------------------------------------------------------------------


_LEGACY_REVIEW_RESPONSE = json.dumps(
    {
        "verdict": "request_changes",
        "summary": "One missing error path.",
        "done_well": ["clean module layout"],
        "findings": [
            {
                "finding_id": "cr-001",
                "severity": "medium",
                "confidence": 0.85,
                "title": "Missing error handling",
                "description": "login() lacks try/except for network calls",
                "file_path": "src/auth.py",
                "line_range": [1, 1],
                "suggestion": "Wrap network calls in try/except",
            },
        ],
    },
)


_DEEP_REVIEW_RESULT: dict[str, object] = {
    "messages": [
        {"role": "user", "content": "review the workdir"},
        {"role": "assistant", "content": "done"},
    ],
    "files": {
        "vfs:/findings/review.json": json.dumps(
            [
                {
                    "finding_id": "cr-001",
                    "source_node": "code_review_node",
                    "severity": "medium",
                    "confidence": 0.85,
                    "title": "Missing error handling",
                    "description": "login() lacks try/except for network calls",
                    "file_path": "src/auth.py",
                    "line_range": [1, 1],
                    "suggestion": "Wrap network calls in try/except",
                },
            ],
        ),
    },
}


_LEGACY_AUDIT_RESPONSE = json.dumps(
    {
        "summary": "One high-severity issue.",
        "positive_observations": ["uses parameterized queries"],
        "findings": [
            {
                "finding_id": "sec-1",
                "severity": "high",
                "confidence": 0.9,
                "dimension": "data_protection",
                "title": "Plaintext token logged",
                "description": "Token written to stdout",
                "file_path": "src/auth.py",
                "suggestion": "Redact secrets before logging",
            },
        ],
    },
)


_DEEP_AUDIT_RESULT: dict[str, object] = {
    "messages": [{"role": "user", "content": "audit"}],
    "files": {
        "vfs:/findings/security.json": json.dumps(
            [
                {
                    "finding_id": "sec-1",
                    "source_node": "security_audit_node",
                    "severity": "high",
                    "confidence": 0.9,
                    "title": "Plaintext token logged",
                    "description": "Token written to stdout",
                    "file_path": "src/auth.py",
                    "suggestion": "Redact secrets before logging",
                },
            ],
        ),
    },
}


_LEGACY_TEST_RESPONSE = json.dumps(
    {
        "summary": "One coverage gap.",
        "coverage_assessment": {"unit": "partial"},
        "findings": [
            {
                "finding_id": "te-1",
                "severity": "medium",
                "confidence": 0.7,
                "title": "Missing failure-path test",
                "description": "login() error path uncovered",
                "file_path": "tests/test_auth.py",
                "suggestion": "Add test_login_invalid_credentials",
            },
        ],
        "proposed_tasks": [
            {
                "task_id": "test-task-1",
                "title": "Add login failure test",
                "description": "Cover invalid-credentials path",
                "acceptance_checks": ["pytest passes"],
                "estimated_complexity": "s",
                "capability_type": "agent_only",
                "verification_step": "pytest",
            },
        ],
    },
)


_DEEP_TEST_RESULT: dict[str, object] = {
    "messages": [{"role": "user", "content": "review tests"}],
    "files": {
        "vfs:/findings/test.json": json.dumps(
            [
                {
                    "finding_id": "te-1",
                    "source_node": "test_engineer_node",
                    "severity": "medium",
                    "confidence": 0.7,
                    "title": "Missing failure-path test",
                    "description": "login() error path uncovered",
                    "file_path": "tests/test_auth.py",
                    "suggestion": "Add test_login_invalid_credentials",
                },
            ],
        ),
        "vfs:/context/proposed_tasks.json": json.dumps(
            [
                {
                    "task_id": "test-task-1",
                    "title": "Add login failure test",
                    "description": "Cover invalid-credentials path",
                    "acceptance_checks": ["pytest passes"],
                    "estimated_complexity": "s",
                    "capability_type": "agent_only",
                    "verification_step": "pytest",
                },
            ],
        ),
    },
}


# ---------------------------------------------------------------------------
# Contract case parameter
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContractCase:
    """One legacy-vs-deep parity case."""

    node_name: str
    node_module: ModuleType
    node_fn: Callable[..., dict[str, Any]]
    legacy_response: str
    deep_result: Mapping[str, object]
    finding_key: str
    extra_keys: tuple[str, ...] = ()  # legacy keys beyond findings (e.g. proposed_tasks)
    commit_attr: str = ""
    issue_attr: str = "_create_github_issues"


_CASES: tuple[ContractCase, ...] = (
    ContractCase(
        node_name="code_review_node",
        node_module=cr_module,
        node_fn=code_review_node,
        legacy_response=_LEGACY_REVIEW_RESPONSE,
        deep_result=_DEEP_REVIEW_RESULT,
        finding_key="review_findings",
        commit_attr="_commit_review_to_repo",
    ),
    ContractCase(
        node_name="security_audit_node",
        node_module=sa_module,
        node_fn=security_audit_node,
        legacy_response=_LEGACY_AUDIT_RESPONSE,
        deep_result=_DEEP_AUDIT_RESULT,
        finding_key="security_findings",
        commit_attr="_commit_audit_to_repo",
    ),
    ContractCase(
        node_name="test_engineer_node",
        node_module=te_module,
        node_fn=test_engineer_node,
        legacy_response=_LEGACY_TEST_RESPONSE,
        deep_result=_DEEP_TEST_RESULT,
        finding_key="test_findings",
        extra_keys=("proposed_tasks",),
        commit_attr="_commit_report_to_repo",
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stub_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    case: ContractCase,
) -> None:
    """No-op the git/gh side effects so the tests are hermetic."""
    monkeypatch.setattr(case.node_module, case.commit_attr, lambda *a, **k: None)
    monkeypatch.setattr(case.node_module, case.issue_attr, lambda *a, **k: None)


def _patch_deep(
    monkeypatch: pytest.MonkeyPatch,
    case: ContractCase,
) -> None:
    """Patch ``build_deep_agent`` + ``run_deep_agent_bounded`` to the canned result."""

    def _fake_build(*_a: object, **_k: object) -> object:
        return object()

    def _fake_run(*_a: object, **_k: object) -> Mapping[str, object]:
        return case.deep_result

    monkeypatch.setattr(case.node_module, "build_deep_agent", _fake_build)
    monkeypatch.setattr(case.node_module, "run_deep_agent_bounded", _fake_run)


def _diff_dict_shape(
    legacy: dict[str, Any],
    deep: dict[str, Any],
    *,
    drop: set[str],
) -> str:
    """Render a side-by-side diff of keys for failure messages."""
    legacy_keys = sorted(legacy.keys())
    deep_keys = sorted(set(deep.keys()) - drop)
    return f"\n  legacy keys: {legacy_keys}\n  deep keys (− {sorted(drop)}): {deep_keys}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture(params=_CASES, ids=lambda c: c.node_name)
def case(request: pytest.FixtureRequest) -> ContractCase:
    return request.param  # type: ignore[no-any-return]


class TestLegacyVsDeepContract:
    """Spec §11.3: artifact-shape equivalence between legacy and deep paths."""

    def test_top_level_keys_match(
        self,
        case: ContractCase,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Legacy
        monkeypatch.delenv("FLOWFORGE_DEEP_AGENTS", raising=False)
        _stub_side_effects(monkeypatch, case)
        legacy_state = _state(str(tmp_path / "legacy"))
        legacy_llm = MockLLM(responses=[case.legacy_response])
        legacy_result = case.node_fn(legacy_state, llm=legacy_llm)

        # Deep
        monkeypatch.setenv("FLOWFORGE_DEEP_AGENTS", "1")
        _patch_deep(monkeypatch, case)
        deep_state = _state(str(tmp_path / "deep"))
        deep_result = case.node_fn(deep_state, llm=MockLLM(responses=["unused"]))

        legacy_keys = set(legacy_result.keys())
        deep_keys = set(deep_result.keys()) - {"deep_agent_traces"}
        assert legacy_keys == deep_keys, (
            f"top-level state delta keys diverge for {case.node_name}:"
            + _diff_dict_shape(legacy_result, deep_result, drop={"deep_agent_traces"})
        )
        # Every declared finding key must be present.
        assert case.finding_key in legacy_keys
        for k in case.extra_keys:
            assert k in legacy_keys

    def test_finding_count_band_matches(
        self,
        case: ContractCase,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("FLOWFORGE_DEEP_AGENTS", raising=False)
        _stub_side_effects(monkeypatch, case)
        legacy_result = case.node_fn(
            _state(str(tmp_path / "legacy")),
            llm=MockLLM(responses=[case.legacy_response]),
        )

        monkeypatch.setenv("FLOWFORGE_DEEP_AGENTS", "1")
        _patch_deep(monkeypatch, case)
        deep_result = case.node_fn(
            _state(str(tmp_path / "deep")),
            llm=MockLLM(responses=["unused"]),
        )

        assert len(legacy_result[case.finding_key]) == len(deep_result[case.finding_key])

    def test_finding_schema_and_source_node_match(
        self,
        case: ContractCase,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("FLOWFORGE_DEEP_AGENTS", raising=False)
        _stub_side_effects(monkeypatch, case)
        legacy_result = case.node_fn(
            _state(str(tmp_path / "legacy")),
            llm=MockLLM(responses=[case.legacy_response]),
        )

        monkeypatch.setenv("FLOWFORGE_DEEP_AGENTS", "1")
        _patch_deep(monkeypatch, case)
        deep_result = case.node_fn(
            _state(str(tmp_path / "deep")),
            llm=MockLLM(responses=["unused"]),
        )

        for f in legacy_result[case.finding_key]:
            assert isinstance(f, Finding)
            assert f.source_node == case.node_name
        for f in deep_result[case.finding_key]:
            assert isinstance(f, Finding)
            assert f.source_node == case.node_name

    def test_deep_path_populates_trace(
        self,
        case: ContractCase,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FLOWFORGE_DEEP_AGENTS", "1")
        _stub_side_effects(monkeypatch, case)
        _patch_deep(monkeypatch, case)
        deep_result = case.node_fn(
            _state(str(tmp_path)),
            llm=MockLLM(responses=["unused"]),
        )

        traces = deep_result["deep_agent_traces"]
        assert case.node_name in traces
        assert isinstance(traces[case.node_name], DeepAgentTrace)

    def test_legacy_path_does_not_populate_trace(
        self,
        case: ContractCase,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("FLOWFORGE_DEEP_AGENTS", raising=False)
        _stub_side_effects(monkeypatch, case)
        legacy_result = case.node_fn(
            _state(str(tmp_path)),
            llm=MockLLM(responses=[case.legacy_response]),
        )
        assert "deep_agent_traces" not in legacy_result


class TestReviewContract:
    """Named entry point — referenced by T7's verification list.

    Acceptance: ``pytest tests/contract/test_legacy_vs_deep.py::test_review_contract``.
    """

    def test_review_contract(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        review_case = next(c for c in _CASES if c.node_name == "code_review_node")

        monkeypatch.delenv("FLOWFORGE_DEEP_AGENTS", raising=False)
        _stub_side_effects(monkeypatch, review_case)
        legacy_result = review_case.node_fn(
            _state(str(tmp_path / "legacy")),
            llm=MockLLM(responses=[review_case.legacy_response]),
        )

        monkeypatch.setenv("FLOWFORGE_DEEP_AGENTS", "1")
        _patch_deep(monkeypatch, review_case)
        deep_result = review_case.node_fn(
            _state(str(tmp_path / "deep")),
            llm=MockLLM(responses=["unused"]),
        )

        assert "review_findings" in legacy_result
        assert "review_findings" in deep_result
        assert len(legacy_result["review_findings"]) == len(deep_result["review_findings"])
        assert "deep_agent_traces" in deep_result
        assert "code_review_node" in deep_result["deep_agent_traces"]


# ---------------------------------------------------------------------------
# Generative cases (T8): structured-artifact contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GenerativeCase:
    """One legacy-vs-deep parity case for a generative node."""

    node_name: str
    node_module: ModuleType
    node_fn: Callable[..., dict[str, Any]]
    state_factory: Callable[[str], GraphState]
    legacy_response_factory: Callable[[GraphState], str]
    deep_result_factory: Callable[[GraphState], Mapping[str, object]]
    commit_attrs: tuple[str, ...]
    assert_keys: tuple[str, ...]
    primary_key: str


def _clarify_state(workdir: str) -> GraphState:
    return GraphState(
        request="Build a TypeScript MCP server for the WHOOP API.",
        run_status=RunStatus.RUNNING,
        auto_clarify=True,
        ambiguity_status=AmbiguityStatus(
            score=1.0,
            unresolved_dimensions=list(REQUIRED_DIMENSIONS),
            deferred_dimensions=[],
            is_complete=False,
        ),
        clarification_transcript=ClarificationTranscript(exchanges=[]),
        workdir=workdir,
    )


def _clarify_payload() -> dict[str, str]:
    return {
        "solution_type": "library",
        "scope_size": "medium",
        "target_users": "AI developers",
        "delivery_boundaries": "Out: hosting",
        "constraints": "Node 18+",
        "success_criteria": "Tools accessible",
        "summary": "MCP server for the WHOOP API.",
    }


def _spec_state(workdir: str) -> GraphState:
    clarified = ClarifiedRequest(
        solution_type="library",
        scope_size="medium",
        target_users="AI developers",
        constraints=["Node 18+"],
        success_criteria=["Tools accessible"],
        summary="MCP server for the WHOOP API.",
    )
    return GraphState(
        request="Build an MCP server.",
        run_status=RunStatus.RUNNING,
        clarified_request=clarified,
        ambiguity_status=AmbiguityStatus(
            score=0.0,
            unresolved_dimensions=[],
            deferred_dimensions=[],
            is_complete=True,
        ),
        workdir=workdir,
    )


def _spec_payload() -> dict[str, Any]:
    return {
        "artifact_path": "docs/spec/whoop-mcp.md",
        "summary": "MCP server.",
        "objective": "Provide tools.",
        "target_users": "AI developers",
        "tech_stack": ["TypeScript"],
        "commands": {"build": "npm run build"},
        "project_structure": ["src/"],
        "code_style": ["No any"],
        "acceptance_criteria": ["All tools work"],
        "assumptions": [],
        "open_questions": [],
        "security_considerations": [],
        "testing_strategy": [],
        "boundaries": {},
    }


def _plan_state(workdir: str) -> GraphState:
    spec = SpecOutput(
        artifact_path="docs/spec/whoop-mcp.md",
        summary="MCP server.",
        objective="Tools.",
        target_users="AI developers",
        acceptance_criteria=["All tools work"],
    )
    return GraphState(
        request="Build an MCP server.",
        run_status=RunStatus.RUNNING,
        spec=spec,
        workdir=workdir,
    )


def _plan_payload() -> dict[str, Any]:
    return {
        "phases": ["scaffold"],
        "tasks": [
            {
                "task_id": "t1",
                "title": "Scaffold project",
                "description": "Initialize TypeScript project.",
                "acceptance_checks": ["package.json exists"],
                "estimated_complexity": "s",
                "capability_type": "agent_only",
                "verification_step": "test -f package.json",
            },
        ],
        "edges": [],
        "plan_revision": 1,
    }


def _triager_finding() -> Finding:
    return Finding(
        finding_id="rev-1",
        source_node="code_review_node",
        severity=IssueSeverity.HIGH,
        confidence=0.9,
        title="Missing input validation",
        description="Login endpoint accepts unsanitised input.",
        file_path="src/login.py",
        line_range=(10, 25),
    )


def _triager_state(workdir: str) -> GraphState:
    return GraphState(
        request="Build login service.",
        run_status=RunStatus.RUNNING,
        review_findings=[_triager_finding()],
        workdir=workdir,
    )


def _triager_legacy_response(state: GraphState) -> str:
    fp = compute_fingerprint(state.review_findings[0])
    return json.dumps(
        {
            "issues": [
                {
                    "fingerprint": fp,
                    "disposition": "must_fix_before_ship",
                    "remediation": "Add validation middleware.",
                    "owner": "code_review_node",
                    "sla_target": "24h",
                },
            ],
        },
    )


def _triager_deep_result(state: GraphState) -> Mapping[str, object]:
    return {
        "messages": [{"role": "user", "content": "triage"}],
        "files": {
            "vfs:/context/issues_output.json": _triager_legacy_response(state),
        },
    }


def _implementer_state(workdir: str) -> GraphState:
    Path(workdir).mkdir(parents=True, exist_ok=True)
    defn = TaskDefinition(
        task_id="t1",
        title="Add greet()",
        description="Implement greet() returning 'hi'.",
        acceptance_checks=["greet returns 'hi'"],
        estimated_complexity="xs",
        capability_type=CapabilityType.AGENT_ONLY,
        verification_step="pytest tests/test_greet.py -q",
    )
    plan = ImplementationPlan(
        phases=["Phase 1"],
        dag=TaskDAG(tasks=[defn], edges=[]),
    )
    return GraphState(
        request="Build greet().",
        run_status=RunStatus.RUNNING,
        workdir=workdir,
        implementation_plan=plan,
    )


def _implementer_legacy_response() -> str:
    return json.dumps(
        {
            "status": "succeeded",
            "artifacts": [
                {
                    "artifact_id": "a1",
                    "artifact_type": "source",
                    "path": "src/greet.py",
                    "fingerprint": "x",
                    "content": "def greet():\n    return 'hi'\n",
                },
            ],
            "verification_evidence": ["pytest tests/test_greet.py -q -> 1 passed"],
        },
    )


def _implementer_deep_result(_state: GraphState) -> Mapping[str, object]:
    return {
        "messages": [{"role": "assistant", "content": "implementer done"}],
        "files": {
            "vfs:/src/greet.py": "def greet():\n    return 'hi'\n",
        },
    }


_GENERATIVE_CASES: tuple[GenerativeCase, ...] = (
    GenerativeCase(
        node_name="clarification_node",
        node_module=clar_module,
        node_fn=clarification_node,
        state_factory=_clarify_state,
        legacy_response_factory=lambda _s: json.dumps(_clarify_payload()),
        deep_result_factory=lambda _s: {
            "messages": [{"role": "user", "content": "clarify"}],
            "files": {
                "vfs:/context/clarified_request_output.json": json.dumps(_clarify_payload()),
            },
        },
        commit_attrs=(),
        assert_keys=(
            "clarified_request",
            "clarification_transcript",
            "ambiguity_status",
            "run_status",
        ),
        primary_key="clarified_request",
    ),
    GenerativeCase(
        node_name="spec_node",
        node_module=spec_module,
        node_fn=spec_node,
        state_factory=_spec_state,
        legacy_response_factory=lambda _s: json.dumps(_spec_payload()),
        deep_result_factory=lambda _s: {
            "messages": [{"role": "user", "content": "spec"}],
            "files": {
                "vfs:/context/spec_output.json": json.dumps(_spec_payload()),
            },
        },
        commit_attrs=("_commit_spec_to_repo",),
        assert_keys=("spec", "run_status"),
        primary_key="spec",
    ),
    GenerativeCase(
        node_name="plan_node",
        node_module=plan_module,
        node_fn=plan_node,
        state_factory=_plan_state,
        legacy_response_factory=lambda _s: json.dumps(_plan_payload()),
        deep_result_factory=lambda _s: {
            "messages": [{"role": "user", "content": "plan"}],
            "files": {
                "vfs:/context/plan_output.json": json.dumps(_plan_payload()),
            },
        },
        commit_attrs=("_commit_plan_to_repo",),
        assert_keys=("implementation_plan", "run_status"),
        primary_key="implementation_plan",
    ),
    GenerativeCase(
        node_name="issue_orchestrator_node",
        node_module=iorch_module,
        node_fn=issue_orchestrator_node,
        state_factory=_triager_state,
        legacy_response_factory=_triager_legacy_response,
        deep_result_factory=_triager_deep_result,
        commit_attrs=("_commit_triage_to_repo", "_create_github_issues"),
        assert_keys=("triaged_issues", "tool_operations"),
        primary_key="triaged_issues",
    ),
    GenerativeCase(
        node_name="task_node",
        node_module=task_module,
        node_fn=task_node,
        state_factory=_implementer_state,
        legacy_response_factory=lambda _s: _implementer_legacy_response(),
        deep_result_factory=_implementer_deep_result,
        commit_attrs=("_commit_artifacts",),
        assert_keys=("tasks",),
        primary_key="tasks",
    ),
)


def _stub_generative(monkeypatch: pytest.MonkeyPatch, gcase: GenerativeCase) -> None:
    for attr in gcase.commit_attrs:
        monkeypatch.setattr(gcase.node_module, attr, lambda *a, **k: None)


def _patch_deep_generative(
    monkeypatch: pytest.MonkeyPatch,
    gcase: GenerativeCase,
    state: GraphState,
) -> None:
    canned = gcase.deep_result_factory(state)

    def _fake_build(*_a: object, **_k: object) -> object:
        return object()

    def _fake_run(*_a: object, **_k: object) -> Mapping[str, object]:
        return canned

    monkeypatch.setattr(gcase.node_module, "build_deep_agent", _fake_build)
    monkeypatch.setattr(gcase.node_module, "run_deep_agent_bounded", _fake_run)


@pytest.fixture(params=_GENERATIVE_CASES, ids=lambda c: c.node_name)
def gcase(request: pytest.FixtureRequest) -> GenerativeCase:
    return request.param  # type: ignore[no-any-return]


class TestGenerativeContract:
    """Spec §11.3: generative-node legacy-vs-deep parity for T8."""

    def test_top_level_keys_match(
        self,
        gcase: GenerativeCase,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("FLOWFORGE_DEEP_AGENTS", raising=False)
        _stub_generative(monkeypatch, gcase)
        legacy_state = gcase.state_factory(str(tmp_path / "legacy"))
        legacy_result = gcase.node_fn(
            legacy_state,
            llm=MockLLM(responses=[gcase.legacy_response_factory(legacy_state)]),
        )

        monkeypatch.setenv("FLOWFORGE_DEEP_AGENTS", "1")
        _stub_generative(monkeypatch, gcase)
        deep_state = gcase.state_factory(str(tmp_path / "deep"))
        _patch_deep_generative(monkeypatch, gcase, deep_state)
        deep_result = gcase.node_fn(
            deep_state,
            llm=MockLLM(responses=["unused"]),
        )

        legacy_keys = set(legacy_result.keys())
        deep_keys = set(deep_result.keys()) - {"deep_agent_traces"}
        for k in gcase.assert_keys:
            assert k in legacy_keys, f"{gcase.node_name} legacy missing {k}"
            assert k in deep_keys, f"{gcase.node_name} deep missing {k}"

    def test_primary_artifact_has_matching_type(
        self,
        gcase: GenerativeCase,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("FLOWFORGE_DEEP_AGENTS", raising=False)
        _stub_generative(monkeypatch, gcase)
        legacy_state = gcase.state_factory(str(tmp_path / "legacy"))
        legacy_result = gcase.node_fn(
            legacy_state,
            llm=MockLLM(responses=[gcase.legacy_response_factory(legacy_state)]),
        )

        monkeypatch.setenv("FLOWFORGE_DEEP_AGENTS", "1")
        _stub_generative(monkeypatch, gcase)
        deep_state = gcase.state_factory(str(tmp_path / "deep"))
        _patch_deep_generative(monkeypatch, gcase, deep_state)
        deep_result = gcase.node_fn(
            deep_state,
            llm=MockLLM(responses=["unused"]),
        )

        legacy_artifact = legacy_result[gcase.primary_key]
        deep_artifact = deep_result[gcase.primary_key]
        assert type(legacy_artifact) is type(deep_artifact)
        # Lists must have parity in length.
        if isinstance(legacy_artifact, list):
            assert len(legacy_artifact) == len(deep_artifact)
            for legacy_item, deep_item in zip(legacy_artifact, deep_artifact, strict=True):
                assert type(legacy_item) is type(deep_item)
                if isinstance(legacy_item, Issue):
                    assert isinstance(deep_item, Issue)
                    assert legacy_item.fingerprint == deep_item.fingerprint
                    assert legacy_item.disposition == deep_item.disposition

    def test_deep_path_populates_trace(
        self,
        gcase: GenerativeCase,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FLOWFORGE_DEEP_AGENTS", "1")
        _stub_generative(monkeypatch, gcase)
        state = gcase.state_factory(str(tmp_path))
        _patch_deep_generative(monkeypatch, gcase, state)
        deep_result = gcase.node_fn(state, llm=MockLLM(responses=["unused"]))

        traces = deep_result["deep_agent_traces"]
        assert gcase.node_name in traces
        assert isinstance(traces[gcase.node_name], DeepAgentTrace)

    def test_legacy_path_does_not_populate_trace(
        self,
        gcase: GenerativeCase,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("FLOWFORGE_DEEP_AGENTS", raising=False)
        _stub_generative(monkeypatch, gcase)
        state = gcase.state_factory(str(tmp_path))
        legacy_result = gcase.node_fn(
            state,
            llm=MockLLM(responses=[gcase.legacy_response_factory(state)]),
        )
        assert "deep_agent_traces" not in legacy_result
