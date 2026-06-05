# Spec: FlowForge LangGraph Agentic Development Pipeline

## Objective
Build a LangGraph-driven automation pipeline for agentic software development that:
- Accepts a product/change request as input.
- Runs a conversational clarification loop to resolve scope ambiguity before specification.
- Produces a specification document.
- Produces an implementation plan with a task DAG.
- Dynamically fans out implementation task execution by DAG dependencies.
- Runs quality gates (code review, security audit, test engineering).
- Triages discovered issues in batch.
- Performs shipping-readiness checks and ships when ready.
- Is fully visualizable and runnable from LangGraph Studio.
- Integrates with GitHub Copilot, Codex, and Claude Code through MCP-compatible interfaces.

Primary users:
- AI-assisted development operators orchestrating agent workflows.
- Engineering teams that want traceable, repeatable software delivery automation.

Success definition:
- A single graph execution can move from request -> shipped outcome (or blocked with explicit reasons), with full node-level traceability in LangGraph Studio.

## Assumptions
1. Runtime is Python 3.12+ with LangGraph (Python).
2. Agent nodes are implemented with model providers that support tool calling.
3. The graph state is persisted with a LangGraph-compatible checkpointer/store.
4. Shipping may target GitHub-based repositories (PR merge/release), but shipping adapters are pluggable.
5. Production shipping always requires explicit human approval.
6. Assistant integrations (GitHub Copilot, Codex, Claude Code) are implemented as pluggable adapters with a common contract.
7. Default persistence backend is PostgreSQL for shared environments and SQLite for local development.
8. Model routing supports a run-level default model and node-level model overrides.

## Non-Goals
- Building model-provider-specific prompts for every coding language in this spec.
- Defining organization-specific release policy details beyond required interfaces.
- Replacing CI systems; this graph orchestrates and consumes CI outcomes.

## Tech Stack
- Python 3.12+ with strict typing (no Any in production graph code)
- LangGraph (Python)
- LangChain core abstractions for model/tool interfaces where needed
- Pydantic for graph state and node IO schema validation
- Pytest for tests
- Ruff for linting
- mypy for static type checking

## Commands
- Install: pip install -r requirements.txt
- Build check: python -m compileall src
- Typecheck: mypy src
- Test: pytest
- Lint: ruff check .
- Dev graph run: python -m src.graph.dev
- LangGraph Studio: langgraph dev

## Assistant Integration Requirements
Supported assistants:
- GitHub Copilot
- Codex
- Claude Code

Integration model:
- The graph exposes MCP-compatible tool and workflow surfaces consumable by all supported assistants.
- Each assistant uses a dedicated adapter that maps assistant-specific request/response formats into a canonical internal schema.
- All adapters must produce identical canonical graph input/output structures for equivalent requests.

Assistant adapter contract (required):
- adapter_id
- auth_mode
- capability_profile (agent-only, agent-with-tools, direct-tools)
- request_normalizer
- response_normalizer
- error_mapper
- rate_limit_policy
- telemetry_tags

Required behavior:
- Assistant selection is configurable at runtime per graph run.
- Adapter failures must be isolated and surfaced as typed integration errors.
- Graph execution semantics must remain unchanged across assistant providers.
- All assistant-side tool operations route through MCP tools and follow the same allowlist/security policy.

Canonical normalization contract:
- Adapters must normalize assistant input into canonical_request with fields:
  - request_id
  - assistant_provider (copilot, codex, claude_code)
  - user_prompt
  - repository_context
  - constraints
  - execution_policy
  - metadata
- Adapters must normalize graph output into canonical_response with fields:
  - request_id
  - run_id
  - terminal_status
  - produced_artifacts
  - triaged_issues
  - shipping_readiness
  - shipping_result
  - diagnostics
- Equivalent canonical graph output means equality for all deterministic fields above, except allowed variance in metadata timestamps, provider-specific token accounting, and trace IDs.

## High-Level Graph Topology

