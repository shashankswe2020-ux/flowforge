# Code Review Checkpoint 22: Phase 2 Deep Agent Generative Wrappers (T8)

> **Reviewer:** Code Reviewer Agent (Staff Engineer)
> **Date:** 2026-06-12
> **Scope:** Uncommitted working-tree diff vs. `HEAD` (`3596ed7`).
> Files: [flowforge/deep_agents/factory.py](flowforge/deep_agents/factory.py),
> [flowforge/deep_agents/instructions/{clarifier,spec_author,planner,triager}.md](flowforge/deep_agents/instructions/),
> [flowforge/nodes/{clarification,spec,plan,issue_orchestrator}.py](flowforge/nodes/),
> [flowforge/nodes/{code_review,security_audit,test_engineer}.py](flowforge/nodes/) (backfill),
> [tests/unit/test_node_{clarification,spec,plan,issue_orchestrator}_deep_agent.py](tests/unit/),
> [tests/contract/test_legacy_vs_deep.py](tests/contract/test_legacy_vs_deep.py).
> **Gate:** Plan §T8 acceptance criteria.
> **Verification:** `pytest tests/ -q` → **710 passed**; `mypy flowforge` → 14
> errors (pre-existing baseline, 0 new); `ruff check flowforge tests` → 130
> errors (baseline, 0 new). All re-run live for this review.

---

## Verdict: ✅ **APPROVE WITH MINOR FOLLOW-UPS**

**Counts:** Critical 0 · Important 3 · Minor 4

**Overview:** The four generative wrappers (`clarifier`, `spec_author`,
`planner`, `triager`) land cleanly behind `FLOWFORGE_DEEP_AGENTS`,
preserve legacy state-delta keys, and ship 21 new unit tests + 16 new
contract tests with full `_CASES`/`_GENERATIVE_CASES` parity. The
`invocation_sink` plumbing is correctly threaded through factory +
all seven wrappers, and `_parse_issue_items` is the right shape of
defensive parser. **Three Important issues** are flagged: a real gap
against the plan's "parent → child linkage" acceptance criterion, a
misleading prompt in `spec_author.md`, and an inconsistency in the
triager's fallback trace handling. None are merge-blocking; treat as
follow-ups before flipping the default in T14.

---

## Plan §T8 Acceptance Criteria — Audit

