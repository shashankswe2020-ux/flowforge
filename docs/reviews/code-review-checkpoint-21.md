# Code Review Checkpoint 21: Phase 1 Deep Agent Read-Only Wrappers (T7 + T12)

> **Reviewer:** Code Reviewer Agent (Staff Engineer)
> **Date:** 2026-06-11
> **Scope:** Commits `5c10550` (T7 — read-only wrappers) and `09d710b` (T12 — contract tests).
> Files: `flowforge/nodes/{code_review,security_audit,test_engineer}.py`,
> `flowforge/deep_agents/instructions/{reviewer,auditor,tester}.md`,
> `tests/unit/test_node_{review,security,test_engineer}_deep_agent.py`,
> `tests/contract/test_legacy_vs_deep.py`, `tests/conftest.py`.
> **Gate:** Plan §Checkpoint B — Phase 1 complete.
> **Test suite:** Per the user request, the full suite was not re-run for this
> review. Targeted runs of the in-scope unit + contract files were verified
> green earlier in the day (per session record).

---

## Verdict: ✅ APPROVE WITH COMMENTS

**Overview:** The three read-only wrappers (`reviewer`, `auditor`, `tester`)
land cleanly behind `FLOWFORGE_DEEP_AGENTS`, preserve legacy state-delta keys,
populate `deep_agent_traces`, and have a thorough contract harness in
[tests/contract/test_legacy_vs_deep.py](tests/contract/test_legacy_vs_deep.py).
All previously-flagged Checkpoint B audit findings (C1, H1/H2, I2, M1, M2) are
present and verified in the diff. No new Critical findings; the items below
are either Important architectural cleanups or Minor polish.

---

## Prior-Findings Acknowledgement