```text
start
  -> clarification_node
  -> spec_node
  -> plan_node
  -> task_fanout_router
      -> task_node[*] (dynamic from task DAG)
  -> quality_gate_join
      -> code_review_node
      -> security_audit_node
      -> test_engineer_node
  -> issue_orchestrator_node
  -> ship_node
  -> end
```

Control-flow behavior:
1. clarification_node runs a conversational loop to resolve scope, constraints, and success targets from the initial request.
2. spec_node generates a spec document from clarified request input.
3. plan_node generates an implementation plan and task DAG.
4. task_fanout_router materializes runnable task nodes based on DAG dependencies.
5. task nodes execute until DAG completion (including retry/failure policy).
6. quality_gate_join waits for DAG completion, then executes review/security/test branches.
7. issue_orchestrator_node aggregates and triages open issues in batch.
8. ship_node checks shipping readiness; if pass, executes shipping action; else returns blocked report.

Clarification loop rules:
- clarification_node must run before spec_node for all new requests.
- The loop continues until minimum scope completeness criteria are met or the user explicitly defers unknowns.
- If unresolved ambiguity remains above threshold, run transitions to waiting_for_input with user-friendly follow-up prompts.
- clarification prompts must be plain-language and non-technical by default.

Join semantics:
- DAG completion means all tasks are in a terminal status set: succeeded, failed, blocked, skipped, cancelled.
- Default gate mode is strict: quality_gate_join proceeds only if all tasks are succeeded or policy-approved skipped.
- In lenient mode (explicit opt-in), quality_gate_join may proceed with failed/blocked tasks and must annotate confidence and missing coverage.
- Any task in running/retrying/ready/pending keeps quality_gate_join blocked.

## State Model (Canonical Graph State)
All nodes read/write a strongly-typed shared state.

Persistence backend and availability behavior:
- Production/default backend: PostgreSQL-backed LangGraph checkpointer/store.
- Local development backend: SQLite checkpointer/store.
- If checkpointer/store is unavailable at run start, graph must fail closed before side effects.
- If checkpointer/store becomes unavailable mid-run, graph must move to blocked, persist best-effort diagnostics, and require resume from last durable checkpoint.

Core fields:
- request: original user request and constraints.
- clarifiedRequest: normalized request after conversational clarification.
- clarificationTranscript: question/answer history used to disambiguate scope.
- ambiguityStatus: current ambiguity score, unresolved dimensions, and deferments.
- defaultModelConfig: run-level default model/provider and decoding parameters.
- nodeModelOverrides: optional per-node model selections keyed by node ID.
- spec: generated spec metadata and content location.
- implementationPlan: ordered phases + dependency DAG.
- tasks: normalized task list with status and artifacts.
- reviewFindings: results from code-review node.
- securityFindings: results from security-audit node.
- testFindings: results from test-engineer node.
- triagedIssues: batch-triaged issue set with owners/priorities.
- shippingReadiness: computed readiness report and blockers.
- shippingResult: release/ship output when executed.
- runMetadata: timestamps, retries, node durations, model/tool usage, correlationId, actor identity, policy version, and gate decision rationale.

State invariants:
- spec_node must not run before clarification_node completion criteria are satisfied.
- plan_node must not run without spec output.
- task execution must be dependency-safe (a node can run only when all predecessors succeed or are explicitly skipped by policy).
- Every model-bound node must resolve to an effective model via node override or defaultModelConfig.
- ship_node must not ship when any blocker severity >= configured threshold.
- All state transitions must be legal according to the state machine below; illegal transitions are hard-fail errors.

### Run and Task State Machine
Run statuses:
- pending
- running
- waiting_for_input
- blocked
- failed
- succeeded
- cancelled

Task statuses:
- pending
- ready
- running
- retrying
- succeeded
- failed
- blocked
- skipped
- cancelled

Allowed run transitions:
- pending -> running, cancelled
- running -> waiting_for_input, blocked, failed, succeeded, cancelled
- waiting_for_input -> running, cancelled
- blocked -> running, failed, cancelled

