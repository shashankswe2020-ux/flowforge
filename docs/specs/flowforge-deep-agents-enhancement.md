# Spec: Enhance FlowForge Graph with LangChain Deep Agents

> Status: **Draft**
> Author: spec agent
> Date: 2026-06-11
> Reference: https://docs.langchain.com/oss/python/deepagents/overview
> Supersedes/extends: `docs/specs/flowforge-langgraph-agentic-development-pipeline.md`

---

## 1. Objective

Upgrade the existing 10-node FlowForge LangGraph pipeline (`clarification → spec → plan → fan-out tasks → parallel quality gates → triage → ship`) so that the **agentic nodes** are powered by **LangChain Deep Agents** instead of single-shot LLM invocations.

Deep Agents introduce four primitives that the current pipeline lacks:

1. **Planning tool** (`write_todos`) — explicit, persistent plans the agent edits as it works.
2. **Sub-agents** — context-isolated worker agents the orchestrator can delegate to.
3. **Virtual file system** (`ls` / `read_file` / `write_file` / `edit_file`) — long-horizon scratch memory that does not pollute the LLM context window.
4. **Detailed system prompt** — domain-specific instructions, examples, and guardrails per agent role.

Adopting Deep Agents converts each long-running FlowForge node from a *prompt-and-parse* call into a self-directed agent loop, and lets us replace several bespoke orchestration mechanisms (DAG fan-out for tasks, manual quality-gate join/merge) with the framework's built-in sub-agent delegation.

### 1.1 Target users

- **AI assistant operators** (Claude Desktop, GitHub Copilot, Codex) who run `swe-forge run "<prompt>"`.
- **Engineering teams** who want each pipeline node to be auditable, resumable, and capable of multi-step reasoning.

### 1.2 Success definition

A single graph execution can:

1. Reach `succeeded` or `blocked` end state with **identical artifact contracts** to today (specs, plans, code, reviews, audits, tests, triage, PR).
2. Surface a **persistent todo list** and **virtual workspace state** per agent node, visible in LangGraph Studio.
3. Allow each agent node to **delegate** to specialized sub-agents (e.g. `code-reviewer`, `security-auditor`, `test-engineer`) rather than running them as separate top-level graph nodes.
4. Pass the entire existing test suite (441 tests) with deep-agent-backed nodes substituted in via feature flag.

---

## 2. Scope

### 2.1 In scope

- Replacing the LLM call inside each of the following nodes with a `create_deep_agent(...)` invocation:
  - `clarification_node`
  - `spec_node`
  - `plan_node`
  - `task_node` (per-task worker)
  - `code_review_node`
  - `security_audit_node`
  - `test_engineer_node`
  - `issue_orchestrator_node`
- Defining a canonical set of **FlowForge tools** exposed to deep agents (file write, gh CLI shim, git shim, MCP tool passthrough).
- Defining **sub-agents** for delegation (reviewer, auditor, tester, refactorer, doc-writer).
- Extending `GraphState` to carry the deep-agent virtual file system and todo list per node.
- Persisting deep-agent state through the existing LangGraph checkpointer.
- Feature-flagging the rollout: `--use-deep-agents` CLI flag and `FLOWFORGE_DEEP_AGENTS=1` env var.

### 2.2 Out of scope

- Replacing the deterministic nodes (`task_fanout_router`, `quality_gate_join`, `quality_gate_merge`, `ship_node`). These remain plain Python.
- Changing the public CLI surface, config file format, or generated-repo layout.
- Swapping the LLM provider abstraction (`flowforge/adapters/`) — Deep Agents will be wrapped by the existing adapter contract.
- Replacing pytest, ruff, or mypy.

### 2.3 Non-goals

- Building a new orchestration framework.
- Multi-tenant / multi-user runtime isolation beyond what Deep Agents already provides.
- Agent-to-agent direct messaging outside the LangGraph state channel.

---

## 3. Background — the current graph

Existing topology (from `flowforge/graph/builder.py`):

