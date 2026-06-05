# Changelog

All notable changes to this project will be documented in this file.

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