Allowed task transitions:
- pending -> ready, skipped, cancelled
- ready -> running, skipped, cancelled
- running -> retrying, succeeded, failed, blocked, cancelled
- retrying -> running, failed, blocked, cancelled

Any transition not listed above is invalid and must fail fast with typed error diagnostics.

## Node Definitions

### 0) clarification_node (Scope Clarifier Agent)
Purpose:
- Resolve ambiguous user intent into an implementable project scope before specification.

Input:
- raw request text.
- optional repository context.

Output:
- clarifiedRequest.
- ambiguityStatus.
- clarificationTranscript.

Required behavior:
- Must ask plain-language clarifying questions across required dimensions:
  - solution type (web app, CLI, API/service, automation script, or other)
  - scope size (small prototype, feature-level, production-ready system)
  - target users and usage context
  - delivery boundaries (must-have vs nice-to-have)
  - constraints (timeline, tech preferences, integrations, compliance)
  - success criteria in user-understandable terms
- Must support a non-technical interaction style and avoid jargon.
- Must summarize back the interpreted scope and ask for confirmation before completing.
- Must set waiting_for_input when required dimensions remain unresolved and no explicit deferment is provided.
- Must produce actionable follow-up prompts in user language (for example: "Do you want a browser app, a command-line tool, or an API?").
- Must collect optional model preferences, including:
  - default model for the run
  - per-node model overrides (if the user wants different models for specific nodes)

### 1) spec_node (Spec Agent)
Purpose:
- Convert user request into a structured spec document.

Input:
- clarifiedRequest, ambiguityStatus, constraints, repo metadata.

Output:
- spec document artifact path and summary.
- acceptance criteria list.
- open questions/assumptions list.

Required behavior:
- Must produce verifiable acceptance criteria.
- Must identify explicit assumptions.
- Must not proceed when ambiguityStatus exceeds threshold and unresolved required dimensions exist.
- Must return user-friendly clarification prompts instead of technical diagnostics when input remains underspecified.

### 2) plan_node (Plan Agent)
Purpose:
- Translate spec into implementation plan and task DAG.

Input:
- spec artifact.

Output:
- implementation plan.
- task DAG with task IDs, dependencies, acceptance checks, estimated complexity.

Required behavior:
- Must emit acyclic DAG.
- Must include verification step per task.
- Must include deterministic task IDs and planRevision metadata.

### 3) task_fanout_router
Purpose:
- Dynamically schedule task_node executions from plan DAG.

Input:
- task DAG + current task statuses.

Output:
- next runnable task set.

Required behavior:
- Supports parallel execution for independent tasks.
- Supports retries with capped attempts.
- Records per-task artifact references.
- Is sole writer for scheduling fields and retry counters.
- Detects and handles write conflicts deterministically (optimistic concurrency token or equivalent guard).
- Applies planRevision lock semantics: router may schedule tasks only for the active revision lock held by the run.
- Rejects stale revision writes and retries with latest revision snapshot.

### 4) task_node[*] (Dynamic Task Execution Nodes)
Purpose:
- Execute one implementation task via one of:
  - pure agent node,
  - agent-with-tools node,
  - direct-tools node.

Input:
- task definition + scoped context.

Output:
- task status (succeeded/failed/blocked).
- produced artifacts (files, diffs, reports).
- verification evidence (tests/build/lint output references).

Required behavior:
- Enforce task-level schema validation.
- Persist all artifacts for later review nodes.
- Is sole writer for execution result, verification evidence, and produced artifact metadata.
- Must be idempotent for retried execution of side-effecting operations.

### 5) code_review_node (Code Reviewer Agent)
Purpose:
- Evaluate changes for correctness, readability, architecture, security, performance.

Input:
- aggregated task artifacts and diffs.

Output:
- structured findings with severity and fix suggestions.

### 6) security_audit_node (Security Auditor Agent)
Purpose:
- Perform threat-focused review of produced changes and tool usage.

Input:
- diffs, dependency changes, runtime surfaces.

Output:
- security findings, risk score, required remediations.

### 7) test_engineer_node (Test Engineer Agent)
Purpose:
- Evaluate test quality and missing coverage, propose/add test tasks.