```
START
  └─▶ clarification_node ─▶ spec_node ─▶ plan_node ─▶ task_fanout_router
                                                          │
                                                          ▼
                                                       task_node
                                                          │
                                                          ▼
                                                  quality_gate_join
                              ┌───────────────────────────┼───────────────────────────┐
                              ▼                           ▼                           ▼
                    code_review_node          security_audit_node            test_engineer_node
                              └───────────────────────────┼───────────────────────────┘
                                                          ▼
                                                  quality_gate_merge
                                                          │
                                                          ▼
                                              issue_orchestrator_node
                                                          │
                                                          ▼
                                                      ship_node ─▶ END
```

Each agent node today:
- Receives `GraphState`.
- Builds a prompt string.
- Calls `llm.invoke(prompt)`.
- Parses the response into structured artifacts (Pydantic).
- Writes files to the workspace (`flowforge/nodes/_workspace.py`).
- Returns a state delta.

Limitations this enhancement addresses:
1. **Single-shot** — no multi-turn reasoning, planning, or self-correction inside a node.
2. **No scratch memory** — every node call re-derives context from `GraphState`.
3. **No delegation** — `code_review_node` cannot ask a focused "security sub-reviewer" for a second opinion without creating a new graph node.
4. **Context bloat** — large file contents get embedded in prompts every time.

---

## 4. Deep Agents primer (anchored to docs)

Per https://docs.langchain.com/oss/python/deepagents/overview, a Deep Agent is constructed with:

```python
from deepagents import create_deep_agent

agent = create_deep_agent(
    tools=[...],            # callable tools or LangChain Tools
    instructions="...",     # detailed system prompt for THIS role
    subagents=[...],        # list of sub-agent specs (name, description, prompt, tools)
    model=...,              # LangChain chat model (optional override)
).with_config({"recursion_limit": 1000})
```

The returned object is a **LangGraph** (CompiledStateGraph) and exposes the standard `invoke({"messages": [...]})` / `stream(...)` interface. Built-in tools the framework injects:

- `write_todos` — planning.
- `ls`, `read_file`, `write_file`, `edit_file` — virtual file system.
- `task` — invoke a named sub-agent with an isolated context window.

This means a Deep Agent node can be **embedded inside an outer LangGraph node** as a sub-graph: the outer node calls `agent.invoke({...})` and translates the result to a `GraphState` delta.

---

## 5. Proposed architecture

### 5.1 Topology after enhancement

The outer FlowForge graph topology is **unchanged**. Internally, each agentic node is replaced with a Deep Agent invocation:

```
clarification_node       → DeepAgent(role="clarifier",   subagents=[…])
spec_node                → DeepAgent(role="spec_author", subagents=["researcher"])
plan_node                → DeepAgent(role="planner",     subagents=["estimator"])
task_node (per task)     → DeepAgent(role="implementer", subagents=["refactorer","doc_writer"])
code_review_node         → DeepAgent(role="reviewer",    subagents=["arch_reviewer","perf_reviewer"])
security_audit_node      → DeepAgent(role="auditor",     subagents=["dep_scanner","secret_scanner"])
test_engineer_node       → DeepAgent(role="tester",      subagents=["coverage_analyst"])
issue_orchestrator_node  → DeepAgent(role="triager",     subagents=["dedupe_helper"])
```

The deterministic nodes (`task_fanout_router`, `quality_gate_join`, `quality_gate_merge`, `ship_node`) remain plain Python. Adding Deep Agents *inside* nodes preserves the existing checkpointer, retry semantics, and Studio visualization at the outer graph level.

### 5.2 Module layout

```
flowforge/
├── deep_agents/                    # NEW package
│   ├── __init__.py
│   ├── factory.py                  # build_deep_agent(role, llm, workdir) → CompiledStateGraph
│   ├── instructions/               # one .md per role; loaded at runtime
│   │   ├── clarifier.md
│   │   ├── spec_author.md
│   │   ├── planner.md
│   │   ├── implementer.md
│   │   ├── reviewer.md
│   │   ├── auditor.md
│   │   ├── tester.md
│   │   └── triager.md
│   ├── subagents.py                # registry of named sub-agent specs
│   ├── tools.py                    # FlowForge-specific tools (gh, git, run_tests, etc.)
│   └── adapters.py                 # GraphState ⇄ deep-agent message/file-system mapping
├── graph/
│   └── builder.py                  # gains build_deep_agent_graph(llm)
└── nodes/                          # existing nodes; each gains a deep_agent variant
```

