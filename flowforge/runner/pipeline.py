"""FlowForge pipeline runner — end-to-end execution with real LLM and GitHub output.

Orchestrates the full pipeline: clarification → spec → plan → task execution
→ quality gates → issue triage → ship to GitHub.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from flowforge.state.models import (
    AmbiguityStatus,
    ClarifiedRequest,
    Finding,
    GraphState,
    ImplementationPlan,
    Issue,
    IssueDisposition,
    IssueSeverity,
    RunStatus,
    ShippingReadiness,
    ShippingResult,
    SpecOutput,
    Task,
    TaskArtifact,
    TaskDAG,
    TaskDefinition,
    TaskDependency,
    TaskStatus,
)
from flowforge.shipping.github import (
    GitHubResult,
    compute_file_fingerprint,
    ship_to_github,
    write_files,
)


class LLMProtocol:
    """Protocol for LLM wrapper."""

    def invoke(self, prompt: str) -> Any: ...  # noqa: ANN401


def extract_json(text: str) -> dict[str, Any]:
    """Extract JSON from LLM response, handling markdown code fences."""
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = cleaned.strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        return json.loads(match.group())  # type: ignore[no-any-return]
    return json.loads(cleaned)  # type: ignore[no-any-return]


def _safe_invoke(llm: Any, prompt: str) -> dict[str, Any]:
    """Invoke LLM and extract JSON safely."""
    response = llm.invoke(prompt)
    content = response.content if hasattr(response, "content") else str(response)
    return extract_json(content)


class PipelineRunner:
    """Runs the complete FlowForge pipeline with real LLM calls."""

    def __init__(self, llm: Any, *, output_dir: Path | None = None) -> None:
        self._llm = llm
        self._output_dir = output_dir or Path(tempfile.mkdtemp(prefix="flowforge-"))
        self._state = GraphState(run_status=RunStatus.PENDING)
        self._generated_files: dict[str, str] = {}

    @property
    def output_dir(self) -> Path:
        return self._output_dir

    @property
    def state(self) -> GraphState:
        return self._state

    @property
    def generated_files(self) -> dict[str, str]:
        return self._generated_files

    def run(
        self,
        prompt: str,
        *,
        repo_name: str | None = None,
        skip_github: bool = False,
        pre_answered: dict[str, str] | None = None,
    ) -> PipelineResult:
        """Execute the full pipeline end-to-end.

        Args:
            prompt: The user's natural language request.
            repo_name: GitHub repo name. If None, asks or creates one.
            skip_github: If True, generate files but don't push to GitHub.
            pre_answered: Pre-answered clarification dimensions (for automation).

        Returns:
            PipelineResult with all outputs and GitHub result.
        """
        self._state = GraphState(request=prompt, run_status=RunStatus.RUNNING)

        # Step 1: Clarification (use pre-answered or defaults)
        print("\n━━━ Step 1: Clarification ━━━")
        clarified = self._clarify(prompt, pre_answered)
        self._state = self._state.model_copy(
            update={
                "clarified_request": clarified,
                "ambiguity_status": AmbiguityStatus(
                    score=0.0, is_complete=True, unresolved_dimensions=[]
                ),
            }
        )
        print(f"  ✓ Scope: {clarified.summary[:100]}...")

        # Step 2: Spec generation
        print("\n━━━ Step 2: Spec Generation ━━━")
        spec = self._generate_spec(clarified)
        self._state = self._state.model_copy(update={"spec": spec})
        print(f"  ✓ Spec: {spec.summary[:100]}...")
        print(f"  ✓ Criteria: {len(spec.acceptance_criteria)} items")

        # Step 3: Plan generation
        print("\n━━━ Step 3: Implementation Plan ━━━")
        plan = self._generate_plan(spec)
        self._state = self._state.model_copy(update={"implementation_plan": plan})
        print(f"  ✓ Tasks: {len(plan.dag.tasks)}")
        for t in plan.dag.tasks:
            print(f"      [{t.task_id}] {t.title}")

        # Step 4: Task execution (generate actual code)
        print("\n━━━ Step 4: Task Execution (Code Generation) ━━━")
        tasks = self._execute_tasks(plan)
        self._state = self._state.model_copy(update={"tasks": tasks})
        print(f"  ✓ Generated {len(self._generated_files)} files")

        # Step 5: Write files to output directory
        print("\n━━━ Step 5: Write Files to Disk ━━━")
        write_files(self._output_dir, self._generated_files)
        print(f"  ✓ Written to: {self._output_dir}")
        for path in self._generated_files:
            print(f"      📄 {path}")

        # Step 6: Code review
        print("\n━━━ Step 6: Code Review ━━━")
        review_findings = self._code_review()
        self._state = self._state.model_copy(update={"review_findings": review_findings})
        print(f"  ✓ Findings: {len(review_findings)}")

        # Step 7: Security audit
        print("\n━━━ Step 7: Security Audit ━━━")
        security_findings = self._security_audit()
        self._state = self._state.model_copy(update={"security_findings": security_findings})
        print(f"  ✓ Findings: {len(security_findings)}")

        # Step 8: Issue triage
        print("\n━━━ Step 8: Issue Triage ━━━")
        issues = self._triage_issues(review_findings + security_findings)
        self._state = self._state.model_copy(update={"triaged_issues": issues})
        blockers = [i for i in issues if i.disposition == IssueDisposition.MUST_FIX_BEFORE_SHIP]
        print(f"  ✓ Issues: {len(issues)} ({len(blockers)} blocking)")

        # Step 9: Ship to GitHub
        github_result: GitHubResult | None = None
        if not skip_github:
            print("\n━━━ Step 9: Ship to GitHub ━━━")
            github_result = ship_to_github(
                self._output_dir,
                repo_name=repo_name,
                create_if_missing=True,
                private=True,
            )
            self._state = self._state.model_copy(
                update={
                    "run_status": RunStatus.SUCCEEDED,
                    "shipping_result": ShippingResult(
                        shipped=True,
                        release_url=github_result.repo_url,
                        commit_sha=github_result.commit_sha,
                    ),
                }
            )
            print(f"  ✓ Repo: {github_result.repo_url}")
            print(f"  ✓ Commit: {github_result.commit_sha}")
        else:
            print("\n━━━ Step 9: Ship (local only) ━━━")
            self._state = self._state.model_copy(
                update={"run_status": RunStatus.SUCCEEDED}
            )
            print(f"  ✓ Files at: {self._output_dir}")

        return PipelineResult(
            state=self._state,
            generated_files=self._generated_files,
            output_dir=self._output_dir,
            github_result=github_result,
        )

    @staticmethod
    def _normalize_complexity(value: str) -> str:
        """Normalize LLM complexity values to valid enum (xs/s/m/l)."""
        mapping = {
            "extra_small": "xs", "extra-small": "xs", "extrasmall": "xs", "xs": "xs",
            "small": "s", "low": "s", "easy": "s", "simple": "s", "s": "s",
            "medium": "m", "moderate": "m", "mid": "m", "m": "m",
            "large": "l", "high": "l", "hard": "l", "complex": "l", "l": "l",
        }
        return mapping.get(value.lower().strip(), "s")

    @staticmethod
    def _normalize_capability(value: str) -> str:
        """Normalize LLM capability type to valid enum."""
        v = value.lower().strip().replace("-", "_").replace(" ", "_")
        if "tool" in v and "agent" in v:
            return "agent_with_tools"
        if "direct" in v or "tool" in v:
            return "direct_tool"
        return "agent_only"

    def _clarify(self, prompt: str, pre_answered: dict[str, str] | None) -> ClarifiedRequest:
        """Resolve clarification dimensions via LLM."""
        if pre_answered:
            return ClarifiedRequest(
                solution_type=pre_answered.get("solution_type", "application"),
                scope_size=pre_answered.get("scope_size", "small"),
                target_users=pre_answered.get("target_users", "developers"),
                must_have=pre_answered.get("must_have", "core functionality").split(","),
                nice_to_have=pre_answered.get("nice_to_have", "").split(","),
                constraints=pre_answered.get("constraints", "").split(","),
                success_criteria=pre_answered.get("success_criteria", "works correctly").split(","),
                tech_preferences=pre_answered.get("tech_preferences", "").split(","),
                summary=pre_answered.get("summary", prompt[:200]),
            )

        clarify_prompt = (
            "You are a project scope clarifier. Given this request, produce a "
            "structured clarification.\n\n"
            f"Request: {prompt}\n\n"
            "Respond with JSON containing:\n"
            '{"solution_type": "cli|web|api|library|script", '
            '"scope_size": "small|medium|large", '
            '"target_users": "who", '
            '"must_have": ["features"], '
            '"nice_to_have": ["features"], '
            '"constraints": ["constraints"], '
            '"success_criteria": ["criteria"], '
            '"tech_preferences": ["tech"], '
            '"summary": "one paragraph summary"}'
        )
        data = _safe_invoke(self._llm, clarify_prompt)
        return ClarifiedRequest(
            solution_type=data.get("solution_type", "application"),
            scope_size=data.get("scope_size", "small"),
            target_users=data.get("target_users", "developers"),
            must_have=data.get("must_have", []),
            nice_to_have=data.get("nice_to_have", []),
            constraints=data.get("constraints", []),
            success_criteria=data.get("success_criteria", []),
            tech_preferences=data.get("tech_preferences", []),
            summary=data.get("summary", prompt[:200]),
        )

    def _generate_spec(self, clarified: ClarifiedRequest) -> SpecOutput:
        """Generate spec from clarified request."""
        prompt = (
            "You are a spec writer. Produce a structured spec from this request.\n\n"
            f"Summary: {clarified.summary}\n"
            f"Solution type: {clarified.solution_type}\n"
            f"Scope: {clarified.scope_size}\n"
            f"Must-have: {', '.join(clarified.must_have)}\n"
            f"Constraints: {', '.join(clarified.constraints)}\n"
            f"Success criteria: {', '.join(clarified.success_criteria)}\n\n"
            "Respond with JSON:\n"
            '{"artifact_path": "docs/specs/spec.md", '
            '"summary": "paragraph", '
            '"acceptance_criteria": ["list"], '
            '"assumptions": ["list"], '
            '"open_questions": ["list or empty"]}'
        )
        data = _safe_invoke(self._llm, prompt)
        return SpecOutput(
            artifact_path=data.get("artifact_path", "docs/specs/spec.md"),
            summary=data["summary"],
            acceptance_criteria=data["acceptance_criteria"],
            assumptions=data.get("assumptions", []),
            open_questions=data.get("open_questions", []),
        )

    def _generate_plan(self, spec: SpecOutput) -> ImplementationPlan:
        """Generate implementation plan from spec."""
        prompt = (
            "You are a project planner. Generate an implementation plan as a task DAG.\n\n"
            f"Spec: {spec.summary}\n"
            f"Criteria: {', '.join(spec.acceptance_criteria)}\n\n"
            "Respond with JSON:\n"
            '{"phases": ["phase names"], '
            '"tasks": [{"task_id": "t1", "title": "...", "description": "detailed instructions for code to write", '
            '"acceptance_checks": ["..."], "estimated_complexity": "s", '
            '"capability_type": "agent_only", "verification_step": "..."}], '
            '"edges": [{"from_task_id": "t1", "to_task_id": "t2"}]}\n\n'
            "IMPORTANT: Each task description must contain SPECIFIC instructions about "
            "what code files to create and what they should contain."
        )
        data = _safe_invoke(self._llm, prompt)

        tasks = [
            TaskDefinition(
                task_id=t["task_id"],
                title=t["title"],
                description=t["description"],
                acceptance_checks=t["acceptance_checks"],
                estimated_complexity=self._normalize_complexity(
                    t.get("estimated_complexity", "s")
                ),
                capability_type=self._normalize_capability(
                    t.get("capability_type", "agent_only")
                ),
                verification_step=t.get("verification_step", "verify output"),
            )
            for t in data["tasks"]
        ]
        edges = [
            TaskDependency(from_task_id=e["from_task_id"], to_task_id=e["to_task_id"])
            for e in data.get("edges", [])
        ]
        dag = TaskDAG(tasks=tasks, edges=edges, plan_revision=1)

        from flowforge.dag.validator import validate_dag

        validate_dag(dag)

        return ImplementationPlan(phases=data.get("phases", ["implementation"]), dag=dag)

    def _execute_tasks(self, plan: ImplementationPlan) -> list[Task]:
        """Execute each task via LLM, producing actual code files."""
        executed_tasks: list[Task] = []

        for task_def in plan.dag.tasks:
            print(f"    → Executing: [{task_def.task_id}] {task_def.title}")

            prompt = (
                "You are a code generator. Implement the following task by producing "
                "actual source code files.\n\n"
                f"Task: {task_def.title}\n"
                f"Description: {task_def.description}\n"
                f"Acceptance checks: {', '.join(task_def.acceptance_checks)}\n\n"
                "Respond with JSON containing:\n"
                '{"files": [{"path": "relative/path/to/file.py", "content": "full file content"}], '
                '"verification_evidence": ["what was done"]}\n\n'
                "IMPORTANT:\n"
                "- Produce COMPLETE, working code (not snippets or placeholders)\n"
                "- Use proper imports, error handling, and documentation\n"
                "- File paths should be relative to the project root\n"
                "- The content field must contain the FULL file content as a string"
            )

            try:
                data = _safe_invoke(self._llm, prompt)
                artifacts: list[TaskArtifact] = []

                for file_info in data.get("files", []):
                    path = file_info["path"]
                    content = file_info["content"]
                    fingerprint = compute_file_fingerprint(content)
                    self._generated_files[path] = content
                    artifacts.append(
                        TaskArtifact(
                            artifact_id=f"art-{uuid.uuid4().hex[:8]}",
                            artifact_type="source_file",
                            path=path,
                            fingerprint=fingerprint,
                        )
                    )

                task = Task(
                    task_id=task_def.task_id,
                    definition=task_def,
                    status=TaskStatus.SUCCEEDED,
                    artifacts=artifacts,
                    verification_evidence=data.get("verification_evidence", []),
                )
                print(f"      ✓ Produced {len(artifacts)} file(s)")

            except Exception as e:
                task = Task(
                    task_id=task_def.task_id,
                    definition=task_def,
                    status=TaskStatus.FAILED,
                    error_message=str(e),
                )
                print(f"      ✗ Failed: {e}")

            executed_tasks.append(task)

        return executed_tasks

    def _code_review(self) -> list[Finding]:
        """Run code review on generated files."""
        file_list = "\n".join(
            f"- {path} ({len(content)} chars)" for path, content in self._generated_files.items()
        )
        # Include first few files' content for actual review
        code_samples = ""
        for path, content in list(self._generated_files.items())[:3]:
            code_samples += f"\n--- {path} ---\n{content[:2000]}\n"

        prompt = (
            "You are a senior code reviewer. Review these files for correctness, "
            "readability, architecture, security, and performance.\n\n"
            f"Files:\n{file_list}\n\n"
            f"Code samples:\n{code_samples}\n\n"
            "Respond with JSON:\n"
            '{"findings": [{"finding_id": "cr-001", "severity": "low|medium|high|critical|info", '
            '"confidence": 0.8, "title": "...", "description": "...", '
            '"file_path": "...", "suggestion": "..."}]}'
        )
        data = _safe_invoke(self._llm, prompt)
        findings: list[Finding] = []
        for f in data.get("findings", []):
            findings.append(
                Finding(
                    finding_id=f["finding_id"],
                    source_node="code_review_node",
                    severity=IssueSeverity(f.get("severity", "info")),
                    confidence=float(f.get("confidence", 0.5)),
                    title=f["title"],
                    description=f["description"],
                    file_path=f.get("file_path"),
                    suggestion=f.get("suggestion"),
                )
            )
        return findings

    def _security_audit(self) -> list[Finding]:
        """Run security audit on generated files."""
        code_samples = ""
        for path, content in list(self._generated_files.items())[:5]:
            code_samples += f"\n--- {path} ---\n{content[:2000]}\n"

        prompt = (
            "You are a security auditor. Review these files for vulnerabilities, "
            "injection risks, exposed secrets, and insecure patterns.\n\n"
            f"Code:\n{code_samples}\n\n"
            "Respond with JSON:\n"
            '{"findings": [{"finding_id": "sec-001", "severity": "low|medium|high|critical|info", '
            '"confidence": 0.8, "title": "...", "description": "...", '
            '"file_path": "...", "suggestion": "..."}]}\n'
            "If no security issues, return {\"findings\": []}"
        )
        data = _safe_invoke(self._llm, prompt)
        findings: list[Finding] = []
        for f in data.get("findings", []):
            findings.append(
                Finding(
                    finding_id=f["finding_id"],
                    source_node="security_audit_node",
                    severity=IssueSeverity(f.get("severity", "info")),
                    confidence=float(f.get("confidence", 0.5)),
                    title=f["title"],
                    description=f["description"],
                    file_path=f.get("file_path"),
                    suggestion=f.get("suggestion"),
                )
            )
        return findings

    def _triage_issues(self, findings: list[Finding]) -> list[Issue]:
        """Triage findings into issues with dispositions."""
        if not findings:
            return []

        finding_lines = "\n".join(
            f"- [{f.severity}] {f.title}: {f.description}" for f in findings
        )
        prompt = (
            "You are an issue triage orchestrator. Classify each finding.\n\n"
            f"Findings:\n{finding_lines}\n\n"
            "Respond with JSON:\n"
            '{"issues": [{"id": "issue-001", "severity": "low|medium|high|critical|info", '
            '"disposition": "must_fix_before_ship|can_follow_up|rejected", '
            '"remediation": "what to do", "owner": null}]}'
        )
        data = _safe_invoke(self._llm, prompt)
        issues: list[Issue] = []
        for i, issue_data in enumerate(data.get("issues", [])):
            source = findings[i].source_node if i < len(findings) else "unknown"
            fingerprint = hashlib.sha256(
                f"{issue_data.get('id', '')}{issue_data.get('remediation', '')}".encode()
            ).hexdigest()[:16]
            issues.append(
                Issue(
                    id=issue_data.get("id", f"issue-{i}"),
                    source_node=source,
                    fingerprint=fingerprint,
                    severity=IssueSeverity(issue_data.get("severity", "info")),
                    confidence=0.8,
                    disposition=IssueDisposition(
                        issue_data.get("disposition", "can_follow_up")
                    ),
                    remediation=issue_data.get("remediation", ""),
                )
            )
        return issues


class PipelineResult:
    """Result of a full pipeline run."""

    def __init__(
        self,
        state: GraphState,
        generated_files: dict[str, str],
        output_dir: Path,
        github_result: GitHubResult | None = None,
    ) -> None:
        self.state = state
        self.generated_files = generated_files
        self.output_dir = output_dir
        self.github_result = github_result

    @property
    def succeeded(self) -> bool:
        return self.state.run_status == RunStatus.SUCCEEDED

    @property
    def repo_url(self) -> str | None:
        if self.github_result:
            return self.github_result.repo_url
        return None

    def summary(self) -> str:
        """Human-readable summary of the pipeline run."""
        lines = [
            f"Status: {self.state.run_status}",
            f"Files generated: {len(self.generated_files)}",
            f"Output dir: {self.output_dir}",
        ]
        if self.github_result:
            lines.append(f"GitHub repo: {self.github_result.repo_url}")
            lines.append(f"Commit: {self.github_result.commit_sha}")
        if self.state.spec:
            lines.append(f"Spec: {self.state.spec.summary[:80]}...")
        return "\n".join(lines)
