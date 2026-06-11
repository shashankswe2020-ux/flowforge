# Security Audit Report #18 — T9 Implementer Deep Agent + Secret Scanner

> **Auditor:** Security Auditor Agent
> **Date:** 2026-06-12
> **Branch / commits:** working tree vs HEAD `f9f6df3` (T9 post-review-23 fixes)
> **Scope:**
> - `flowforge/deep_agents/secret_scanner.py` — diff-based regex (HIGH) +
>   entropy (MEDIUM) detection.
> - `flowforge/nodes/task_runner.py` — `_run_via_deep_agent`,
>   `_scan_files_for_secrets`, `_extract_verification_evidence`, BLOCK
>   semantics, partial commit on block.
> - `flowforge/deep_agents/instructions/implementer.md` — agent system
>   prompt.
> - `tests/deep_agents/test_secret_scanner.py`,
>   `tests/unit/test_node_task_deep_agent.py`,
>   `tests/integration/test_implementer_ab_harness.py`,
>   `tests/contract/test_legacy_vs_deep.py`.
> **Methodology:** 5-dimension audit per
> `.github/skills/security-and-hardening/SKILL.md`, OWASP Top 10 baseline,
> threat-model questions from the request: bypass paths, workdir escape,
> state exposure, symlink races, trace leakage, partial-commit semantics,
> fallback path.
> **External calls:** none performed (no `pip-audit`, no network).
> **Note on numbering:** This is the 18th audit; the prior file
> `security-audit-17.md` is preserved unchanged.

---

## Executive Summary

**Overall risk rating: Medium — one Important finding gates merge.**

The T9 wrapper makes meaningful progress on the threat model: the secret
scan now operates on a true `unified_diff(on_disk, agent_emitted)` (so
pre-existing token-shaped strings on disk no longer false-positive),
BLOCK fires **before** `persist_files`, partial progress is committed
deterministically, and the verification-evidence sentinel file is
consumed in-memory. Tests cover all four guarantees.