### 5.3 Agent factory contract

```text
build_deep_agent(
    role: AgentRole,                # enum
    llm: ChatModel,                 # via existing adapter
    workdir: Path,                  # generated-repo workdir; tools constrain writes here
    todo_seed: list[str] | None,    # optional initial plan
    extra_tools: list[Tool] = [],
) -> CompiledStateGraph
```

Returns a Deep Agent whose system prompt = `instructions/<role>.md` rendered with workdir context, whose tools include the role-appropriate subset of §6, and whose `subagents` are pulled from the registry by role.

### 5.4 Per-node integration pattern

Each existing node becomes a thin wrapper:

```text
def code_review_node(state: GraphState, *, llm) -> dict:
    agent = build_deep_agent(role=Role.REVIEWER, llm=llm, workdir=state.workdir)
    result = agent.invoke({
        "messages": [HumanMessage(seed_prompt_for_review(state))],
        "files": adapters.materialize_files(state),     # seed VFS with generated artifacts
    })
    findings = adapters.extract_findings(result)
    artifacts = adapters.persist_files(result, state.workdir)
    return state.delta(findings=findings, artifacts=artifacts)
```

Key invariants:
- **Inputs to and outputs from each FlowForge node remain Pydantic-typed**; Deep Agents are an implementation detail.
- The deep agent's virtual file system is **mirrored** to the on-disk workdir at node exit (and only the diff is captured in the artifact list).
- Each node enforces a **recursion limit** (default 50) and a **wall-clock timeout** (default 5 min) to bound runaway agents.

---

## 6. Tools exposed to Deep Agents

Beyond the framework's built-in `write_todos` / `ls` / `read_file` / `write_file` / `edit_file` / `task`, FlowForge provides:

| Tool                     | Available to roles               | Description                                                                 |
| ------------------------ | -------------------------------- | --------------------------------------------------------------------------- |
| `run_tests(path?)`       | implementer, tester              | Runs `pytest -q` (or detected runner) inside `workdir`; returns junit-style summary. |
| `run_lint()`             | implementer, reviewer            | Runs `ruff check .` (or detected linter); returns findings JSON.            |
| `run_typecheck()`        | implementer, reviewer            | Runs `mypy` (or detected type-checker); returns findings JSON.              |
| `git_status()`           | implementer, ship-prep           | Shadow of `git status --porcelain` — read-only.                             |
| `git_diff(rev?)`         | reviewer, auditor                | Read-only diff against `HEAD` or named revision.                            |
| `gh_issue_create(...)`   | triager                          | Wraps `gh issue create`; idempotent via title hash.                         |
| `gh_label_ensure(...)`   | triager                          | Ensures label exists.                                                       |
| `web_search(q)`          | spec_author, planner, auditor    | Optional; gated by `FLOWFORGE_ALLOW_WEB=1`.                                 |
| `mcp_invoke(tool, args)` | all                              | Generic passthrough to MCP tools registered with the assistant adapter.     |

Tool requirements:

1. Every tool is a **typed Python function** with a Pydantic input schema. No `Any` in production tool signatures.
2. Every tool is **side-effect-bounded** to `state.workdir`. Path-traversal escapes are rejected with a typed `ToolPolicyError`.
3. Every tool emits a **telemetry event** (`tool.invoked`, `tool.succeeded`, `tool.failed`) consumed by the existing run logger.
4. Long-running tools (`run_tests`, `web_search`) are **interruptible** via the LangGraph checkpointer.

---

## 7. Sub-agents

Sub-agents run with their own context window and are listed in the parent agent's `subagents` array. Each spec includes `name`, `description`, `prompt`, optional `tools`, and optional `model` override.