Input:
- code/test artifacts and execution results.

Output:
- test findings, coverage gaps, optionally additional task recommendations.

Loopback protocol:
- If additional tasks are accepted, graph re-enters task_fanout_router with incremented planRevision.
- Quality nodes re-run on delta scope by default; full scope re-run is policy-configurable.
- Maximum quality-task loop iterations are capped at 3 for every run. Exceeding this cap blocks the run and requires human intervention.

Delta scope definition:
- Delta includes newly added tasks, modified tasks, and all transitively dependent tasks in the DAG.
- Delta-derived verification set includes changed files, affected tests, and impacted quality findings linked by artifact fingerprints.

Mid-run DAG mutation and locking semantics:
- DAG mutations are append-only within the current loop cycle; previously succeeded tasks remain immutable.
- Router acquires revision lock before scheduling any mutated DAG tasks.
- In-flight tasks from prior revision are allowed to finish, but their outputs are reconciled against the newest revision before join.
- quality_gate_join only evaluates tasks from the highest committed planRevision.

### 8) issue_orchestrator_node
Purpose:
- Batch triage all open issues from review/security/test outputs.

Input:
- findings from the 3 quality nodes.

Output:
- deduplicated issues with severity, owner, priority, and disposition:
  - must-fix-before-ship
  - can-follow-up
  - rejected/false-positive

Issue contract (required fields):
- id
- sourceNode
- fingerprint
- severity
- confidence
- owner
- disposition
- remediation
- evidenceLinks
- slaTarget

Required behavior:
- Deterministic deduplication strategy.
- Policy-based severity thresholding.
- Initial source-of-truth issue tracker is GitHub Issues.
- All issue read/write operations must execute through MCP tools (no direct tracker API calls from graph nodes).

### 9) ship_node (Shipping Agent)
Purpose:
- Execute final readiness gate and shipping action.

Input:
- triaged issues + all prior artifacts.

Output:
- shippingReadiness report.
- shippingResult (executed) OR blocked report (not executed).

Required behavior:
- Readiness checks must run before any shipping action.
- If blockers exist, shipping must be skipped with explicit blocker list.

Default shipping policy:
- Unresolved Critical or High security findings are must-fix-before-ship blockers.
- Missing, errored, or stale security reports fail closed and block shipping.
- Autonomous merge/release is disabled by default in production.
- Production shipping requires explicit human approval even when all gates pass.

## Node Capability Types
Each executable node must declare one of:
- AGENT_ONLY: model-driven reasoning, no external tool execution.
- AGENT_WITH_TOOLS: model node with constrained tool access.
- DIRECT_TOOL: deterministic tool/action node without model reasoning.

Model selection contract:
- User may define one default model used by all model-bound nodes unless overridden.
- User may override model selection at node level for AGENT_ONLY and AGENT_WITH_TOOLS nodes.
- DIRECT_TOOL nodes do not use LLM model selection.
- Effective model resolution order:
  1. nodeModelOverrides[node_id]
  2. defaultModelConfig
  3. system fallback model policy (only if user did not provide a default)
- Unknown or unauthorized model identifiers must fail with typed configuration error and user-friendly remediation guidance.
- Model configuration changes are versioned in runMetadata for auditability.

Capability contract:
- Default-deny tool execution; only explicitly allowlisted tools may run.
- Explicit allowed tools list per node with validated argument schemas and max input sizes.
- Tool timeout/retry/concurrency policy per node.
- Tool side-effect class per tool: READ_ONLY, WRITE_SCOPED, DESTRUCTIVE.
- DESTRUCTIVE tools are disallowed unless explicitly enabled by node policy.
- WRITE_SCOPED tools must enforce workspace-root allowlist and reject traversal/symlink escape.
- Auditable trace of tool invocations.

## LangGraph Studio Visualization Requirements
- Graph must be loadable in LangGraph Studio as a named graph.
- Node names must match canonical IDs in this spec.
- Dynamic task nodes must be visible as runtime-expanded nodes or grouped subgraph entries.
- State snapshots must be inspectable per node transition.
- Execution traces must show branch/join timing and retry attempts.

