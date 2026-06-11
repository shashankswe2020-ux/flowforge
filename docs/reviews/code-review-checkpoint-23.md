# Code Review Checkpoint 23: Phase 3 Implementer Deep Agent (T9)

> **Reviewer:** Code Reviewer Agent (Staff Engineer)
> **Date:** 2026-06-12
> **Scope:** Uncommitted working-tree diff vs. `HEAD` (`f9f6df3`).
> Files: [flowforge/deep_agents/secret_scanner.py](../../flowforge/deep_agents/secret_scanner.py),
> [flowforge/nodes/task_runner.py](../../flowforge/nodes/task_runner.py),
> [flowforge/deep_agents/instructions/implementer.md](../../flowforge/deep_agents/instructions/implementer.md),
> [tests/deep_agents/test_secret_scanner.py](../../tests/deep_agents/test_secret_scanner.py),
> [tests/unit/test_node_task_deep_agent.py](../../tests/unit/test_node_task_deep_agent.py),
> [tests/integration/test_implementer_ab_harness.py](../../tests/integration/test_implementer_ab_harness.py),
> [tests/contract/test_legacy_vs_deep.py](../../tests/contract/test_legacy_vs_deep.py).
> **Gate:** Plan §T9 acceptance criteria.
> **Verification (re-run live):** `pytest -q` → **763 passed, 1 skipped**;
> `mypy flowforge` → 14 errors (pre-existing baseline; 0 new); `ruff check
> flowforge tests` → 129 errors (was 130 baseline; net −1, 0 new).

---

## Verdict: ✅ **APPROVE WITH FOLLOW-UPS**

**Counts:** Critical 0 · Important 4 · Minor 5

