# Changelog

All notable changes to this project will be documented in this file.

## [0.2.1] - 2026-06-12

### Fixed

- **Deep-agent recursion no longer aborts the run.** When a deep-agent
  bounded executor hits its recursion limit inside `task_node` or
  `code_review_node`, the node now reports a structured failure
  (failed task + `DeepAgentTrace`) and lets the pipeline continue
  through the remaining quality gates and `ship_node`, instead of
  raising `RecursionLimitExceededError` and crashing the whole graph.
- **Verifier-loop guard is now persistent.** Anti-loop counters for
  `run_tests` / `run_lint` / `run_typecheck` survive deep-agent /
  tool-wrapper rebuilds (keyed by `workdir + tool`), so repeated
  successful checks against an unchanged workdir are blocked across
  fan-out waves rather than resetting on every wrapper rebuild.
- Workdir-fingerprint change detection added to the verifier guard;
  no-change repeated calls trip the threshold without needing a hard
  recursion limit.

### Added

- Regression tests in `tests/deep_agents/test_factory.py`,
  `tests/unit/test_task_runner_per_task.py`, and
  `tests/unit/test_node_review_deep_agent.py` covering persistent
  guard state and deep-agent recursion fallback paths.

## [0.2.0] - 2026-06-12

### Added

- **Deep Agents pipeline** (spec `docs/specs/flowforge-deep-agents-enhancement.md`):
  every agentic node (`clarification`, `spec`, `plan`, `task_runner`,
  `code_review`, `security_audit`, `test_engineer`, `issue_orchestrator`)
  now has a Deep-Agent variant backed by `deepagents.create_deep_agent`,
  per-role instructions, sub-agent dispatch, virtual filesystem
  materialisation, and a structured `DeepAgentTrace` per node.
- **Per-run resource budgets** — recursion limit 50, timeout 300 s, and a
  tool budget of 200 invocations enforced via `ContextVar`. All three
  knobs are overridable through `FLOWFORGE_DEEP_*` env vars.
- **Implementer secret scanner** — diff-based scanner runs before any
  artifact is persisted; HIGH-confidence findings (AWS, GitHub PATs
  classic + fine-grained, OpenAI classic / project / service-account,
  Slack, Google API, Stripe live, PEM private keys) block the run. The
  offending file never touches disk; prior tasks’ files are committed
  before the BLOCKED return.
- **Contract harness** (`tests/contract/test_legacy_vs_deep.py`) — every
  agentic node’s legacy and deep paths are tested for top-level state
  shape parity, finding-count band, and trace propagation.
- **A/B implementer harness** across 5 fixtures including a planted-
  secret case.
- **Nightly E2E demo workflow** — `.github/workflows/nightly-deep-agent.yml`
  runs the canonical `build tic-tac-toe web app` demo with both
  `FLOWFORGE_DEEP_AGENTS=0` and `=1`, gates releases on test-count
  regression, and re-runs the suite under `OPENAI_API_KEY=invalid` to
  confirm zero real LLM calls (spec §13.12).

### Changed

- **Default-on rollout (T14)** — `swe-forge setup` now writes
  `{"deep_agents": true}` into `~/.flowforge/config.json`, and the
  resolved fallback when no CLI / env / config value is present is also
  `True`. Existing config files are respected as written.

### Deprecated

- `--no-deep-agents` and `FLOWFORGE_DEEP_AGENTS=0` remain available for
  one minor version (slated for removal in `v0.4`) per spec §13.15.
  Use either flag to opt back into the legacy single-shot executors.

### Migration

- Users on `0.1.x` will see Deep Agents activate automatically on the
  next run. To preserve the legacy behaviour, either:
  - run with `swe-forge run "<prompt>" --no-deep-agents`, or
  - export `FLOWFORGE_DEEP_AGENTS=0`, or
  - edit `~/.flowforge/config.json` to set `"deep_agents": false`.
- Test suites that exercised the legacy path implicitly should pin the
  env var to `0` (see `tests/conftest.py` for the project pattern).

## [0.1.0] - 2026-06-05

### Added

- Full LangGraph pipeline: clarification → spec → plan → execute → quality → ship
- Conversational clarification node with 6 required dimensions
- Spec generation from clarified requests
- Plan generation with acyclic task DAG validation (Kahn's algorithm)
- DAG-based task scheduler with dependency-aware dispatch
- Revision locking with optimistic concurrency control
- Append-only DAG mutation for mid-run quality loopback
- Three capability executors: agent-only, agent-with-tools, direct-tool
- Idempotency store with compensation strategies
- Tool allowlist and policy enforcement (destructive blocking, path traversal prevention)
- Code review, security audit, and test engineer quality gate nodes
- Quality loopback protocol capped at 3 iterations
- Issue orchestrator with deduplication, fingerprinting, and LLM-based triage
- Ship node with readiness gate, blocker enforcement, and production approval
- Model selection with 3-tier resolution (override → default → fallback)
- Checkpointer wrapper with fail-closed semantics
- State machine with validated transitions for run and task status
- Three assistant adapters: GitHub Copilot, Codex, Claude Code
- Canonical request/response schemas with cross-adapter equivalence
- Rate limit policy per adapter
- 400 tests, 98% coverage, strict mypy, ruff lint/format