### 7.1 Catalog

| Parent role     | Sub-agent          | Purpose                                                                 |
| --------------- | ------------------ | ----------------------------------------------------------------------- |
| spec_author     | `researcher`       | Gather references / prior art, write to `vfs:/research/*.md`.            |
| planner         | `estimator`        | Estimate task size & dependencies; emits `vfs:/plan/estimates.json`.     |
| implementer     | `refactorer`       | Apply mechanical refactors; never introduces new behavior.              |
| implementer     | `doc_writer`       | Generate docstrings & README sections from code.                        |
| reviewer        | `arch_reviewer`    | Architectural & boundary critique only.                                 |
| reviewer        | `perf_reviewer`    | Performance & complexity critique only.                                 |
| auditor         | `dep_scanner`      | Inspect dependency manifests for known-vulnerable versions.             |
| auditor         | `secret_scanner`   | Scan diff for accidental secrets (regex + entropy).                     |
| tester          | `coverage_analyst` | Identify under-tested modules; suggests test list.                       |
| triager         | `dedupe_helper`    | Cluster overlapping findings into single issues.                        |

### 7.2 Sub-agent contract

- **Isolation**: each invocation gets its own message history; only its return string is folded into the parent.
- **VFS scope**: sub-agents share the parent's virtual file system but operate read-mostly; writes are namespaced under `vfs:/subagent/<name>/`.
- **Determinism**: sub-agent prompts and tool lists are versioned in `subagents.py`; changes require a spec note.

---

## 8. State and persistence

### 8.1 Extensions to `GraphState`

Add an optional, typed field:

```text
class DeepAgentTrace(BaseModel):
    role: AgentRole
    todos: list[Todo]
    vfs_keys: list[str]               # paths in the deep-agent VFS
    messages_digest: str               # sha256 of full message history (for audit)
    duration_ms: int
    recursion_depth: int
    tool_invocations: list[ToolInvocationRecord]

class GraphState(BaseModel):
    ...
    deep_agent_traces: dict[str, DeepAgentTrace] = Field(default_factory=dict)
```

Keyed by node name. This makes the deep-agent loop **observable in LangGraph Studio** as part of the regular state diff at each step.

### 8.2 Checkpointing

- Default backend remains **PostgreSQL** for shared environments and **SQLite** for local dev (per existing spec §Assumptions 7).
- Deep-agent VFS contents are **not** stored in the checkpointer payload directly; instead, the VFS is materialized to the workdir on node exit and the trace records *paths only*. This keeps checkpoint sizes bounded.
- Mid-node interruption: if a Deep Agent run is killed mid-flight, the next resume rebuilds the agent and replays from the last `write_todos` checkpoint (the framework already supports this).

---

## 9. Configuration & feature flags

| Knob                            | Default                  | Effect                                              |
| ------------------------------- | ------------------------ | --------------------------------------------------- |
| `FLOWFORGE_DEEP_AGENTS`         | `0`                      | When `1`, `build_live_graph()` calls `build_deep_agent_graph(llm)`. |
| `--use-deep-agents` CLI flag    | off                      | Same effect as env var, scoped to a single run.     |
| `FLOWFORGE_DEEP_AGENT_RECURSION`| `50`                     | Max recursion depth per Deep Agent invocation.      |
| `FLOWFORGE_DEEP_AGENT_TIMEOUT_S`| `300`                    | Wall-clock timeout per node.                        |
| `FLOWFORGE_ALLOW_WEB`           | `0`                      | Enables `web_search` tool.                          |
| `~/.flowforge/config.json`      | `{ "deep_agents": false }` | Persisted toggle from `swe-forge setup`.            |

The legacy single-shot path remains the default until acceptance gates in §13 pass.

---

## 10. Security boundaries