**Overview:** T9 lands the implementer Deep Agent variant cleanly. The
secret-scanner module is well-scoped and well-tested (14 dedicated unit
tests covering all five high-confidence patterns plus negative and
entropy paths); the wrapper threads the `invocation_sink` plumbing
established in T8, integrates the scanner as a synchronous post-task
gate, and emits a `BLOCKED` `RunStatus` consistent with the state
machine (`RUNNING → BLOCKED` is legal per
[machine.py:11-19](../../flowforge/state/machine.py#L11)). The 15 A/B
harness tests + 4 contract tests give the deep/legacy parity story
real coverage. **Four Important issues** are flagged: (1) the
synthetic-diff approach treats the agent's *whole rewritten file* as
new lines and so will block on a pre-existing secret the agent merely
re-emitted; (2) the prompt promises a `vfs:/context/implementer_output.json`
summary that the wrapper never reads, so verification evidence is
silently dropped; (3) successful intermediate writes are *not* rolled
back when a later task is BLOCKED (uncommitted secrets-adjacent files
remain on disk); (4) the deep wrapper marks tasks `SUCCEEDED` without
ever validating the agent actually ran tests. None are merge-blocking;
all should be resolved before T14 flips the default.

---

## Plan §T9 Acceptance Criteria — Audit

| Criterion | Status | Evidence |
|---|---|---|
| `task_node` deep variant invokes `implementer` with `refactorer` + `doc_writer` sub-agents | ✅ | [task_runner.py:226](../../flowforge/nodes/task_runner.py#L226) builds with `role=AgentRole.IMPLEMENTER`; sub-agents wired into the role catalog at T1 (verified upstream); prompt at [implementer.md:32-39](../../flowforge/deep_agents/instructions/implementer.md#L32-L39) names both sub-agents. |
| Secret scanner runs after each task; HIGH blocks the run | ✅ | [task_runner.py:251-258](../../flowforge/nodes/task_runner.py#L251-L258); `has_blocking_secret` returns the run with `RunStatus.BLOCKED` and `TaskStatus.BLOCKED`. Test: `test_high_confidence_secret_blocks_run`. |
| Tool budget 200 / recursion 50 / timeout 300s, overridable | ✅ | Reuses `run_deep_agent_bounded` per task; per-call enforcement is automatic (T10). See Important #5 — the *cumulative* budget is `N × 200` for `N` tasks. |
| A/B harness over 5 fixture tasks | ✅ | `_FIXTURES` has 5 tasks (cli_greet, parser, config_loader, multi_file, planted_aws_secret); 3 parametrized scenarios = 15 tests. |

Carry-over from Checkpoint 22 (Important #2): `vfs:/context/clarified_request.json`
is now materialized in [adapters.py:113-116](../../flowforge/deep_agents/adapters.py#L113-L116). ✅ resolved.

---

## Critical Issues

**None.**

---

## Important Issues

### 1. Synthetic-diff over the whole artifact will block on a pre-existing secret the agent merely re-emitted
- **File:** [flowforge/nodes/task_runner.py:194-208](../../flowforge/nodes/task_runner.py#L194-L208) (`_scan_artifacts_for_secrets`).
- **Problem:** The wrapper synthesises a "diff" by emitting `+` for *every line of every artifact* — i.e., it treats a complete file rewrite as 100 % added. If the agent reads `vfs:/src/cfg.py` (which already contains a token-shaped string elsewhere in the codebase that has been there for months — e.g., a test fixture, a redacted example in a comment, or a Slack token *substring* in a URL fragment), refactors *one unrelated function*, then writes the file back, the scanner blocks the run on a string the agent did not introduce. The synthetic-diff is a **sound upper bound on agent output**, not a sound model of "what changed in this task."
- **Impact:** False-positive blocking when the agent legitimately re-emits an existing file. The asymmetry the A/B harness already documents in `test_legacy_path_produces_artifact` ("legacy executor has no secret scanner — every fixture writes the file") becomes false-positive blocking in production where files have history.
- **Recommendation:** Compute a real diff against the on-disk content. The persistence helper already knows which path it's writing; in `persist_files`, capture `(path, before_content, after_content)` and let `_scan_artifacts_for_secrets` consume that triple. For brand-new files, every line is added — same behaviour as today. For modified files, only the line-add positions feed the scanner. (Shelling out to `git diff` is *also* sound but adds a subprocess per task; computing the diff in-process via `difflib.unified_diff` is cheaper and avoids the dependency on a workdir being a git repo.)
- **Priority:** Important. Without this fix, the gate is over-eager precisely on the realistic case (refactor of an existing module) and gets the safe case (fresh file) right by accident. Block T14 on this.

### 2. The wrapper never reads `vfs:/context/implementer_output.json` — `verification_evidence` is silently dropped
- **Files:** [implementer.md:42-65](../../flowforge/deep_agents/instructions/implementer.md#L42-L65), [task_runner.py:299-308](../../flowforge/nodes/task_runner.py#L299-L308).
- **Problem:** The prompt instructs the agent to write a summary file with `verification_evidence` (e.g., `"pytest tests/test_foo.py -q → 3 passed"`) at `vfs:/context/implementer_output.json`. The wrapper never reads it: `persist_files` skips the `context/` namespace by design ([adapters.py:39-44](../../flowforge/deep_agents/adapters.py#L39)), so the summary lives in `result["files"]` and dies there. The wrapper builds `Task(...)` with `verification_evidence=[]` unconditionally on the success path ([task_runner.py:300-307](../../flowforge/nodes/task_runner.py#L300-L307)).
- **Impact:** The downstream `code_review`/`security_audit`/`test_engineer` nodes lose the agent-asserted verification record. The TDD methodology in the prompt is unenforceable: the framework cannot tell whether the agent ran `run_tests` at all. Combined with Important #4, this means the deep path can mark every task SUCCEEDED with zero evidence.
- **Recommendation:** In `_run_via_deep_agent`, after each task, look up `result["files"].get("vfs:/context/implementer_output.json")`, parse it, and copy `verification_evidence` (and `notes`) onto the constructed `Task`. Reject the task (or mark `BLOCKED` with `error_message="implementer omitted summary"`) if the summary is missing or malformed — the prompt promises it.
- **Priority:** Important.

### 3. Mid-loop BLOCK leaves earlier tasks' files on disk un-rolled-back and uncommitted
- **File:** [flowforge/nodes/task_runner.py:255-294](../../flowforge/nodes/task_runner.py#L255-L294).
- **Problem:** When task `k` triggers `has_blocking_secret`, the wrapper returns immediately with `RunStatus.BLOCKED`. But for every task `0 .. k-1` that succeeded, `persist_files` already wrote files to `workdir`. The wrapper never calls `_commit_artifacts` on the BLOCKED return path (line 286-294), so:
  - Files from task 0 sit uncommitted in the workdir.
  - The pipeline's BLOCKED state is observed by the graph, but a manual recovery / inspection sees an inconsistent tree (some changes written, none committed, no diff vs. HEAD that matches the actual state).
  - If a follow-up run resumes from the checkpoint, the workdir is dirty and downstream nodes see "real code" without provenance.
- **Impact:** Operational. Not a security or correctness bug per se, but it muddies the recovery story the BLOCKED state is supposed to enable. Compare with `ship.py:663` (the other `RunStatus.BLOCKED` site), which doesn't mutate the workdir before blocking.
- **Recommendation:** On the BLOCK path, either:
  - (a) call `_commit_artifacts(workdir, written_paths)` before returning, so the partial state is at least committed and inspectable, **or**
  - (b) revert `written_paths` (`git checkout -- <paths>` for tracked files, `unlink` for new files) so a BLOCK leaves the workdir at HEAD.
  
  (b) is the safer default for a security gate. If the caller wants to inspect the partial work they can re-run with the scanner disabled.
- **Priority:** Important.

### 4. Successful task = "any artifact persisted" — no verification of test/lint/typecheck outcome
- **File:** [flowforge/nodes/task_runner.py:300-307](../../flowforge/nodes/task_runner.py#L300-L307).
- **Problem:** The wrapper constructs the success-path `Task(..., status=TaskStatus.SUCCEEDED)` whenever `persist_files` returned a non-empty list. There is *no* check that the agent ran `run_tests`, that the run passed, that `run_lint` was clean, or that the agent even loaded the spec. A model that writes one stub file and exits returns SUCCEEDED. The legacy path runs through `execute_task` which has its own validation hooks; the deep path skips that entire validation surface.
- **Impact:** Quality. A misbehaving agent (or one that hits the recursion cap mid-task) ships green-marked tasks downstream. `code_review_node` is the next gate, but it can't substitute for the agent's own verification — it reviews finished code, not the path taken to get there.
- **Recommendation:** Combine with Important #2 — require a non-empty `verification_evidence` array on the parsed summary file, and at minimum require one entry that mentions `pytest`/`run_tests`. If absent, mark the task `FAILED` (or `BLOCKED`) with the appropriate `error_message`. Better still, scan `tool_invocations` for a `run_tests` call before flagging SUCCEEDED — that's authoritative since it's framework-recorded, not agent-claimed.
- **Priority:** Important.

---

## Suggestions (Minor)

### 1. OpenAI `sk-proj-...` keys are not matched
- **File:** [flowforge/deep_agents/secret_scanner.py:54](../../flowforge/deep_agents/secret_scanner.py#L54).
- **Problem:** Pattern `sk-[A-Za-z0-9]{20,}\b` excludes hyphens, so OpenAI's project-scoped keys (`sk-proj-...`) — the default for new keys since 2024 — are not flagged as HIGH. The entropy heuristic *might* catch them if quoted with sufficient length, but they fall to MEDIUM at best.
- **Recommendation:** Two patterns or one generous form: `sk-(?:proj-)?[A-Za-z0-9_\-]{20,}\b`. Likewise consider adding GitHub `ghs_`/`gho_`/`ghu_`/`ghr_`/`github_pat_` prefixes; Google `AIzaSy[A-Za-z0-9_\-]{33}`; Stripe `sk_live_[A-Za-z0-9]{24,}`. Each is a one-line addition with a regression test.

### 2. Per-task budget × N is a cumulative budget of `N × 200` tool calls per run
- **File:** [flowforge/nodes/task_runner.py:225-244](../../flowforge/nodes/task_runner.py#L225-L244).
- A 12-task plan permits up to 2400 tool calls before the gate trips. The plan §T9 acceptance text says "Tool budget enforced at 200" — strictly true per call, not per run. Either document this explicitly in the wrapper's docstring, or thread a *run-level* counter that decrements across tasks. The latter aligns better with cost-control intent. Not a defect against the literal acceptance criterion.

### 3. `SecretSeverity.LOW` is declared but never emitted
- **File:** [flowforge/deep_agents/secret_scanner.py:35](../../flowforge/deep_agents/secret_scanner.py#L35).
- The enum value `LOW` is unused. Either (a) emit it for entropy findings below a second-tier threshold (e.g., 3.5 < entropy < 4.0), or (b) drop it from the enum. Dead code in a security-adjacent module is a foot-gun — a future contributor will assume "LOW" means the scanner has a tier when it doesn't.

### 4. `SecretFinding.line` is the diff-line index, not the source line
- **File:** [flowforge/deep_agents/secret_scanner.py:81-83](../../flowforge/deep_agents/secret_scanner.py#L81-L83).
- The dataclass field is named `line` and the docstring says "One detected secret in an added diff line." A reader will assume `line` maps to the source file. It currently maps to the position within the synthetic diff text (each artifact's headers + content). Once Important #1 is addressed and a real diff is computed, the right fix is to record `(file_path, source_line)` on the finding. Today, rename the field to `diff_line_index` or document the meaning explicitly.

### 5. `_build_task_prompt(task_definition: Any)` should be typed
- **File:** [flowforge/nodes/task_runner.py:181](../../flowforge/nodes/task_runner.py#L181).
- `TaskDefinition` is already imported at module scope (line 32). The `Any` + `noqa: ANN401` is unnecessary and out of step with the project's "no `any`" convention from `.github/copilot-instructions.md`. Use `def _build_task_prompt(task_definition: TaskDefinition) -> str:`.

---

## What's Done Well

- **The secret scanner is the right shape: a pure function on a string, with a separate `has_blocking_secret` predicate.** No I/O, no globals, no hidden state. The 14-test file walks every regex and the entropy path (positive + negative + boundary). `test_ignores_diff_metadata_lines` and `test_ignores_removed_lines` are exactly the safety tests I'd write — they prove the function rejects the two classes of false positive that diff-based scanners famously hit. ([secret_scanner.py:74-103](../../flowforge/deep_agents/secret_scanner.py#L74-L103))
- **Strict regex anchors throughout.** `\bAKIA[0-9A-Z]{16}\b` will not match `AKIAxxx16` in flowing prose; `ghp_[A-Za-z0-9]{36}\b` enforces the exact 36-char body. The patterns prefer false negatives (Important #1 sketches additions) over false positives, which is the right tradeoff for a *blocking* gate. ([secret_scanner.py:48-58](../../flowforge/deep_agents/secret_scanner.py#L48-L58))
- **`invocation_sink` plumbing carried through cleanly from T8.** Every `run_deep_agent_bounded` call passes a fresh per-task `invocations: list[ToolInvocationRecord]` and aggregates into `aggregate_invocations` ([task_runner.py:240-244, 281, 302](../../flowforge/nodes/task_runner.py#L240)). The trace is built once at the end, with `vfs_keys` deduplicated via a set. Symmetric on the BLOCK path (lines 268-285).
- **A/B harness fixture set is well-chosen.** Five fixtures span single-file, multi-file, parser, config, and the planted-secret negative control. The `expects_block` flag in the fixture dataclass keeps the parametrization readable. Skipping the legacy/deep-paths comparison on `expects_block=True` ([test_implementer_ab_harness.py:209](../../tests/integration/test_implementer_ab_harness.py#L209)) is the right call — comparing paths on a blocked run would assert a contract that doesn't hold.
- **Test pyramid is correct.** 14 unit tests for the pure-function scanner, 6 unit tests for the wrapper with stubbed `build_deep_agent`/`run_deep_agent_bounded`, 15 integration tests for the A/B harness, 4 contract tests for legacy/deep parity. Each level captures the behaviour at the lowest tier that proves it. The contract tests reuse the existing `GenerativeCase` shape from T8 — zero harness drift.
- **State machine compatibility verified by structure, not by mock.** The wrapper returns `RunStatus.BLOCKED` only after constructing real `Task` objects with `TaskStatus.BLOCKED`, and the `RunStatus.RUNNING → BLOCKED` transition is legal per `_RUN_TRANSITIONS` ([machine.py:11-19](../../flowforge/state/machine.py#L11)). The shape matches the pre-existing `ship.py:663` BLOCK path, so the state machine surface stays uniform across the two BLOCK sites in the codebase.
- **`materialize_files` carry-over fixes Checkpoint 22 Important #2.** `vfs:/context/clarified_request.json` is now seeded into the implementer's VFS ([adapters.py:113-116](../../flowforge/deep_agents/adapters.py#L113-L116)). Combined with `spec.json` and `plan.json`, the implementer has the full prior-context chain.
- **Per-task DeepAgent instantiation is intentionally fresh.** `build_deep_agent` is called inside the task loop (line 226). This isolates each task's bounded context — no cross-task ContextVar contamination, no need to reset `_BUDGET_VAR` between tasks. ContextVar thread-safety is handled by the per-call `set/reset` pattern that T10 already established; under LangGraph's parallel branches each branch gets its own ContextVar context (asyncio task-local), so concurrent `task_node` invocations across runs don't share budget state. **Low concern for the parallel-execution question raised in the request.**
- **Implementer prompt has the right boundaries.** "Never write secrets", "Never disable, skip, or delete tests to make a build green", "Never write outside `vfs:/`", "Never introduce new runtime dependencies without an explicit allowance" — these four bans are exactly the load-bearing safety properties for an autonomous code-writing agent. ([implementer.md:78-90](../../flowforge/deep_agents/instructions/implementer.md#L78-L90))

---

## Verification Story

| Check | Status | Notes |
|-------|--------|-------|
| Tests reviewed | ✅ | All 39 new tests (14 scanner unit + 6 wrapper unit + 15 A/B harness + 4 contract) read end-to-end. |
| Tests pass | ✅ | `pytest -q` → 763 passed, 1 skipped (vs. 710 baseline; +53 = +14 + +6 + +15 + +4 = +39 new + 14 from earlier T8 fixes touched). |
| mypy | ✅ | 14 errors, all pre-existing baseline. 0 new from T9. |
| ruff | ✅ | 129 errors (was 130). Net −1, 0 new. |
| Security | ⚠️ Partial | Scanner correctly rejects removed lines and `+++` headers; missing OpenAI `sk-proj-` and several GitHub token prefixes (Suggestion #1). Synthetic-diff is overly broad (Important #1). No new shell-out paths. |
| Spec §T9 acceptance | ✅ | All four criteria met. |
| Prior findings (Checkpoint 22) | ⚠️ Partial | #2 (clarified_request VFS) resolved. #1 (sub-agent linkage), #3 (triager fallback trace), #4 (`_parse_issue_items` direct tests) remain open per Checkpoint 22 action items — out of scope for T9. |

---

## Action Items

| # | Priority | Issue | Target |
|---|----------|-------|--------|
| 1 | Important | Replace synthetic full-file diff with a real before/after line diff in `_scan_artifacts_for_secrets` | Pre-T14 (block default flip) |
| 2 | Important | Read `vfs:/context/implementer_output.json` from the deep result and populate `Task.verification_evidence` | Pre-T14 |
| 3 | Important | On secret-scanner BLOCK, revert (or commit-and-mark) earlier-task files instead of leaving them dirty in workdir | Pre-T14 |
| 4 | Important | Require `tool_invocations` to include `run_tests` before marking a deep-path task SUCCEEDED | Pre-T14 |
| 5 | Suggestion | Extend regex catalogue: `sk-proj-`, GitHub `ghs_`/`gho_`/`ghu_`/`ghr_`/`github_pat_`, Google `AIzaSy`, Stripe `sk_live_`/`pk_live_` | Backlog |
| 6 | Suggestion | Document or implement run-level (not per-task) tool budget | Backlog |
| 7 | Suggestion | Use `SecretSeverity.LOW` (sub-threshold entropy tier) or remove the enum value | Backlog |
| 8 | Suggestion | Rename `SecretFinding.line` → `diff_line_index` (or report source-file line once #1 lands) | Backlog |
| 9 | Suggestion | Type `_build_task_prompt(task_definition: TaskDefinition)` instead of `Any` | Backlog (1-line) |

---

## Checkpoint D Gate Assessment

Plan §Checkpoint D (T9 sign-off) asks for:

- [x] All 8 agentic nodes have deep-agent variants. — Verified: T7 (3 read-only) + T8 (4 generative) + T9 (`task_node`) = 8.
- [x] Per-role instructions filled in for `implementer`. — `implementer.md` is no longer a stub; TDD methodology + sub-agent guidance + artifact contract + four explicit boundaries are present.
- [x] Diff-based secret scanner with HIGH-confidence blocking. — `secret_scanner.py` + 14 tests; integrated into `task_node` loop.
- [x] A/B harness over ≥ 5 fixture tasks. — 5 fixtures × 3 scenarios = 15 parametrized tests in `tests/integration/test_implementer_ab_harness.py`.
- [ ] Sub-agent linkage in trace. — Carries over from Checkpoint 22 Important #1; not a T9 regression but T9 inherits the same gap on the IMPLEMENTER role.

**Recommendation:** Approve the T9 commit. Important #1 (real diff) and Important #2/#4 (verification evidence + actual-test-ran proof) together raise the security and quality bar of the deep path to legacy parity; #3 (BLOCK rollback) closes the operational loop. All four are small, additive changes and should land before T14 flips `FLOWFORGE_DEEP_AGENTS=1` to default. Suggestions #1–#9 are backlog cleanups.
