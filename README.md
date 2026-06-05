# swe-forge

[![PyPI](https://img.shields.io/pypi/v/swe-forge.svg)](https://pypi.org/project/swe-forge/)

Multi-agent LangGraph framework for autonomous software development.

`swe-forge` takes a one-line prompt and runs it through a 10-node graph
(clarification → spec → plan → fan-out tasks → parallel quality gates
→ issue triage → ship), commits artifacts to a brand-new GitHub repo,
files issues for every finding, and pushes a tagged release when the
quality gates pass.

> The PyPI distribution is `swe-forge`; the Python module is `flowforge`.
> `swe-forge` is the primary CLI command — `flowforge` is kept as an alias.

## Quick start

```bash
# 1. install from PyPI
pip install swe-forge

# 2. configure provider/model (one-time, interactive)
swe-forge setup

# 3. run a prompt — auto-creates a private GitHub repo and pushes there
swe-forge run "build tic-tac-toe web app"

# common flags
swe-forge run "<prompt>" --repo my-name      # use/create a specific repo
swe-forge run "<prompt>" --skip-github       # local-only, no remote
swe-forge run "<prompt>" --no-studio         # skip LangGraph Studio
```

Generated projects land in `~/flowforge-workspace/<slug>/` and are
pushed to `https://github.com/<you>/<slug>`. The `swe-forge` source
repo itself is never written to.

### Prerequisites

- Python ≥ 3.12
- `gh` CLI authenticated (`gh auth login`) — for repo creation + issue filing
- One provider credential, depending on `swe-forge setup` choice:
  - **GitHub Copilot** (default): `gh auth token` is used — no extra setup
  - **OpenAI / Codex**: `export OPENAI_API_KEY=...`
  - **Claude Code**: `export ANTHROPIC_API_KEY=...`

### `swe-forge setup` — interactive walkthrough

```
$ swe-forge setup
━━━ FlowForge Setup ━━━

Which AI assistant integration do you use?
  1) GitHub Copilot (uses GitHub Models API)
  2) OpenAI Codex (uses OpenAI API)
  3) Claude Code (uses Anthropic API)
Select provider [1]: 1

  Using GitHub Models API at https://models.inference.ai.azure.com
  Authentication: `gh auth token` (ensure `gh` CLI is logged in)
  Model (gpt-4o-mini, gpt-4o, o1-mini, o1-preview) [gpt-4o-mini]: gpt-4o-mini

  Create repos as private by default? [Y/n]: Y
  LangGraph Studio port [8123]: 8123

✅ Config saved to ~/.flowforge/config.json
   Provider: copilot
   Model: gpt-4o-mini
   Studio port: 8123
```

Config persists at `~/.flowforge/config.json` (mode `0600`). Re-run
`swe-forge setup` any time to switch model or provider.

## Demo: `build tic-tac-toe web app`

Real run from `pip install swe-forge` (v0.1.21) using `gpt-4o-mini`
via GitHub Copilot.

### Command

```bash
swe-forge run "build tic-tac-toe web app" \
  --repo tic-tac-toe-web --no-studio
```

### Terminal output

```
  ✓ Created GitHub repo tic-tac-toe-web → https://github.com/<you>/tic-tac-toe-web
======================================================================
FlowForge — AI-Powered Code Generation Pipeline
======================================================================

  Prompt: build tic-tac-toe web app
  Provider: copilot
  Model: gpt-4o-mini
  Workdir: ~/flowforge-workspace/tic-tac-toe-web
  Target repo: tic-tac-toe-web
  Repo URL: https://github.com/<you>/tic-tac-toe-web

🚀 Starting LangGraph server on port 8123...
   (server logs → ~/.flowforge/langgraph-dev.log)
  ✓ LangGraph server ready

📊 LangGraph Studio: https://smith.langchain.com/studio/?baseUrl=http://localhost:8123
   Watch your graph execute live in the browser!

━━━ Running Pipeline via LangGraph API ━━━

  Invoking graph... (watch Studio for live visualization)

  ━━━ Node: clarification_node ━━━
     · status=running
     ⏳ clarification_node running... 4s
     + docs/spec/tic-tac-toe-web-app.md
     ✓ clarification_node done in 7.8s
  ━━━ Node: spec_node ━━━
     · summary: This project involves building a small web application for a tic-tac-toe game…
     · 5 acceptance criteria | stack: HTML5, CSS3, JavaScript >= ES6, React 17+
     · wrote: docs/spec/tic-tac-toe-web-app.md
     ⏳ spec_node running... 6s
     + docs/plans/tic-tac-toe-web-app.md
     ✓ spec_node done in 11.1s
  ━━━ Node: plan_node ━━━
     · 3 phases: Phase 1: Foundation, Phase 2: Core Features, Phase 3: Polish | 6 tasks | 6 deps
     ✓ plan_node done in 0.0s
  ━━━ Node: task_fanout_router ━━━
     ✓ task_fanout_router done in 0.0s
  ━━━ Node: task_node ━━━
     ✓ task_node done in 0.0s
  ━━━ Node: quality_gate_join ━━━
     ✓ quality_gate_join done in 0.0s
  ━━━ Node: test_engineer_node ━━━
     · test: 0 findings
     ⏳ test_engineer_node running... 10s
     + docs/reviews/code-review-checkpoint-1.md
     + docs/security-audits/security-audit-1.md
     ✓ test_engineer_node done in 12.7s
  ━━━ Node: code_review_node ━━━
     · review: 3 findings (critical=2, high=1)
     ⏳ code_review_node running... 3s
     ✓ code_review_node done in 7.4s
  ━━━ Node: security_audit_node ━━━
     · security: 4 findings (critical=1, high=1, low=1, medium=1)
     ✓ security_audit_node done in 0.0s
  ━━━ Node: quality_gate_merge ━━━
     ⏳ quality_gate_merge running... 35s
     + docs/triage/triage-report-1.md
     ✓ quality_gate_merge done in 36.9s
  ━━━ Node: issue_orchestrator_node ━━━
     · 7 triaged: can_follow_up=4, must_fix_before_ship=3
     · shipping_ready=False (0 blockers)
     ✓ issue_orchestrator_node done in 0.0s
  ━━━ Node: ship_node ━━━
     · shipped=False
     ✓ ship_node done in 0.0s

======================================================================
PIPELINE COMPLETE
======================================================================

⚠️  Pipeline ended with status: blocked
   Local workdir: ~/flowforge-workspace/tic-tac-toe-web
```

End-to-end runtime: **~75s** with `gpt-4o-mini`.

### What was produced

Five commits, all pushed to the new GitHub repo:

```bash
$ cd ~/flowforge-workspace/tic-tac-toe-web && git log --oneline
a464d05 docs: add triage report #1
a48ffc2 docs: add security audit report #1
df635eb docs: add code review checkpoint 1
ebd691a docs: add implementation plan (tic-tac-toe-web-app.md)
d666fb9 docs: add specification (tic-tac-toe-web-app.md)

$ find docs -type f
docs/plans/tic-tac-toe-web-app.md
docs/spec/tic-tac-toe-web-app.md
docs/security-audits/security-audit-1.md
docs/triage/triage-report-1.md
docs/reviews/code-review-checkpoint-1.md
```

12 GitHub issues filed automatically — every finding from the
review / security gates becomes a labelled issue, then the
orchestrator dedupes and prioritizes them:

```bash
$ gh issue list --repo <you>/tic-tac-toe-web --limit 12
12  OPEN  [LOW] Missing Security Headers                       security, priority-low
11  OPEN  [MEDIUM] Sensitive Data Exposure in API Responses    security, priority-medium
10  OPEN  [HIGH] Insecure Password Storage                     security, priority-high
 9  OPEN  [CRITICAL] SQL Injection Vulnerability Detected      security, priority-critical
 8  OPEN  [CRITICAL] Lack of Input Validation                  security, priority-critical
 7  OPEN  [LOW] Missing Security Headers                       issue-by-code-review, security
 6  OPEN  [MEDIUM] Sensitive Data Exposure in API Responses    issue-by-code-review, security
 5  OPEN  [HIGH] Insecure Password Storage                     issue-by-code-review, security
 4  OPEN  [HIGH] Potential N+1 Query Pattern                   issue-by-code-review
 3  OPEN  [CRITICAL] SQL Injection Vulnerability Detected      issue-by-code-review, security
 2  OPEN  [CRITICAL] Lack of Input Validation                  issue-by-code-review
 1  OPEN  [CRITICAL] Game State Not Updating Correctly         issue-by-code-review
```

### Reading the output

| Marker | Meaning |
| --- | --- |
| `━━━ Node: X ━━━` | Node started |
| `· …` | Structured payload summary (spec title, finding counts, etc.) |
| `+ path/file` | File created in the workdir |
| `~ path/file` | File modified in the workdir |
| `⏳ X running… Ns` | Heartbeat printed every 5s for slow nodes |
| `✓ X done in N.Ns` | Node finished with elapsed time |

### Pipeline outcomes

| Status | When | Behavior |
| --- | --- | --- |
| `succeeded` | All gates clean | `ship_node` writes `CHANGELOG.md` / `README.md`, bumps version, tags, and runs `git push origin HEAD --follow-tags` |
| `blocked` | One or more `must_fix_before_ship` issues | Artifacts committed and issues filed, but no release tag / push from `ship_node` |

The tic-tac-toe run hit `blocked` because the code review flagged
2 critical issues (input validation, game-state bug) and security
flagged 1 critical (SQL injection) — exactly what the gates exist for.
Re-run `swe-forge run` after addressing the must-fix issues to
trigger a clean ship.

## Architecture

```
START
  └─▶ clarification_node ─▶ spec_node ─▶ plan_node ─▶ task_fanout_router
                                                          │
                                                          ▼
                                                       task_node
                                                          │
                                                          ▼
                                                  quality_gate_join
                                       ┌──────────────────┼──────────────────┐
                                       ▼                  ▼                  ▼
                               code_review_node  security_audit_node  test_engineer_node
                                       └──────────────────┼──────────────────┘
                                                          ▼
                                                  quality_gate_merge
                                                          │
                                                          ▼
                                              issue_orchestrator_node
                                                          │
                                                          ▼
                                                      ship_node ─▶ END
```

All file writes, git commits, `gh` issue/label creation, and the
final push happen with `cwd=workdir`, so the source repo of
`swe-forge` is never touched.

## Development

```bash
git clone https://github.com/shashankswe2020-ux/flowforge && cd flowforge
pip install -e ".[dev]"

pytest tests/ -q                # full test suite (431 tests)
ruff check flowforge tests      # lint
mypy flowforge                  # type-check
python -m build                 # build wheel + sdist
```

Server logs go to `~/.flowforge/langgraph-dev.log`. Config lives at
`~/.flowforge/config.json`. Workspaces at `~/flowforge-workspace/`.

## Links

- PyPI: https://pypi.org/project/swe-forge/
- Source: https://github.com/shashankswe2020-ux/flowforge
- Issues: https://github.com/shashankswe2020-ux/flowforge/issues