## Failure Handling and Recovery
- Per-node retry policies with exponential backoff.
- Hard-fail classes (schema violation, invalid DAG, shipping policy violation).
- Soft-fail classes (transient tool timeout, rate limiting).
- Resume-from-checkpoint support for interrupted runs.
- Manual override requires two distinct human approvers, explicit scope, expiry timestamp, and recorded justification.

Failure policy matrix (minimum requirements):
- Every capability type must define retryable vs non-retryable error classes.
- Every node must define max attempts and backoff formula.
- Side-effecting steps must define idempotency key and compensation/rollback expectations.
- Checkpoint resume must guard against replaying already-applied side effects.

## Security and Governance
Always:
- Validate all node inputs/outputs with Pydantic.
- Source secrets only from secret manager or scoped environment injection.
- Redact secrets before persisting logs, issues, traces, or artifacts.
- Run automated secret scanning on diffs and artifacts before shipping.
- Log all tool invocations and policy decisions.
- Maintain append-only tamper-evident audit records.

Tool runtime isolation:
- Tool execution runs in isolated runtime with least privilege.
- Outbound network egress is deny-by-default and allowlisted per node/tool destination.
- Per-node secrets exposure is minimized; global secret exposure to tools is prohibited.

Ask first:
- Adding new external execution tools with write access.
- Relaxing shipping blocker thresholds.
- Enabling autonomous merge/release without approval.

Never:
- Ship with unresolved must-fix-before-ship issues.
- Execute destructive repository actions without explicit policy.
- Store credentials in code, logs, or artifacts.
- Bypass policy gates through implicit or undocumented overrides.

## Testing Strategy
Testing layers:
- Unit tests: node reducers, schema validation, DAG scheduler logic.
- Integration tests: full graph execution with mocked agents/tools.
- Scenario tests: success path, blocked ship path, retry/recovery path, invalid DAG rejection.

Required test cases:
1. clarification_node resolves ambiguous prompt into clarifiedRequest before spec generation.
2. clarification_node enters waiting_for_input with plain-language prompts when required dimensions are missing.
3. spec_node cannot execute before clarification completion criteria are met.
4. spec_node returns user-friendly follow-up prompts (not technical diagnostics) for underspecified clarifiedRequest.
5. spec_node outputs valid spec schema and acceptance criteria.
6. plan_node emits acyclic DAG; cycle detection fails fast.
7. task_fanout executes independent tasks in parallel and respects dependencies.
8. quality nodes run only after task DAG completion.
9. issue_orchestrator deduplicates and classifies issues deterministically.
10. ship_node blocks on must-fix issues and ships when clear.
11. Studio visualization metadata is present and graph compiles for Studio loading.
12. Illegal run/task state transitions are rejected as typed hard-fail errors.
13. Join behavior under mixed outcomes matches configured strict or lenient mode.
14. Retry policy follows configured cap and backoff, with idempotent side-effect protection.
15. Concurrent task updates do not lose writes; conflicts are detected and resolved deterministically.
16. Resume-from-checkpoint run reaches equivalent final state as uninterrupted execution.
17. Prompt-injection attempts cannot escalate tool permissions.
18. Path traversal/symlink escape attempts are blocked for WRITE_SCOPED tools.
19. Unauthorized network egress attempts are blocked and auditable.
20. Secret canaries are redacted and scanner failures block shipping.
21. Override requests without two approvals/scope/expiry are rejected.
22. Production shipping path requires explicit human approval and blocks without it.
23. Quality-task loop terminates at cap of 3 with blocked status and intervention request.
24. Issue tracker operations execute through MCP tools and target GitHub Issues.
25. GitHub Copilot adapter normalizes requests/responses to canonical schema and runs end-to-end.
26. Codex adapter normalizes requests/responses to canonical schema and runs end-to-end.
27. Claude Code adapter normalizes requests/responses to canonical schema and runs end-to-end.
28. Equivalent inputs from all three assistants produce equivalent graph state transitions and terminal outcomes.
29. Assistant adapter errors map to typed integration errors without corrupting graph state.
30. Router enforces revision lock semantics and rejects stale planRevision scheduling attempts.
31. Mid-run DAG mutations reconcile in-flight prior-revision outputs without violating join invariants.
32. Checkpointer unavailability at run start fails closed before side effects.
33. Mid-run checkpointer outage transitions run to blocked and supports checkpoint-based resume.
34. Run-level default model is applied to all model-bound nodes when no node override exists.
35. Node-level model override takes precedence over default model for the targeted node only.
36. DIRECT_TOOL nodes ignore model configuration without failure.
37. Invalid model IDs produce typed configuration errors with user-friendly guidance.

