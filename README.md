<div align="center">

# FlowForge

## Autonomous Software Engineering via LangGraph

### Build, review, triage, and ship from one prompt

[![PyPI](https://img.shields.io/pypi/v/swe-forge.svg)](https://pypi.org/project/swe-forge/)
[![Python](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![LangGraph](https://img.shields.io/badge/LangGraph-powered-black.svg)](https://www.langchain.com/langgraph)

[![GitHub](https://img.shields.io/badge/GitHub-integrated-181717.svg)](https://github.com)
[![Deep%20Agents](https://img.shields.io/badge/Deep_Agents-default-success.svg)](https://docs.langchain.com/oss/python/deepagents/overview)
[![Quality%20Gates](https://img.shields.io/badge/Quality_Gates-3-blue.svg)](#why-flowforge)

[![Claude%20Code](https://img.shields.io/badge/Claude_Code-supported-blueviolet.svg)](#prerequisites)
[![Codex](https://img.shields.io/badge/Codex-supported-blueviolet.svg)](#prerequisites)
[![Copilot](https://img.shields.io/badge/Copilot-supported-blueviolet.svg)](#prerequisites)

</div>

![FlowForge LangGraph pipeline](https://raw.githubusercontent.com/shashankswe2020-ux/flowforge/main/docs/screenshots/langgraph-pipeline.png)

`swe-forge` takes a one-line prompt and runs it through a 10-node pipeline
(clarification -> spec -> plan -> fan-out tasks -> parallel quality gates
-> issue triage -> ship), writes artifacts into a fresh workdir, opens a PR,
and can publish tagged releases when quality gates pass.

> The PyPI distribution is `swe-forge`; the Python module is `flowforge`.
> Use the `swe-forge` command after install.

## Demo

[![Watch the demo video](https://img.youtube.com/vi/AhMBkaBqc8Y/maxresdefault.jpg)](https://youtu.be/AhMBkaBqc8Y)

Watch the demo video: https://youtu.be/AhMBkaBqc8Y

<details>
<summary><strong>What you will see in the demo</strong></summary>

- End-to-end prompt execution
- Generated spec and implementation plan
- Task fan-out execution and artifact creation
- Quality gates producing review, security, and test findings
- Final triage and shipping decision

</details>

---

## Get Started

### 1. Install

```bash
pip install swe-forge
```

### 2. Configure Provider and Model

```bash
swe-forge setup
```

### 3. Run a Prompt

```bash
swe-forge run "build tic-tac-toe web app"
```

Common flags:

```bash
swe-forge run "<prompt>" --repo my-name      # use/create a specific repo
swe-forge run "<prompt>" --skip-github       # local-only, no remote
swe-forge run "<prompt>" --no-studio         # skip LangGraph Studio
swe-forge run "<prompt>" --no-deep-agents    # deprecated, removed in v0.4
```

<details>
<summary><strong>Expected outputs from one run</strong></summary>

- Project files in `~/flowforge-workspace/<slug>/`
- Git history on a feature branch
- Pull request to `main`
- Findings filed as GitHub issues

</details>

Generated projects are created under `~/flowforge-workspace/<slug>/`.
When GitHub is enabled, FlowForge pushes to
`https://github.com/<you>/<slug>` without writing into this source repo.

---

## Why FlowForge?

FlowForge orchestrates full software-delivery loops, not just code generation:

- Clarifies ambiguous prompts before implementation
- Produces specs and dependency-ordered plans
- Executes tasks in parallel where safe
- Runs three quality gates (code review, security audit, test engineer)
- Files and prioritizes GitHub issues from findings
- Ships through PR and release workflows with traceable artifacts

| Capability | Outcome |
| --- | --- |
| Clarification + Spec + Plan | Better structure before code generation |
| Task fan-out | Faster parallel execution where dependencies allow |
| Quality gates | Deterministic review/security/test checkpoints |
| Issue orchestration | Actionable follow-up work with severity context |
| Shipping node | Consistent PR/release flow with traceability |

## Deep Agents

Deep Agents are enabled by default since `v0.2`.

- Each agentic node runs as a LangChain Deep Agent
- Resource budgets are enforced per run (recursion, timeout, tool budget)
- Implementer includes a diff-based secret scan before persisting files

Opt-out options for the transition period:

```bash
swe-forge run "<prompt>" --no-deep-agents
export FLOWFORGE_DEEP_AGENTS=0
```

<details>
<summary><strong>Deep Agent safety controls</strong></summary>

- Per-run tool budgets
- Recursion and timeout ceilings
- Diff-based secret scanning before persist

</details>

---

## Prerequisites

- Python >= 3.12
- `gh` CLI authenticated via `gh auth login` (repo creation and issue filing)
- Provider credential configured via `swe-forge setup`:
  - GitHub Copilot (default): `swe-forge copilot-login`
  - OpenAI/Codex: `OPENAI_API_KEY`
  - Claude Code: `ANTHROPIC_API_KEY`

### Copilot Login

```bash
swe-forge copilot-login
```

Uses GitHub device-flow and stores OAuth credentials in:
`~/.flowforge/copilot-oauth.json` (mode `0600`).

### Setup Wizard

```bash
swe-forge setup
```

Persists config to `~/.flowforge/config.json` (mode `0600`).

---

## How It Works

```text
START
  -> clarification_node -> spec_node -> plan_node -> task_fanout_router
                                                      |
                                                      v
                                                   task_node
                                                      |
                                                      v
                                              quality_gate_join
                                   +--------------+--------------+
                                   v              v              v
                           code_review    security_audit   test_engineer
                                   +--------------+--------------+
                                                  v
                                           quality_gate_merge
                                                  |
                                                  v
                                       issue_orchestrator_node
                                                  |
                                                  v
                                              ship_node -> END
```

<details>
<summary><strong>Execution model notes</strong></summary>

- Graph state is durable and auditable by node
- Quality gates run in parallel and merge into a single triage view
- `blocked` does not discard output; it annotates and routes follow-up work

</details>

Quality gates annotate delivery rather than silently dropping work:

- `succeeded`: clean gates, release + PR flow proceeds
- `blocked`: must-fix findings exist, but branch/PR artifacts are still produced

---

## Example Run

```bash
swe-forge run "build tic-tac-toe web app" --repo tic-tac-toe-demo --no-studio
```

Typical outputs include:

- Specification and implementation plan docs
- Generated application artifacts and tests
- Code review, security audit, and test reports
- Triage report + GitHub issues
- Feature branch commits and an auto-opened pull request

<details>
<summary><strong>Status semantics</strong></summary>

- `succeeded`: all must-fix gates are clear
- `blocked`: one or more must-fix findings remain

Both statuses still preserve generated artifacts and commit history.

</details>

---

## Development

```bash
git clone https://github.com/shashankswe2020-ux/flowforge && cd flowforge
pip install -e ".[dev]"

pytest tests/ -q
ruff check flowforge tests
mypy flowforge
python -m build
```

Local runtime paths:

- Logs: `~/.flowforge/langgraph-dev.log`
- Config: `~/.flowforge/config.json`
- Workspaces: `~/flowforge-workspace/`

---

## Links

- PyPI: https://pypi.org/project/swe-forge/
- Source: https://github.com/shashankswe2020-ux/flowforge
- Issues: https://github.com/shashankswe2020-ux/flowforge/issues
- Demo video: https://youtu.be/AhMBkaBqc8Y

---

## License

MIT

---

<div align="center">

Built for agentic software delivery with LangGraph and GitHub workflows.

[Report Bug](https://github.com/shashankswe2020-ux/flowforge/issues) · [Request Feature](https://github.com/shashankswe2020-ux/flowforge/issues)

</div>