1. **Workdir confinement**: every tool resolves paths via `Path.resolve()` and rejects anything outside `state.workdir`. Enforced by `flowforge/deep_agents/tools.py::_safe_path`.
2. **Shell execution**: only `run_tests`, `run_lint`, `run_typecheck`, and `git_*` may shell out, and they use **argument lists** — never shell strings — and `cwd=workdir`.
3. **Network**: disabled by default. `web_search` and `mcp_invoke(non-local)` require `FLOWFORGE_ALLOW_WEB=1`.
4. **Secrets**: tokens (`OPENAI_API_KEY`, `gh auth token`) never enter prompts or VFS. The `secret_scanner` sub-agent scans every diff and blocks the run if a high-confidence secret is detected.
5. **Permissions**: `~/.flowforge/config.json` remains mode `0600`. Any new persisted credential follows the same.
6. **Recursion / cost**: recursion limit and timeout (above) bound runaway loops. Tool invocation count per node is capped at 200 with a typed `ToolBudgetExceededError`.
7. **Auditability**: every sub-agent invocation is logged with parent → child linkage and a content digest, so a reviewer can reproduce decisions from the trace.

---

## 11. Testing strategy

Follows existing FlowForge conventions: `pytest`, `ruff`, `mypy`.

### 11.1 Unit tests

- `tests/deep_agents/test_factory.py` — `build_deep_agent` produces a graph for every `AgentRole` with the right tools and sub-agents.
- `tests/deep_agents/test_tools.py` — each tool: happy path, path-escape rejection, telemetry emission.
- `tests/deep_agents/test_adapters.py` — round-trip `GraphState ⇄ messages/files`.

### 11.2 Integration tests (per node)

For each role, a fixture-driven test that:
1. Builds the Deep Agent with a **stubbed LLM** that replays a recorded message stream.
2. Invokes the wrapper node with a synthetic `GraphState`.
3. Asserts artifact files written, `DeepAgentTrace` populated, and state delta shape identical to the legacy node's output.

### 11.3 Contract tests (legacy ↔ deep-agent equivalence)

For each agentic node, run the **same input** through:
- the legacy single-shot implementation, and
- the deep-agent implementation,

and assert that the **artifact contracts** match (file paths, JSON schemas, severity counts in expected ranges). The deep-agent output may have richer content; the contract tests check **shape**, not text.

### 11.4 End-to-end

Re-run the full pipeline on the canonical demo prompt (`build tic-tac-toe web app`) with `FLOWFORGE_DEEP_AGENTS=1`, asserting:
- Pipeline completes (status `succeeded` or `blocked`).
- A PR is opened.
- All 441 existing tests still pass.
- New deep-agent test count ≥ 60.

### 11.5 Coverage targets

- `flowforge/deep_agents/` — **≥ 90 %** line coverage.
- `flowforge/nodes/` (deep-agent wrappers) — **≥ 80 %**.
- Overall package — **no regression** vs. current baseline.

### 11.6 LLM mocking

- All tests use the existing fake-LLM helper (`tests/_fakes/llm.py`).
- A new `tests/_fakes/deep_agent.py` records and replays Deep Agent message streams.
- **No real LLM calls** in CI.

---

## 12. Rollout plan

1. **Phase 0 — Foundation.** Add `deepagents` to `requirements.txt`, scaffold `flowforge/deep_agents/` package, factory, instructions stubs, tool registry. Ship behind flag, no node uses it yet.
2. **Phase 1 — Read-only nodes first.** Migrate `code_review_node`, `security_audit_node`, `test_engineer_node`. These produce reports only — lowest blast radius. Run contract tests in CI for both modes.
3. **Phase 2 — Generative nodes.** Migrate `clarification_node`, `spec_node`, `plan_node`, `triager`. Validate against demo runs.
4. **Phase 3 — Implementer.** Migrate `task_node`. This is the highest-risk migration (it writes code). Keep parallel A/B for one release.
5. **Phase 4 — Default on.** Flip `FLOWFORGE_DEEP_AGENTS=1` default. Legacy path remains available via `--no-deep-agents` for one minor version, then removed.

Each phase ends with a code review (per `docs/reviews/`) and a security audit (per `docs/security-audits/`) following existing conventions.

---

## 13. Acceptance criteria