Coverage targets:
- >= 80% for scheduler/state transition modules.
- >= 70% overall project coverage.

## Acceptance Criteria
1. Every new run starts with clarification_node and collects sufficient scope detail or explicit deferments before spec generation.
2. Non-technical users receive plain-language clarification questions and confirmation summary before spec_node runs.
3. spec_node is blocked when unresolved ambiguity exceeds threshold and resumes only after clarification input.
4. Given a valid request, graph run generates spec, plan, tasks, quality outputs, issue triage, and either ship result or blocked report.
5. Task execution is dynamically fanned out from plan DAG with dependency-safe scheduling.
6. code_review_node, security_audit_node, and test_engineer_node all execute and emit structured findings.
7. issue_orchestrator_node produces deduplicated triage output with explicit disposition.
8. ship_node performs readiness checks before shipping and blocks on configured blockers.
9. Graph is visualizable in LangGraph Studio with observable state transitions.
10. For fixed input and seed, scheduler behavior is deterministic (or follows declared partial-order constraints).
11. If test_engineer_node proposes accepted tasks, graph re-enters fanout with incremented planRevision and re-runs required quality gates before shipping.
12. Shipping is blocked when unresolved Critical/High security findings exist or required security evidence is missing.
13. Run artifacts include auditable correlationId, policy version, and provenance chain for shipping decisions.
14. In production, shipping is blocked unless human approval is explicitly recorded.
15. Quality-task refinement loop never exceeds 3 iterations in a single run.
16. GitHub Issues is used as the initial issue source of truth via MCP tools.
17. GitHub Copilot, Codex, and Claude Code can each invoke the graph through MCP-compatible interfaces.
18. For equivalent requests, all supported assistant integrations produce equivalent canonical graph outputs.
19. Shipping is blocked when secret scanning reports failures.
20. Checkpointer availability failures are handled with fail-closed start behavior and blocked mid-run recovery behavior.
21. Users can set a default LLM model for a run, and all eligible nodes inherit it unless overridden.
22. Users can set per-node LLM model overrides, and override precedence is enforced deterministically.

## Verification Commands
- mypy --strict --disallow-any-generics src
- pytest
- pytest --cov=src --cov-report=term-missing --cov-fail-under=70
- pytest tests/scheduler tests/state_transition --cov=src/scheduler --cov=src/state_transition --cov-fail-under=80
- python -m compileall src
- langgraph dev (manual verification: graph loads and visualizes correctly)

## Deliverables
- Graph specification document (this file).
- Node contract definitions (to be implemented from this spec).
- State schema definition and transition rules.
- Model configuration schema covering default and node-level override contracts.
- Issue and shipping readiness schema definitions.
- Test plan and acceptance suite (to be implemented from this spec).
- Representative run trace proving Studio visualization and checkpoint resume behavior.
- Assistant adapter contract specification for GitHub Copilot, Codex, and Claude Code.
- Integration verification report showing successful runs from all three assistant adapters.

## Resolved Decisions
1. Production shipping requires mandatory human approval.
2. Quality-task loop is capped at 3 iterations per run.
3. Initial issue tracker is GitHub Issues.
4. Tracker operations must use MCP tools.
5. Scope clarification loop is mandatory before spec generation for new requests.
