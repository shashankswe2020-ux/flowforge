# flowforge

Multi-agent LangGraph framework for autonomous software development.

FlowForge takes a one-line prompt and runs it through a 10-node graph
(clarification → spec → plan → fan-out tasks → parallel quality gates
→ issue triage → ship), commits artifacts to a brand-new GitHub repo,
files issues for every finding, and pushes a tagged release when the
quality gates pass.

## Quick start

```bash
# 1. install
pip install swe-forge

# 2. configure provider/model (one-time)
swe-forge setup

# 3. run a prompt — auto-creates a private GitHub repo and pushes there
swe-forge run "build cute kawaii calculator web app"

# common flags
swe-forge run "<prompt>" --repo my-name      # use/create a specific repo
swe-forge run "<prompt>" --skip-github       # local-only, no remote
swe-forge run "<prompt>" --no-studio         # skip LangGraph Studio
```

Generated projects land in `~/flowforge-workspace/<slug>/` and are
pushed to `https://github.com/<you>/<slug>`. The flowforge source
repo itself is never written to.

## Demo: `build cute kawaii calculator web app`

Run with `gpt-4o-mini` via GitHub Copilot (~58s end-to-end).

### Command

```bash
swe-forge run "build cute kawaii calculator web app" \
  --repo cute-kawaii-calculator-web --no-studio
```

### Terminal output

```
  ✓ Created GitHub repo cute-kawaii-calculator-web → https://github.com/<you>/cute-kawaii-calculator-web
======================================================================
FlowForge — AI-Powered Code Generation Pipeline
======================================================================

  Prompt: build cute kawaii calculator web app
  Provider: copilot
  Model: gpt-4o-mini
  Workdir: ~/flowforge-workspace/cute-kawaii-calculator-web
  Target repo: cute-kawaii-calculator-web
  Repo URL: https://github.com/<you>/cute-kawaii-calculator-web.git

🚀 Starting LangGraph server on port 8123...
   (server logs → ~/.flowforge/langgraph-dev.log)
  ✓ LangGraph server ready

📊 LangGraph Studio: https://smith.langchain.com/studio/?baseUrl=http://localhost:8123

━━━ Running Pipeline via LangGraph API ━━━

  Invoking graph... (watch Studio for live visualization)

  ━━━ Node: clarification_node ━━━
     · status=running
     + docs/spec/kawaii-calculator.md
     ✓ clarification_node done in 5.4s
  ━━━ Node: spec_node ━━━
     · summary: The project involves creating a small web application that serves as a kawaii-themed calculator…
     · 5 acceptance criteria | stack: HTML5, CSS3, JavaScript >= ES6, React 17+
     · wrote: docs/spec/kawaii-calculator.md
     + docs/plans/kawaii-calculator.md
     ✓ spec_node done in 9.1s
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
     + docs/reviews/code-review-checkpoint-1.md
     + docs/security-audits/security-audit-1.md
     ✓ test_engineer_node done in 11.4s
  ━━━ Node: security_audit_node ━━━
     · security: 3 findings (high=1, low=1, medium=1)
     ✓ security_audit_node done in 1.7s
  ━━━ Node: code_review_node ━━━
     · review: 4 findings (critical=1, high=1, medium=2)
     ✓ code_review_node done in 0.0s
  ━━━ Node: quality_gate_merge ━━━
     + docs/triage/triage-report-1.md
     ✓ quality_gate_merge done in 31.0s
  ━━━ Node: issue_orchestrator_node ━━━
     · 7 triaged: can_follow_up=5, must_fix_before_ship=2
     · shipping_ready=False (0 blockers)
     ✓ issue_orchestrator_node done in 0.0s
  ━━━ Node: ship_node ━━━
     · shipped=False
     ✓ ship_node done in 0.0s

======================================================================
PIPELINE COMPLETE
======================================================================

⚠️  Pipeline ended with status: blocked
   Local workdir: ~/flowforge-workspace/cute-kawaii-calculator-web
```

### What was produced

Five commits, all pushed to the new GitHub repo:

```bash
$ cd ~/flowforge-workspace/cute-kawaii-calculator-web && git log --oneline
fac8566 docs: add triage report #1
dfa732d docs: add code review checkpoint 1
9e6f90f docs: add security audit report #1
643938f docs: add implementation plan (kawaii-calculator.md)
05645af docs: add specification (kawaii-calculator.md)

$ find . -type f -not -path "./.git/*" | sort
./docs/plans/kawaii-calculator.md
./docs/reviews/code-review-checkpoint-1.md
./docs/security-audits/security-audit-1.md
./docs/spec/kawaii-calculator.md
./docs/triage/triage-report-1.md
```

11 GitHub issues filed automatically — every finding from the
review / security / test gates becomes a labelled issue, then the
orchestrator dedupes and prioritizes them:

```bash
$ gh issue list --repo <you>/cute-kawaii-calculator-web
11  OPEN  [LOW] Missing Security Headers                       security, priority-low
10  OPEN  [MEDIUM] Sensitive Data Exposure in API Responses    security, priority-medium
 9  OPEN  [CRITICAL] Incorrect arithmetic operation handling   bug, priority-critical
 8  OPEN  [HIGH] Potential SQL Injection Vulnerability         security, priority-high
 7  OPEN  [MEDIUM] Potential N+1 query pattern in theme loading
 6  OPEN  [MEDIUM] Inconsistent naming conventions
 5  OPEN  [LOW] Missing Security Headers                       security
 4  OPEN  [HIGH] Lack of input validation for arithmetic operations
 3  OPEN  [MEDIUM] Sensitive Data Exposure in API Responses    security
 2  OPEN  [HIGH] Potential SQL Injection Vulnerability         security
 1  OPEN  …
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

The kawaii calculator run hit `blocked` because the code review
flagged a `critical` arithmetic bug and a `high`-severity
input-validation gap — exactly what the gates exist for.

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
final push happen with `cwd=workdir`, so the flowforge meta-repo is
never touched.

## Development

```bash
pytest tests/ -q       # full test suite
ruff check src tests   # lint
mypy src               # type-check
```

Server logs go to `~/.flowforge/langgraph-dev.log`. Config lives at
`~/.flowforge/config.json`. Workspaces at `~/flowforge-workspace/`.