Each criterion is verifiable via a `pytest` test or a shell command.

| # | Criterion                                                                                              | Verification                                                                              |
| - | ------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------- |
| 1 | `deepagents` is a declared dependency.                                                                 | `pip show deepagents`                                                                     |
| 2 | `flowforge/deep_agents/factory.py::build_deep_agent` returns a `CompiledStateGraph` for every `AgentRole`. | `pytest tests/deep_agents/test_factory.py`                                                |
| 3 | Every FlowForge tool rejects paths outside `workdir`.                                                  | `pytest tests/deep_agents/test_tools.py::test_path_escape_rejected`                       |
| 4 | Each agentic node has a feature-flagged deep-agent variant.                                            | `pytest tests/nodes/test_deep_agent_wrappers.py`                                          |
| 5 | Contract tests pass for every agentic node (legacy ↔ deep-agent shape equivalence).                    | `pytest tests/contract/test_legacy_vs_deep.py`                                            |
| 6 | E2E demo run (`build tic-tac-toe web app`) completes with `--use-deep-agents`.                         | `swe-forge run "build tic-tac-toe web app" --repo demo --no-studio --use-deep-agents`     |
| 7 | All 441 existing tests still pass.                                                                     | `pytest tests/ -q`                                                                        |
| 8 | `mypy flowforge` passes with no `Any` in `flowforge/deep_agents/`.                                     | `mypy flowforge && rg --type py "Any" flowforge/deep_agents/ \| rg -v "from typing"`       |
| 9 | `ruff check flowforge tests` passes.                                                                   | `ruff check flowforge tests`                                                              |
|10 | `DeepAgentTrace` appears in `GraphState` after every agentic node in Studio.                           | Manual: `langgraph dev`, run pipeline, inspect state at each step.                        |
|11 | Recursion limit and timeout enforced; runaway agent fails with typed error.                            | `pytest tests/deep_agents/test_limits.py`                                                 |
|12 | No real LLM call in CI.                                                                                | CI run with `OPENAI_API_KEY=invalid` succeeds.                                            |
|13 | Coverage on `flowforge/deep_agents/` ≥ 90 %.                                                           | `pytest --cov=flowforge.deep_agents --cov-fail-under=90`                                  |
|14 | Secret scanner blocks a planted secret in a generated file.                                            | `pytest tests/deep_agents/test_secret_scanner.py::test_blocks_planted_aws_key`            |
|15 | Legacy single-shot path remains available via `--no-deep-agents` for at least one minor version.       | `swe-forge run "..." --no-deep-agents` succeeds; CHANGELOG entry present.                  |

---

## 14. Open questions

1. **Model routing.** Do sub-agents inherit the parent's model, or do we expose per-sub-agent overrides in `~/.flowforge/config.json`? Recommendation: inherit by default; allow override via env (`FLOWFORGE_SUBAGENT_MODEL_<NAME>`). Decision needed.
2. **VFS persistence on resume.** When a checkpoint resume occurs after the workdir was modified externally, do we re-seed the VFS from disk or trust the trace? Recommendation: re-seed from disk; treat the trace as advisory.
3. **Cost budget.** Should we add a per-run dollar cap that aborts when exceeded? Out of scope for this spec; tracked as a follow-up.
4. **Studio visualization of sub-agents.** Deep Agent sub-agent calls happen inside a node and are *not* visible as nodes in the outer LangGraph Studio view. Is the `DeepAgentTrace` panel sufficient, or do we need to surface sub-agents as nested graphs? Recommendation: ship with trace panel first, evaluate need for nested graph rendering after Phase 2.

---

## 15. References

- LangChain Deep Agents overview — https://docs.langchain.com/oss/python/deepagents/overview
- Existing pipeline spec — `docs/specs/flowforge-langgraph-agentic-development-pipeline.md`
- Graph builder — `flowforge/graph/builder.py`
- Existing agentic nodes — `flowforge/nodes/{clarification,spec,plan,task_runner,code_review,security_audit,test_engineer,issue_orchestrator}.py`
- State models — `flowforge/state/models.py`