| Criterion | Status | Evidence |
|---|---|---|
| Four nodes have deep-agent variants behind the flag. | ✅ | `_run_via_deep_agent` exists in [clarification.py:285](flowforge/nodes/clarification.py#L285), [spec.py:432](flowforge/nodes/spec.py#L432), [plan.py:498](flowforge/nodes/plan.py#L498), [issue_orchestrator.py:629](flowforge/nodes/issue_orchestrator.py#L629); each gated on `resolve_deep_agents_enabled()`. |
| Per-role instructions filled in. | ✅ | All four `.md` files contain operational prompts (artifact contracts, sub-agent rotation, boundaries) — not stubs. |
| Each writes its `DeepAgentTrace`. | ✅ | All four return `"deep_agent_traces": {<node>: trace}` with role, `messages_digest`, `vfs_keys`, `tool_invocations`. Verified by `test_flag_on_populates_trace*`. |
| Persists VFS to workdir. | ⚠️ Partial | None of the T8 wrappers call `persist_files`. This is **deliberately consistent with T7's H1/H2 fix** (see Checkpoint 21 prior-findings table), which removed `persist_files` from the read-only wrappers. The plan text predates that audit decision. The deep paths *do* commit their structured artifacts via the existing repo helpers (`_commit_spec_to_repo`, `_commit_plan_to_repo`, `_commit_triage_to_repo`), so the artifacts reach disk through the canonical writer. **Flag for plan/spec reconciliation**, but not a code defect. |
| Sub-agent invocations recorded with parent → child linkage. | ❌ Not met | See Important #1 below. |

---

## Critical Issues

**None.**

---

## Important Issues

### 1. Sub-agent invocations are not recorded — plan acceptance criterion not met
- **Files:** [flowforge/deep_agents/factory.py:116-145](flowforge/deep_agents/factory.py#L116-L145), [flowforge/state/models.py](flowforge/state/models.py) (`ToolInvocationRecord`).
- **Problem:** Plan §T8 requires "sub-agent invocations recorded in `trace.tool_invocations` with **parent → child linkage**." The current implementation only charges the wrapped FlowForge tools (`run_tests`, `run_lint`, `git_diff`, `gh_issue_create`, `mcp_invoke`, etc. — see `_consume_tool_budget` callsites at lines 303, 313, 323, 333, 347, 367, 392, 415, 434). The `task` tool that `deepagents` exposes for sub-agent dispatch is **not wrapped** — it is registered by `create_deep_agent` directly and bypasses `_BUDGET_VAR`. So:
  - Sub-agent invocations (`researcher`, `estimator`, `dedupe_helper`, etc.) never appear in `trace.tool_invocations`.
  - `ToolInvocationRecord` has no `parent_role` / `subagent_name` field, so even if `task` were wrapped, the model lacks the fields needed for "parent → child linkage."
  - The unit tests rely on a stubbed `run_deep_agent_bounded`, so the gap is invisible: every `test_flag_on_populates_trace*` asserts `trace.tool_invocations == []` and passes.
- **Impact:** A core observability promise of T8 is unmet. Telemetry consumers cannot reconstruct which sub-agent the parent role delegated to, when, or how often. Cost tracking and the eventual T13 demo-run audit will be blind to sub-agent activity.
- **Fix sketch (small, additive):**
  ```python
  # 1) Extend the model:
  class ToolInvocationRecord(BaseModel):
      tool: str
      ok: bool = True
      parent: str | None = None        # parent agent role / name
      subagent: str | None = None      # child sub-agent name when tool == "task"
  
  # 2) Wrap the deepagents task tool the same way the FlowForge tools are wrapped:
  def _wrap_task_tool(role: AgentRole, native_task: BaseTool) -> BaseTool:
      @_lc_tool
      def task(description: str, subagent_type: str) -> str:
          """Delegate to a registered sub-agent (records parent→child linkage)."""
          _consume_tool_budget("task")  # also stamp parent/subagent on the record
          return native_task.invoke({"description": description, "subagent_type": subagent_type})
      return task
  ```
  Then update `_consume_tool_budget` to accept `parent`/`subagent` kwargs and stamp them onto the appended `ToolInvocationRecord`. Add a unit test that runs a real (small) sub-agent dispatch and asserts the linkage shows up in the trace — this is the test the contract harness is missing.
- **Priority:** Important. Block T14 (default flip) on this; not block T8 commit.

### 2. `spec_author.md` and the wrapper user-message claim a VFS path that does not exist
- **Files:** [flowforge/deep_agents/instructions/spec_author.md:32](flowforge/deep_agents/instructions/spec_author.md#L32), [flowforge/nodes/spec.py:457-465](flowforge/nodes/spec.py#L457-L465).
- **Problem:** Both the prompt ("The clarified request is available at `vfs:/context/spec.json` (when the prior pipeline state had a spec)") and the wrapper's user message ("the clarified request stored at vfs:/context/spec.json (if present)") direct the agent to read the **clarified request** from `vfs:/context/spec.json`. But `materialize_files` writes a **`SpecOutput`** (not a `ClarifiedRequest`) to that path — and only when `state.spec is not None`, which is **never true at spec-generation time** (the spec is what the agent is producing). The clarified request is *never* materialized to the VFS at all (grep `materialize_files` in [adapters.py:80-126](flowforge/deep_agents/adapters.py#L80-L126) — only `state.spec` and `state.implementation_plan` are written under `vfs:/context/`). The agent will therefore look for a file that does not exist and silently fall back to whatever it can infer from its system prompt + the freeform user message.
- **Impact:** The spec_author runs without grounded access to the clarified scope it is supposed to be specifying. Quality of the deep-path spec is degraded vs. the legacy path (which receives the clarified request inlined into the prompt by `_build_prompt`). The contract test passes because the canned VFS payload bypasses this path.
- **Fix:** Two-line change in [adapters.py](flowforge/deep_agents/adapters.py):
  ```python
  if state.clarified_request is not None:
      files["vfs:/context/clarified_request.json"] = state.clarified_request.model_dump_json()
  ```
  Then update [spec_author.md:32](flowforge/deep_agents/instructions/spec_author.md#L32) and the user message in [spec.py:464](flowforge/nodes/spec.py#L464) to point at `vfs:/context/clarified_request.json`. Same correction may apply to [planner.md:35](flowforge/deep_agents/instructions/planner.md#L35) ("The clarified spec is at `vfs:/context/spec.json`" — at plan time `state.spec` *is* set, so this is correct, but the prose "clarified spec" is a misnomer; it's just the spec).
- **Priority:** Important.

### 3. Triager fallback emits a `DeepAgentTrace` while the other three wrappers do not
- **File:** [flowforge/nodes/issue_orchestrator.py:692-707](flowforge/nodes/issue_orchestrator.py#L692-L707).
- **Problem:** When `_extract_issues` returns `None`, the triager falls back via `llm.invoke(prompt)` + `_parse_issues(content, deduped)` *and continues building the trace* with `vfs_keys`/`messages_digest` from the failed deep run. The other three T8 wrappers (`_legacy_auto_clarify`, `_legacy_spec`, `_legacy_plan`) take the opposite approach: they return without `deep_agent_traces` so the delta is indistinguishable from a flag-off run. This inconsistency means:
  - Telemetry can't tell whether triager ran the deep path or fell through.
  - A run that exhausted its tool budget and fell back will *still* persist a trace pointing at incomplete VFS content.
- **Impact:** Soft. Won't break anything; muddies observability and the rollback story for partial deep-agent failures.
- **Fix:** Pick one. Either (a) change triager fallback to return without a trace (matches the other three), or (b) update clarification/spec/plan fallbacks to *also* emit a `DeepAgentTrace` flagged with `tool_invocations` from the partial run. (a) is simpler and matches Checkpoint 21's "trace presence == deep path success" implicit contract.
- **Priority:** Important.

### 4. Defensive branches in `_parse_issue_items` are not directly tested
- **File:** [flowforge/nodes/issue_orchestrator.py:602-642](flowforge/nodes/issue_orchestrator.py#L602-L642), tests in [tests/unit/test_node_issue_orchestrator_deep_agent.py](tests/unit/test_node_issue_orchestrator_deep_agent.py).
- **Problem:** `_parse_issue_items` defends against eight invalid-row shapes:
  1. non-dict item
  2. non-str `fingerprint`
  3. unknown fingerprint (`deduped.get(fp) is None`)
  4. non-str `disposition`
  5. `IssueDisposition(raw_disp)` raises `ValueError`
  6. non-str `remediation` → coerced to `""`
  7. non-str `owner` → coerced to `None`
  8. non-str `sla_target` → coerced to `None`
  
  The current 5 unit tests in [test_node_issue_orchestrator_deep_agent.py](tests/unit/test_node_issue_orchestrator_deep_agent.py) only exercise the happy path (case where every field is well-typed). None of branches 1–8 are reached. Coverage holes here are exactly the kind of place agent output drift will silently regress.
- **Impact:** Behavioral. The defenses might already be wrong (e.g. the function silently drops a row with unknown fingerprint *and* with a typo'd disposition — the caller can't distinguish "agent emitted nothing" from "agent emitted garbage"). Without tests, refactors will erode them.
- **Fix:** Add one parametrized test feeding an invalid-row matrix into `_parse_issue_items` directly:
  ```python
  @pytest.mark.parametrize(
      ("bad_item", "reason"),
      [
          ("not-a-dict", "non-dict item"),
          ({"fingerprint": 123, "disposition": "must_fix_before_ship"}, "non-str fingerprint"),
          ({"fingerprint": "unknown", "disposition": "must_fix_before_ship"}, "unknown fingerprint"),
          ({"fingerprint": "<valid>", "disposition": 123}, "non-str disposition"),
          ({"fingerprint": "<valid>", "disposition": "wat"}, "invalid enum value"),
      ],
  )
  def test_parse_issue_items_drops_invalid_rows(...): ...
  ```
  Plus one positive case asserting non-str `remediation`/`owner`/`sla_target` are coerced to defaults.
- **Priority:** Important (small fix).

---

## Suggestions (Minor)

### 1. `clarified_request.json` breaks the `_output` suffix convention
- **File:** [flowforge/nodes/clarification.py:265](flowforge/nodes/clarification.py#L265) (`_CLARIFIED_VFS_PATH = "vfs:/context/clarified_request.json"`).
- The other three wrappers use `_output` to disambiguate from inputs (`spec_output.json`, `plan_output.json`, `issues_output.json`). Clarifier writes `clarified_request.json`. There's no current collision (`materialize_files` doesn't write a clarified request anywhere — see Important #2), but the naming inconsistency is a foot-gun for future readers. Rename to `clarified_request_output.json` or, once Important #2 is fixed and the input path becomes `clarified_request.json`, follow the same `_output` rule.

### 2. `_legacy_*` helpers duplicate fence-stripping logic
- **Files:** [clarification.py:430-445](flowforge/nodes/clarification.py#L430), [spec.py:524-535](flowforge/nodes/spec.py#L524), [plan.py:580-591](flowforge/nodes/plan.py#L580).
- The three new `_legacy_*` fallbacks each re-implement the markdown-fence stripping that already exists in their non-`_legacy` callers. Hoist a `_strip_json_fences(content: str) -> str` helper into a shared module or the relevant file's top-level. Cosmetic.

### 3. Spec `_run_via_deep_agent` ignores the new `_legacy_spec` fallback when `_extract_spec` succeeds with a partial payload
- **File:** [flowforge/nodes/spec.py:495](flowforge/nodes/spec.py#L495).
- `_extract_spec` returns a `SpecOutput` even when most fields default to empty strings/lists — there's no minimum-quality gate. A near-empty agent emission will be accepted and committed without falling back. Consider validating "non-empty `acceptance_criteria` AND non-empty `objective`" before returning, falling back otherwise. Same applies to `_extract_plan` (which does check `"tasks" in parsed`, so partial guard exists) and `_extract_clarified_request`.

### 4. No malformed-VFS test for spec / plan / orchestrator (only clarification has one)
- **Files:** [test_node_clarification_deep_agent.py:185](tests/unit/test_node_clarification_deep_agent.py#L185) (`test_malformed_vfs_falls_back_to_legacy`) is the model — replicate it in the spec / plan / orchestrator deep-agent test files. Without it, the "fallback contract" the user asked about is only verified for one of four wrappers.

---

## What's Done Well

- **`invocation_sink` plumbing is exactly right.** The `ContextVar` stays internal; the wrapper passes a list and gets it populated on both success and exception paths via `executor.shutdown(wait=False, cancel_futures=True)` + `if invocation_sink is not None: invocation_sink.extend(budget.invocations)` in the `finally` block. Lifecycle is clean: ContextVar reset *after* sink is filled, so a future re-entrant call cannot see stale state. ([factory.py:217-225](flowforge/deep_agents/factory.py#L217-L225))
- **`_run_via_deep_agent` dispatch contract is identical across all four wrappers** — same payload shape (`{messages: [...], files: materialize_files(state)}`), same `node_name=` argument matching the LangGraph node identifier, same trace-construction code. Symmetry will pay off at T9/T14.
- **Source-node attribution is preserved exactly where it matters.** The triager builds `Issue` objects via `finding.source_node` (server-side from `deduped`), not from agent input ([issue_orchestrator.py:625](flowforge/nodes/issue_orchestrator.py#L625)). Agent spoofing is structurally impossible. The other three generative artifacts (`ClarifiedRequest`, `SpecOutput`, `ImplementationPlan`) have no `source_node` field, so the I2 audit concern simply doesn't apply.
- **Auto-clarify gate is correct.** `state.auto_clarify and not state.clarified_request and resolve_deep_agents_enabled()` ([clarification.py:218-223](flowforge/nodes/clarification.py#L218-L223)) leaves interactive clarification on the legacy path. `test_auto_clarify_false_bypasses_deep_path` proves it. This is the right call for T8.
- **Contract-test extension via `GenerativeCase` dataclass** ([test_legacy_vs_deep.py:521-538](tests/contract/test_legacy_vs_deep.py#L521-L538)) is a clean re-use of the T7 `ContractCase` pattern. New cases cost ~30 lines each. The `primary_key` + `assert_keys` decoupling lets generative artifacts (which don't have `Finding`) and Finding-shaped artifacts share the same harness.
- **Fallback re-uses the legacy implementation, not a separate code path.** `_legacy_auto_clarify`, `_legacy_spec`, `_legacy_plan` are extracted from the original node bodies — so a deep-path failure produces *exactly* the legacy output, byte-for-byte. No drift. (Triager is the exception — see Important #3.)
- **Triager prompt explicitly forbids fingerprint invention** ("Use the exact fingerprints supplied in the user prompt; do not invent new ones") and the parser belt-and-braces this with `deduped.get(fp) is None: continue` — defense in depth.
- **Backfill of `invocation_sink` on the read-only wrappers** ([code_review.py:451](flowforge/nodes/code_review.py#L451), [security_audit.py:451](flowforge/nodes/security_audit.py#L451), [test_engineer.py:490](flowforge/nodes/test_engineer.py#L490)) keeps T7 + T8 traces on the same shape. Future telemetry consumers see uniform `tool_invocations` across all seven wrappers.

---

## Verification Story

| Check | Status | Notes |
|-------|--------|-------|
| Tests reviewed | ✅ | All four T8 unit files (21 tests) + 16 new contract tests read end-to-end. |
| Tests pass | ✅ | `pytest tests/ -q` → 710 passed (vs. 673 baseline; +37 = +21 unit + +16 contract). |
| mypy | ✅ | 14 errors, all pre-existing baseline (verified by user against stash). 0 new from T8. |
| ruff | ✅ | 130 errors, baseline. 0 new from T8 (per user, after fixing one I001 in contract tests). |
| Build | ✅ | Implicit via mypy + tests. No build step in this Python repo. |
| Security | ✅ | No new shell-out paths; `materialize_files` rejects path-escape (PathTraversalError); no new tool added to any role allowlist. Wrappers force `source_node` server-side where applicable (triager). |
| Spec §T8 acceptance | ⚠️ Partial | 4 of 5 criteria met; sub-agent linkage gap (Important #1). |
| Prior findings (Checkpoint 21) | ✅ | Trace shape + `source_node` enforcement carried forward consistently in T8. |

---

## Action Items

| # | Priority | Issue | Target |
|---|----------|-------|--------|
| 1 | Important | Wrap `deepagents.task` tool + add `parent`/`subagent` fields to `ToolInvocationRecord` so sub-agent linkage is recorded | Pre-T14 (block default flip) |
| 2 | Important | Materialize `clarified_request` to VFS + fix `spec_author.md` + spec wrapper user message | Pre-T9 (planner already correct, but fix wording) |
| 3 | Important | Reconcile triager fallback's `deep_agent_traces` emission with the other three wrappers | Backlog |
| 4 | Important | Add direct unit tests for `_parse_issue_items` invalid-row drops | Backlog |
| 5 | Suggestion | Rename `clarified_request.json` → `clarified_request_output.json` for naming consistency | Backlog |
| 6 | Suggestion | Hoist `_strip_json_fences` helper to deduplicate `_legacy_*` fallbacks | Backlog |
| 7 | Suggestion | Add minimum-quality gate to `_extract_spec` / `_extract_clarified_request` before accepting | Backlog |
| 8 | Suggestion | Add `test_malformed_vfs_falls_back_to_legacy` to the spec / plan / orchestrator deep-agent test files | Backlog (T8 follow-up) |

---

## Checkpoint C Gate Assessment

Plan §Checkpoint C asks for:

- [x] 7 of 8 agentic nodes have deep-agent variants. — Verified: T7's three read-only + T8's four generative = 7. `task_node` remains legacy-only per T9 scope.
- [ ] Demo run with `--use-deep-agents` reaches `quality_gate_merge` successfully. — **Not verified** in this review (out of scope; T13 owns this gate).
- [ ] `code-reviewer` re-review focusing on the new wrappers. — **This document.**

**Recommendation:** Approve the T8 commit. Important #1 (sub-agent linkage) and Important #2 (clarified-request materialization) should be tracked and resolved before T14 flips the default — both are small additive changes. Important #3 and #4 are observability / test-coverage cleanups for the same window.