The audit confirms those properties hold. However, **`_scan_files_for_secrets`
joins the agent-controlled path under `workdir` and reads it without the
`_safe_resolve` check that `persist_files` enforces** ([task_runner.py:200–204](flowforge/nodes/task_runner.py#L200-L204)).
An agent that emits `vfs:/../../etc/passwd` (or any traversal key) causes
the wrapper to call `read_text()` on a host-system path before the
traversal check downstream rejects the write. No content is exfiltrated
through the scan output (the diff scanner only inspects `+` lines, which
come from agent-supplied content), but the read primitive itself is a
sandbox-escape: it can be steered onto `/proc/self/environ`, FIFOs,
`/dev/zero`, or large logs to crash the run via `UnicodeDecodeError`,
hang on a special device, or oracle the existence of host paths via
timing. This must be fixed before merge.

Two further bypass classes are documented as Important / Minor: the
HIGH-severity regex catalogue misses several modern token shapes
(`sk-proj-…` OpenAI project keys, `github_pat_…` fine-grained PATs,
`ghs_/gho_/ghu_/ghr_` GitHub server tokens), and the legacy fallback
path executes without any scanner whatsoever.

| Severity  | Count |
|-----------|-------|
| Critical  | 0 |
| Important | 2 |
| Minor     | 4 |
| Info      | 4 |

---

## Confirmation of Prior-Round Fixes (review-23)

| Property | Status | Evidence |
|----------|--------|----------|
| Diff is computed against on-disk content, not the seed VFS | **Effective** | [task_runner.py:191–209](flowforge/nodes/task_runner.py#L191-L209) — `old_content = target.read_text(...) if target.exists() else ""`; `difflib.unified_diff(old_content.splitlines(), new_content.splitlines(), …)`. Test: `TestDiffVsDisk.test_unchanged_lines_in_existing_file_are_not_flagged` ([test_node_task_deep_agent.py:283–306](tests/unit/test_node_task_deep_agent.py#L283-L306)). |
| HIGH finding blocks **before** `persist_files`; offending file never lands on disk | **Effective** | Order at [task_runner.py:329–356](flowforge/nodes/task_runner.py#L329-L356): scan → if block → return; persist only on the no-block branch at [task_runner.py:358–360](flowforge/nodes/task_runner.py#L358-L360). Test: `TestSecretNotPersisted.test_blocked_artifact_never_touches_disk` ([test_node_task_deep_agent.py:381–396](tests/unit/test_node_task_deep_agent.py#L381-L396)). |
| `vfs:/context/implementer_output.json` consumed in-memory; sentinel namespace not persisted | **Effective** | [task_runner.py:228–251](flowforge/nodes/task_runner.py#L228-L251) reads the key directly from `result["files"]`; `persist_files` skips `context/` ([adapters.py:60–63, 162–165](flowforge/deep_agents/adapters.py#L60-L63)). Test: `TestVerificationEvidence.test_evidence_is_extracted_from_summary_file`. |
| Partial commit on block of task N covers tasks 1..N-1 | **Effective** | [task_runner.py:347–349](flowforge/nodes/task_runner.py#L347-L349) — `if written_paths: _commit_artifacts(workdir, written_paths)` runs in the BLOCK branch. `written_paths` is appended only after a successful `persist_files` per task ([task_runner.py:358–361](flowforge/nodes/task_runner.py#L358-L361)). Test: `TestPartialCommitOnBlock.test_prior_task_files_are_committed_before_block`. |

All four review-23 fixes verified.

---

## Findings by Dimension

### 1. Input Handling

#### [IMPORTANT-1] `_scan_files_for_secrets` reads agent-controlled path without `_safe_resolve`

- **Location:** [flowforge/nodes/task_runner.py:200–204](flowforge/nodes/task_runner.py#L200-L204)
- **OWASP:** A01 (Broken Access Control), A04 (Insecure Design)
- **Description:** `persist_files` enforces a workdir-rooted resolve via
  `_safe_resolve` ([adapters.py:65–77](flowforge/deep_agents/adapters.py#L65-L77)), rejecting absolute paths and `..`-traversal.
  `_scan_files_for_secrets` runs *before* `persist_files` and joins the
  agent-controlled key to the workdir without that check:

  ```python
  rel = raw_path[len("vfs:/"):]
  if rel.startswith(("findings/", "context/", "subagent/")):
      continue
  target = workdir / rel
  old_content = target.read_text(encoding="utf-8") if target.exists() else ""
  ```

  `Path("/abs/wd") / "../../etc/passwd"` does not normalise; `.exists()`
  and `.read_text()` then operate on the resolved target outside the
  workdir.
- **Impact:** The agent can steer a single `read_text(encoding="utf-8")`
  call onto an arbitrary host-readable path per emitted VFS entry per
  task. Concrete consequences:
  1. **DoS / crash.** Reading a binary file (e.g. `/bin/ls`,
     `~/.ssh/id_ed25519` after suid-style locking is bypassed) raises
     `UnicodeDecodeError`, which is **not caught** in
     `_run_via_deep_agent` — the entire run aborts. An attacker who
     controls the LLM output can crash any T9 run on demand by pointing
     at any non-UTF-8 host file.
  2. **Hang.** Reading a FIFO / character device (`/dev/random` blocks
     until enough entropy is available; FIFOs without writers block
     indefinitely) wedges the wrapper.
  3. **Memory / disk exhaustion.** No size limit on `read_text`. Large
     files (`/var/log/syslog`, multi-GB cache files) get loaded into a
     single Python `str` and then handed to `splitlines()` and
     `unified_diff` — both materialise full lists.
  4. **Path-existence oracle.** `target.exists()` differentiates present
     vs absent paths via observable behaviour (read happens or doesn't,
     scanner timing differs). An adversarial LLM could probe host
     filesystem layout one path per task.
  5. **No content exfiltration via scanner output** — `scan_diff` only
     inspects `+` lines (agent-supplied), so `old_content` does not
     surface in `SecretFinding.snippet`. This bounds the impact to
     side-channel reads and DoS, not direct read-up.
- **Proof of concept:**
  ```python
  # Agent emits in a task:
  result["files"] = {"vfs:/../../etc/passwd": ""}
  # _scan_files_for_secrets:
  #   target = workdir / "../../etc/passwd"
  #   target.exists()  → True (on a Linux host)
  #   target.read_text(encoding="utf-8")  → UnicodeDecodeError raises
  #     for many system files; for /etc/passwd it succeeds, content
  #     is then handed to unified_diff and discarded.
  # persist_files later raises PathTraversalError, but the read
  # already happened.
  ```
- **Recommendation:** Route the path through `_safe_resolve` before
  reading, mirroring `persist_files`. Skip the entry on
  `PathTraversalError` (and surface a finding so the run can record the
  attempt). Cap `read_text` size and catch `UnicodeDecodeError` /
  `OSError` to fall back to an empty `old_content`.

  ```python
  from flowforge.deep_agents.adapters import PathTraversalError, _safe_resolve

  _MAX_DIFF_SOURCE_BYTES = 1 * 1024 * 1024  # 1 MiB

  try:
      target = _safe_resolve(workdir, rel)
  except PathTraversalError:
      findings.append(SecretFinding(
          pattern_name="path_traversal",
          severity=SecretSeverity.HIGH,
          line=0,
          snippet=raw_path[:120],
      ))
      continue
  if target.exists():
      try:
          old_content = target.read_text(
              encoding="utf-8", errors="replace",
          )[:_MAX_DIFF_SOURCE_BYTES]
      except OSError:
          old_content = ""
  else:
      old_content = ""
  ```

  Treating a traversal attempt as a HIGH finding also lets the BLOCK
  semantics absorb the case naturally — the run halts the same way it
  does for a planted credential, and the evidence is recorded.
- **Severity rationale:** Important — the persist-path traversal guard
  is intact, but a sibling read primitive bypasses it. No confidentiality
  loss in the current code path, but reachable DoS / oracle and a clear
  invariant violation that future refactors could escalate (e.g. anyone
  who later logs `old_content` for debug purposes converts this into a
  read-up).

#### [IMPORTANT-2] HIGH-confidence regex catalogue misses modern token shapes

- **Location:** [flowforge/deep_agents/secret_scanner.py:50–58](flowforge/deep_agents/secret_scanner.py#L50-L58)
- **OWASP:** A04 (Insecure Design), A09 (Security Logging and Monitoring Failures)
- **Description:** `_HIGH_PATTERNS` covers the 2018-era token formats:
  - `AKIA[0-9A-Z]{16}` — AWS access key id (still current).
  - `ghp_[A-Za-z0-9]{36}` — GitHub *classic* personal access token only.
  - `sk-[A-Za-z0-9]{20,}` — OpenAI key, but `[A-Za-z0-9]` excludes `-`.
  - `xox[baprs]-[A-Za-z0-9-]{10,}` — Slack.
  - `-----BEGIN (?:RSA |EC |DSA |OPENSSH |)PRIVATE KEY-----` — PEM.

  Notable misses:
  - **OpenAI project keys** (`sk-proj-…`) — the `-` after `proj` breaks
    `[A-Za-z0-9]{20,}` immediately; a real `sk-proj-AbCd1234…` does
    **not** match. These have been the dominant OpenAI key shape since
    mid-2024.
  - **GitHub fine-grained PATs** (`github_pat_<22>_<59>`) — entirely
    unmatched.
  - **GitHub server-to-server / OAuth / refresh tokens** — `ghs_`,
    `gho_`, `ghu_`, `ghr_` prefixes — entirely unmatched.
  - **AWS session tokens** (long-form base64) — would only trip the
    MEDIUM entropy heuristic, which does **not** block.
  - **Google API keys** (`AIza[0-9A-Za-z\-_]{35}`), Stripe (`sk_live_`,
    `rk_live_`), Twilio (`SK[0-9a-f]{32}`), generic JWT (`eyJ…`).
- **Impact:** The scanner advertises a "blocks the run on a
  high-confidence credential pattern" guarantee in
  [implementer.md:62–67](flowforge/deep_agents/instructions/implementer.md#L62-L67),
  but an agent emitting a real `sk-proj-…` or `github_pat_…` value will
  pass the gate and the secret will be persisted to disk and committed
  by `_commit_artifacts`. The downstream `security_audit_node` may
  catch it later, but at that point the secret is already in the git
  history and any push has already exposed it.
- **Recommendation:** Extend `_HIGH_PATTERNS` to cover the currently
  active token shapes:

  ```python
  _HIGH_PATTERNS = (
      ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
      ("github_classic_pat", re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
      ("github_fine_grained_pat",
       re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
      ("github_oauth", re.compile(r"\bgh[osur]_[A-Za-z0-9]{36}\b")),
      ("openai_classic", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
      ("openai_project",
       re.compile(r"\bsk-(?:proj|admin|svcacct)-[A-Za-z0-9_\-]{20,}\b")),
      ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
      ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
      ("stripe_live", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{20,}\b")),
      ("private_key_header",
       re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |)PRIVATE KEY-----")),
  )
  ```

  Add a regression test per pattern in
  `tests/deep_agents/test_secret_scanner.py::TestScanDiffHighConfidence`
  using **synthetic** values (never real keys; e.g. `sk-proj-` followed
  by a literal `"A" * 40`) so the test corpus does not trigger
  third-party scanners on this repository.
- **Severity rationale:** Important — the gate exists for the express
  purpose of catching these formats; missing the most common active
  formats turns the guarantee in the prompt into a half-truth.

### 2. Authentication & Authorization

No findings. The wrapper does not handle credentials directly; `git`
inherits identity through the parent process env. The scanner is the
authorisation boundary for "may this artifact land on disk" and the
authority decision is correctly placed before `persist_files`.

### 3. Data Protection

#### [MINOR-1] Line-splitting / concatenation evades regex by construction

- **Location:** [flowforge/deep_agents/secret_scanner.py:75–104](flowforge/deep_agents/secret_scanner.py#L75-L104)
- **Description:** `scan_diff` matches each `+` line independently. An
  agent that splits a credential across two lines —
  `KEY = ("AKIA"\n+    "IOSFODNN7EXAMPLE")` — or that builds it via
  concatenation —
  `KEY = "AKIA" + "IOSFODNN7EXAMPLE"` — escapes every regex.
  Equivalent base64-encoded payloads land at MEDIUM (entropy) which
  is advisory only.
- **Impact:** The scanner is a defence in depth, not a soundness gate;
  this should be documented in the implementer prompt and the spec.
- **Recommendation:** (1) Update [implementer.md:62–67](flowforge/deep_agents/instructions/implementer.md#L62-L67)
  to acknowledge the limitation and shift the burden to the model
  (forbid emitting credentials in *any* form, not just literal token
  strings). (2) Optionally promote MEDIUM entropy findings to HIGH when
  the surrounding identifier matches `(?i)(secret|token|key|password|auth)`.
  (3) Treat this as a known limitation, not a fix to land in T9.
- **Severity rationale:** Minor — bypass requires deliberate model
  cooperation; the scanner is one layer in a stack that includes
  `security_audit_node` and the eventual GitHub push protection.

#### [MINOR-2] Legacy fallback path runs without any secret scanner

- **Location:** [flowforge/nodes/task_runner.py:99–137](flowforge/nodes/task_runner.py#L99-L137)
  (legacy `task_node` body) and [task_runner.py:99–104](flowforge/nodes/task_runner.py#L99-L104)
  (`_run_via_deep_agent` returning `None`).
- **Description:** When `_run_via_deep_agent` cannot produce usable
  artifacts it returns `None` and the legacy single-shot executor runs
  unguarded. The legacy path writes artifact content directly via
  `target.write_text(artifact.content)` with no scan.
- **Impact:** A run that flips between deep and legacy implicitly
  downgrades its security guarantee. An adversarial agent could choose
  to emit no `vfs:/` files and trigger the fallback; once on the legacy
  path, the LLM's `execute_task` artifacts pass straight to disk.
- **Recommendation:** Run the same diff scan against legacy artifacts
  before `target.write_text` ([task_runner.py:114–119](flowforge/nodes/task_runner.py#L114-L119)).
  Out of scope for T9 per the request, but worth tracking explicitly:
  add a TODO comment at the deep-fallback site and log a `BLOCKED`
  follow-up.
- **Severity rationale:** Minor — out of T9 scope by user direction;
  the gap is documented and ticket-tracked rather than fixed in this
  pass. Flagging it here so the spec can keep it on the record.

#### [MINOR-3] Trace `tool_invocations[*].error` may carry verbatim secret text

- **Location:** [flowforge/state/models.py:336–344](flowforge/state/models.py#L336-L344);
  populated via `invocation_sink` plumbed at [task_runner.py:323–328](flowforge/nodes/task_runner.py#L323-L328).
- **Description:** `ToolInvocationRecord.error` is a free-form string.
  If a sub-tool (e.g. `git push`) returns an error message that quotes
  a token from the URL or env (`fatal: Authentication failed for
  'https://x:ghp_…@github.com/…'`), the value lands verbatim in
  `state.deep_agent_traces["task_node"].tool_invocations` and is
  persisted via the checkpointer.
- **Impact:** Low (no current production token surface known to leak
  into tool errors), but the trace is persisted to the checkpointer
  alongside `messages_digest` (which is hashed). The contrast suggests
  a gap.
- **Recommendation:** Run the same regex catalogue (post-fix per
  IMPORTANT-2) against the `error` field at the call site in
  `factory.run_deep_agent_bounded` and replace matches with
  `[REDACTED]`. Out of scope for T9 if the fix is small enough; track
  separately if not.

#### [MINOR-4] `git add` argument list does not use `--` end-of-options separator

- **Location:** [flowforge/nodes/task_runner.py:144–147](flowforge/nodes/task_runner.py#L144-L147)
- **Description:** `subprocess.run(["git", "add", *rels], …)` invokes
  git directly without `--`. If an agent ever emits a relative path
  beginning with `-` (e.g. `vfs:/-foo/bar.py`), `_safe_resolve` accepts
  it (no `..`, not absolute), and `git add -foo/bar.py` interprets
  `-foo` as a (likely unknown) flag. `--quiet`/`--ignore-errors` etc.
  could be set, or git could error and abort the commit silently
  (failure is swallowed). Not RCE — git option parsing is constrained
  — but a behaviour-modulating injection.
- **Impact:** Minor; pre-existing in the legacy path too.
- **Recommendation:** Insert a `--` end-of-options sentinel:
  ```python
  subprocess.run(
      ["git", "add", "--", *rels], cwd=str(workdir), check=True,
      capture_output=True,
  )
  ```

### 4. Infrastructure / Subprocess

#### [INFO-1] Subprocess invocation is correctly hardened

- `subprocess.run(["git", …], cwd=…, check=True, capture_output=True)`
  with `shell=False` and list-form argv ([task_runner.py:144–158](flowforge/nodes/task_runner.py#L144-L158)). ✅
- Failures are swallowed via `subprocess.CalledProcessError` so a
  missing git identity in CI does not cascade. The trade-off (silent
  failure) is acceptable here because `_commit_artifacts` is best-effort.

#### [INFO-2] `DeepAgentTrace` continues to digest messages, not store verbatim

- `aggregate_messages` is reduced via `DeepAgentTrace.digest_messages`
  ([state/models.py:357–366](flowforge/state/models.py#L357-L366)) into a SHA-256.
  Even on BLOCK the trace returned from the deep path
  ([task_runner.py:344–356](flowforge/nodes/task_runner.py#L344-L356))
  carries only the digest plus sorted VFS keys (paths, no content). ✅

### 5. Third-Party Integrations / Filesystem

#### [INFO-3] Symlink following is implicit but not currently exploitable

- `target.read_text()` and `target.write_text()` follow symlinks.
  `persist_files` uses `_safe_resolve`, which calls `target.resolve()`
  and checks `relative_to(workdir_resolved)` *after* symlink resolution.
  This is the right model — a symlink that points outside the workdir
  is rejected. The scan path (IMPORTANT-1) does not yet have that
  guarantee; once IMPORTANT-1 is fixed, symlink races require a
  concurrent attacker with workdir write access, which is outside the
  threat model. ℹ️ Documented.

#### [INFO-4] Seed VFS does not introduce new credential reachability

- `materialize_files` includes `clarified_request.json`, `spec.json`,
  `plan.json`, prior task artifacts, and `findings/*.json`. None of
  these are populated from secret-bearing sources in the FlowForge
  pipeline (they originate from user prompts and prior LLM outputs).
  If a user pastes a token into their request, the agent reads it
  via `vfs:/context/clarified_request.json` (sentinel namespace, not
  scanned, not persisted), and any attempt to embed it in code triggers
  the diff scanner — *provided the regex covers the format* (see
  IMPORTANT-2). Confirmed not to expand the credential blast radius
  beyond what the input already contained. ✅

---

## Threat Model Question Log

| Question | Answer |
|----------|--------|
| Workdir escape via `vfs:/../../etc/passwd`? | **Yes, partial** — escape into the read primitive in `_scan_files_for_secrets` (IMPORTANT-1). Persistence is still blocked by `_safe_resolve`. No content exfiltration through scanner output. |
| Bypass via `findings/`/`context/`/`subagent/` namespaces? | **No effect.** Scanner skips these (correct — they are not persisted). Implementer wrapper does not call `extract_findings`, so agent-written `vfs:/findings/*.json` is dropped. |
| Bypass via `messages` channel? | **No effect.** `messages` is not scanned but is reduced to a SHA-256 digest in the trace; no plaintext storage. |
| Line-splitting / concatenation? | **Bypasses** (MINOR-1). Regex is per-line; concatenation across lines or string operators escapes. Documented as known limitation. |
| Patterns shaped just outside regex anchors? | **Bypasses** for `sk-proj-…`, `github_pat_…`, `gh[osur]_…`, Google, Stripe (IMPORTANT-2). |
| Symlink races during `read_text`? | **Not exploitable** under current threat model (no concurrent writer with workdir access). After IMPORTANT-1 fix, `_safe_resolve` will resolve the symlink and re-check containment. |
| Trace leakage of secrets even on BLOCK? | **Bounded.** Messages digested; VFS keys are paths only; tool errors (MINOR-3) are the residual surface. |
| Partial commit on BLOCK persisting a secret-carrying file? | **No.** `written_paths` only collects post-`persist_files` artifacts of *prior* tasks, each of which already passed its own scan. Verified by `TestPartialCommitOnBlock`. |
| Legacy fallback unscanned? | **Yes** (MINOR-2). Documented as out of T9 scope per request. |

---

## Positive Observations

- The `unified_diff(old, new)` formulation is the right primitive — it
  cleanly separates "what the agent is adding" from "what was already
  there", and the test in `TestDiffVsDisk` codifies the invariant.
- BLOCK-before-persist ordering is verified end-to-end
  (`TestSecretNotPersisted`); this is the strongest property in the
  T9 design.
- Sentinel namespaces (`findings/`/`context/`/`subagent/`) are
  *consistently* skipped in both scanner and persister — no asymmetry
  that could let an agent route around the scan via a special prefix.
- Verification evidence is consumed in-memory from a sentinel-namespace
  file — the right pattern, since it avoids round-tripping advisory
  content through `persist_files`.
- The implementer prompt explicitly tells the agent the scanner exists
  and what it blocks on, raising the cost of accidental leaks.
- `_run_via_deep_agent` falls back to legacy on degenerate output
  rather than failing the run — preserves robustness, but be aware of
  MINOR-2.

---

## Action Items (Priority Order)

| # | Severity | Finding | Recommendation |
|---|----------|---------|----------------|
| 1 | Important | IMPORTANT-1: scanner reads agent-controlled path without `_safe_resolve` | Route through `_safe_resolve`; cap `read_text` size; catch `OSError`/`UnicodeDecodeError`; emit a HIGH `path_traversal` finding on rejection. **Gates merge.** |
| 2 | Important | IMPORTANT-2: regex misses modern token shapes | Extend `_HIGH_PATTERNS` (sk-proj, github_pat, gh[osur]_, Google, Stripe) + tests with synthetic values. **Gates merge.** |
| 3 | Minor     | MINOR-1: line-splitting bypass | Document in `implementer.md`; optionally promote entropy MEDIUM → HIGH near credential identifiers. Track as follow-up. |
| 4 | Minor     | MINOR-2: legacy fallback unscanned | File a follow-up issue; add a TODO at the fallback site. Out of T9 scope. |
| 5 | Minor     | MINOR-3: tool-invocation error strings unredacted | Apply scanner regexes to `error` and replace matches with `[REDACTED]`. |
| 6 | Minor     | MINOR-4: `git add` lacks `--` separator | Add `--` end-of-options sentinel. Two-line change, low risk. |