| ID | Description | Status |
|----|-------------|--------|
| **C1** | `KeyError` on `metadata['verdict']` in deep code-review path | ✅ Fixed — [flowforge/nodes/code_review.py](flowforge/nodes/code_review.py#L470-L475) sets `verdict`, `summary`, `done_well`; regression test [tests/unit/test_node_review_deep_agent.py](tests/unit/test_node_review_deep_agent.py#L218-L259) (`TestDeepPathRendersMarkdown`) lets the real renderer run. |
| **H1/H2** | `persist_files` removed from all three wrappers | ✅ Fixed — `grep -n persist_files flowforge/nodes/{code_review,security_audit,test_engineer}.py` is empty; agents no longer write into the workdir. |
| **I2** | `source_node` forced via `model_copy` | ✅ Fixed — [code_review.py:451](flowforge/nodes/code_review.py#L451-L454), [security_audit.py:417](flowforge/nodes/security_audit.py#L417-L420), [test_engineer.py:443](flowforge/nodes/test_engineer.py#L443-L446). |
| **M1** | `task_id` collision guard in `_extract_proposed_tasks` | ✅ Fixed — [test_engineer.py:497](flowforge/nodes/test_engineer.py#L497-L499) seeds `seen` from `existing_task_ids` and skips collisions; caller passes `{t.task_id for t in state.tasks}`. |
| **M2** | `acceptance_checks` coerced to `list[str]` only | ✅ Fixed — [test_engineer.py:512](flowforge/nodes/test_engineer.py#L512-L515) drops non-string elements. |

---

## Critical Issues

**None.**

---

## Important Issues

### 1. Agent's markdown output is silently discarded
- **Files:** [flowforge/nodes/code_review.py:476](flowforge/nodes/code_review.py#L476-L482),
  [flowforge/nodes/security_audit.py:435](flowforge/nodes/security_audit.py#L435),
  [flowforge/nodes/test_engineer.py:474](flowforge/nodes/test_engineer.py#L474-L482).
- **Problem:** All three role prompts ([reviewer.md](flowforge/deep_agents/instructions/reviewer.md), [auditor.md](flowforge/deep_agents/instructions/auditor.md), [tester.md](flowforge/deep_agents/instructions/tester.md)) instruct the agent to emit a markdown report (e.g. `vfs:/docs/reviews/code-review.md`). The wrappers extract `findings` from `vfs:/findings/*.json` but ignore the markdown — `_commit_*_to_repo` re-renders from the structured findings using a hardcoded metadata stub (`done_well=[]`, `positive_observations=[]`, `summary="Deep-agent review run."`). The agent's narrative reasoning (axis breakdown, OWASP mapping, what's done well) is wasted work.
- **Impact:** Markdown reports under the deep-agent path are objectively *less* informative than the legacy path's, even though the agent did extra work to produce them.
- **Fix:** Either (a) prefer the agent's `vfs:/docs/<dir>/<file>.md` when present and only fall back to the structured renderer, or (b) extract `summary` / `done_well` / `positive_observations` from the agent's JSON and pass them through. Option (a) is simpler:
  ```python
  agent_md = (raw_files or {}).get("vfs:/docs/reviews/code-review.md")
  if isinstance(agent_md, str) and agent_md.strip():
      _write_markdown_directly(agent_md, state)
  else:
      _commit_review_to_repo(findings, metadata, state)
  ```
  Track as a follow-up; not merge-blocking because shape parity (the contract gate) still holds.

### 2. `verdict` derived from finding *count*, not severity
- **File:** [flowforge/nodes/code_review.py:470](flowforge/nodes/code_review.py#L470-L475).
- **Problem:** `"verdict": "request_changes" if findings else "approve"`. Any single `info` or `low` finding flips the deep-agent verdict to `request_changes`, while the legacy path lets the LLM decide. This is a *behavioral* divergence not caught by the contract tests (which assert finding count/shape but not verdict text).
- **Fix:** Treat severity as the gate:
  ```python
  blocking = {IssueSeverity.CRITICAL, IssueSeverity.HIGH}
  verdict = "request_changes" if any(f.severity in blocking for f in findings) else "approve"
  ```
  Or have the agent write a sibling `vfs:/findings/review-verdict.json` and read it.

### 3. Malformed agent JSON crashes the node with no fallback
- **File:** [flowforge/deep_agents/adapters.py:174](flowforge/deep_agents/adapters.py#L174-L209) (called from each wrapper).
- **Problem:** `extract_findings` raises `ValueError` on malformed JSON or non-list payloads. The deep-path wrappers call it without a try/except, so a flaky agent emission turns a budget-bounded run into a hard node failure *after* tool budget has already been spent. The legacy path can also raise `JSONDecodeError`, but it has only one decode site; the deep path has N findings files plus a `proposed_tasks.json` decode in `_extract_proposed_tasks` (which *does* swallow errors — [test_engineer.py:486](flowforge/nodes/test_engineer.py#L486-L488)). Inconsistent.
- **Fix:** Either wrap `extract_findings` in a typed boundary at the wrapper layer (returning `[]` + emitting a `tool.failed` telemetry event), or change `extract_findings` to skip malformed entries and log. Aligns with deferred backlog item S2 ("typed boundary on bounded-run errors") — bumping severity here because the partial fault tolerance in `_extract_proposed_tasks` shows the asymmetry is unintentional.

### 4. Autouse `chdir` fixture has global blast radius
- **File:** [tests/conftest.py:14](tests/conftest.py#L14-L29).
- **Problem:** `_isolated_workdir` is `autouse=True` and `monkeypatch.chdir(tmp_path)` for *every* test in the suite, not only the deep-agent / commit-helper tests that motivated it. Any future test that legitimately depends on `Path.cwd()` resolving to the repo root (e.g. resolving relative config paths, walking `flowforge/deep_agents/instructions/`) will silently break or, worse, accidentally pass against a stale tmp tree.
- **Fix:** Scope the fixture: either keep `autouse` but only apply it to a marker (`pytest.mark.workdir_isolated`), or move it to `tests/unit/conftest.py` next to the wrappers it protects. At minimum, leave a `# DO NOT REMOVE` comment explaining which tests would pollute the repo without it.

---

## Suggestions (Minor)

### 1. Redundant `from flowforge.nodes._workspace import get_workdir` inside helpers
- **Files:** [code_review.py:288, 322](flowforge/nodes/code_review.py#L288),
  [security_audit.py:286, 314](flowforge/nodes/security_audit.py#L286),
  [test_engineer.py:331, 358](flowforge/nodes/test_engineer.py#L331).
- The same symbol is imported at module top *and* re-imported inside three helpers. Pre-existing pattern but the new wrappers carried it forward — drop the inner imports to reduce noise.

### 2. `_extract_proposed_tasks` defined after its only caller
- **File:** [flowforge/nodes/test_engineer.py:483](flowforge/nodes/test_engineer.py#L483-L532).
- Forward references work in Python but readers expect helpers above their callers. Move it above `_run_via_deep_agent` (or before `test_engineer_node`) for top-down flow.

### 3. Hardcoded prompt strings in wrappers should live next to instructions
- **Files:** the literal user-message strings in all three `_run_via_deep_agent` payloads (e.g. [code_review.py:430-437](flowforge/nodes/code_review.py#L430-L437)).
- These instruct the agent which VFS paths to write. They are effectively *part of the role contract* but are duplicated across the wrapper and the `.md` instructions. If the canonical paths ever change, both must be edited in lockstep. Consider defining them as constants on the role (e.g. `AgentRole.REVIEWER.findings_path`) so the wrapper references the single source of truth.

### 4. `_render_review_markdown`/`_render_audit_markdown` mix `\n` in f-strings with `"\n".join`
- **File:** [code_review.py:208-213](flowforge/nodes/code_review.py#L208-L213) (and the audit equivalent).
- `lines.append(f"# Code Review\n")` followed by `"\n".join(lines)` yields a blank line after every header. Pre-existing pattern, not introduced here, but worth noting alongside the deep-path render touch-up (Important #1).

### 5. Test data duplicated across three unit files and the contract harness
- **Files:** the `_state(workdir)` factory and the canned VFS dicts are near-identical in [test_node_review_deep_agent.py](tests/unit/test_node_review_deep_agent.py#L33-L88), [test_node_security_deep_agent.py](tests/unit/test_node_security_deep_agent.py#L28-L85), [test_node_test_engineer_deep_agent.py](tests/unit/test_node_test_engineer_deep_agent.py#L28-L93), and [tests/contract/test_legacy_vs_deep.py](tests/contract/test_legacy_vs_deep.py#L52-L82).
- Hoist `_state` into `tests/factories.py` (which already exists). The canned VFS payloads can stay co-located with their tests — duplication there documents intent.

### 6. Contract harness asserts shape but not severity equivalence
- **File:** [tests/contract/test_legacy_vs_deep.py](tests/contract/test_legacy_vs_deep.py).
- The harness checks finding *counts* and `source_node`, which is what spec §11.3 requires ("severity counts in expected ranges"). It does not yet assert that the *severity histogram* is identical or within a band. This is fine for the read-only nodes where the canned response is identical, but when T8/T9 add live-LLM contract cases the band check will be needed. Flag for the T8 reviewer; not in scope here.

---

## What's Done Well

- **C1 regression test is the right shape.** [test_node_review_deep_agent.py::TestDeepPathRendersMarkdown](tests/unit/test_node_review_deep_agent.py#L208-L259) lets the *real* `_commit_review_to_repo` execute against a tmp workdir instead of stubbing it — exactly the kind of test that would have caught the original `KeyError`. Good Prove-It hygiene.
- **`_extract_proposed_tasks` is impressively defensive** — handles missing/extra fields, casts complexity to a known set, falls back to `CapabilityType.AGENT_ONLY` on enum mismatch, dedupes against existing tasks, and skips bad items individually rather than aborting the whole list. This is the gold standard for parsing agent output ([test_engineer.py:483-532](flowforge/nodes/test_engineer.py#L483-L532)).
- **Contract harness uses a parameterized `ContractCase` dataclass** ([test_legacy_vs_deep.py:241-289](tests/contract/test_legacy_vs_deep.py#L241-L289)) that T8 and T9 can extend by appending tuples — extension cost is one entry per node. Good API design for a test fixture.
- **Trace shape is uniform across all three wrappers**: `role`, `messages_digest` via `DeepAgentTrace.digest_messages`, sorted `vfs_keys`. Makes downstream telemetry trivial.
- **Role prompts are tight and operational**, not aspirational essays — each one tells the agent exactly what to read, what sub-agents to invoke, what to write, and what *not* to do (no artifact mutation). [reviewer.md](flowforge/deep_agents/instructions/reviewer.md) and [auditor.md](flowforge/deep_agents/instructions/auditor.md) in particular are the right length.
- **Read-only invariant is enforced by absence**: no `persist_files` import, no `WriteFile` tool exposure to the role allowlist (per factory). That's the right way to encode "this role cannot mutate the workdir" — at the boundary, not via runtime check.

---

## Verification Story

| Check | Status | Notes |
|-------|--------|-------|
| Tests reviewed | ✅ | All four test files (3 unit + 1 contract) read end-to-end. Coverage maps cleanly to spec §11.2 + §11.3. |
| Build / typecheck / lint | ⚠️ Skipped per request | Last verified green in the session log earlier today. |
| Security checked | ✅ | No new shell-out paths; agent VFS no longer mirrored to disk; gh/git side-effects still cwd-confined to `state.workdir`. |
| Spec §11.3 contract | ✅ | Three nodes covered; `test_review_contract` named entry point present per T7's verification list. |
| Spec §10 budgets | ⚠️ Indirect | Wrappers use `run_deep_agent_bounded` which carries the budget; no test in this batch asserts budget enforcement, but T10 owns that surface. |
| Prior findings closed | ✅ | C1, H1/H2, I2, M1, M2 all verified present in the diff (table above). |

---

## Action Items

| # | Priority | Issue | Target |
|---|----------|-------|--------|
| 1 | Important | Surface agent's markdown when present; don't re-render | Backlog (pre-Checkpoint C, ideally) |
| 2 | Important | Verdict from severity, not count | Backlog (paired with T13 demo run) |
| 3 | Important | Wrap `extract_findings` in typed boundary (overlaps backlog S2) | Backlog (T10 follow-up) |
| 4 | Important | Scope autouse `_isolated_workdir` to a marker or sub-conftest | Backlog |
| 5 | Suggestion | Drop redundant `_workspace` re-imports | Backlog |
| 6 | Suggestion | Move `_extract_proposed_tasks` above its caller | Backlog |
| 7 | Suggestion | Consolidate canonical VFS paths onto `AgentRole` | Backlog (T8 prep) |
| 8 | Suggestion | Hoist `_state` factory into `tests/factories.py` | Backlog |
| 9 | Suggestion | Extend contract harness with severity histogram assertion | T8 reviewer |

---

## Checkpoint B Gate Assessment

Plan §Checkpoint B asks for:

- [x] All three review-side wrappers behind the flag; legacy default still works. — Verified by `test_flag_off_uses_legacy_path` in each unit file.
- [x] Contract tests green for the 3 read-only nodes. — [test_legacy_vs_deep.py](tests/contract/test_legacy_vs_deep.py) ships 3 cases plus a named `test_review_contract` entry point.
- [x] `docs/reviews/` entry written. — This document.
- [ ] `docs/security-audits/` entry written. — Out of scope for this review; flag for the auditor agent.

**Recommendation:** Declare Checkpoint B met once the security-audit checkpoint document is added. The Important findings above are *follow-ups*, not gate blockers.
