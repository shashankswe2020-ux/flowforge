"""FlowForge CLI — main entry point.

Usage:
    flowforge-ai setup              # Configure provider and model
    flowforge-ai "Build a web app"  # Run pipeline with prompt
    flowforge-ai "idea" --repo name # Run and push to GitHub repo
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import click

from flowforge.cli.config import CONFIG_DIR, FlowForgeConfig


class FlowForgeGroup(click.Group):
    """Custom group that resolves subcommands before consuming the prompt argument.

    This allows `flowforge-ai "prompt"` to work as a shortcut for `flowforge-ai run "prompt"`,
    while `flowforge-ai setup` correctly routes to the setup subcommand.
    """

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        # If first arg is a known subcommand, route to it
        if args and args[0] in self.commands:
            return super().parse_args(ctx, args)
        # Otherwise, treat first positional arg as prompt for `run`
        # Inject 'run' subcommand implicitly
        if args and not args[0].startswith("-"):
            args = ["run"] + args
        return super().parse_args(ctx, args)


def _get_version() -> str:
    """Return the installed swe-forge package version."""
    try:
        from importlib.metadata import version
        return version("swe-forge")
    except Exception:
        return "unknown"


@click.group(cls=FlowForgeGroup, context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(_get_version(), "-V", "--version", prog_name="swe-forge")
def cli() -> None:
    """FlowForge — multi-agent LangGraph pipeline for autonomous software development.

    Takes a one-line prompt and runs it through a 10-node graph
    (clarify → spec → plan → fan-out tasks → parallel quality gates
    → issue triage → ship), commits artifacts to a feature branch on a
    fresh GitHub repo, files issues for every finding, and opens a PR.

    \b
    Quick start:
      swe-forge setup                          Configure provider + model (one-time)
      swe-forge run "build tic-tac-toe app"    Generate code + push + open PR
      swe-forge run "<prompt>" --skip-github   Local-only, no GitHub push

    \b
    Files:
      ~/.flowforge/config.json     Provider, model, port (mode 0600)
      ~/.flowforge/.env            LLM credentials for langgraph dev
      ~/.flowforge/langgraph.json  LangGraph server config
      ~/.flowforge/langgraph-dev.log  Server logs
      ~/flowforge-workspace/<slug>/   Generated projects

    Run `swe-forge COMMAND --help` for command-specific options.
    """


@cli.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("prompt")
@click.option(
    "--repo",
    metavar="NAME",
    help="GitHub repo name. Creates a private repo under your account if it "
         "doesn't exist, otherwise reuses the existing one. "
         "Defaults to a slug derived from the prompt.",
)
@click.option(
    "--skip-github",
    is_flag=True,
    help="Local-only run. Skip repo creation, branch push, and PR opening. "
         "Artifacts are still committed to a local git repo in the workdir.",
)
@click.option(
    "--no-studio",
    is_flag=True,
    help="Skip launching LangGraph Studio in the browser. The langgraph dev "
         "server still starts (required for graph execution); only the "
         "Studio URL is suppressed.",
)
@click.option(
    "--use-deep-agents/--no-deep-agents",
    "deep_agents",
    default=None,
    help="Enable (or disable) the Deep Agents execution path for agentic "
         "nodes. As of v0.2 the default is ON; --no-deep-agents remains "
         "available for one minor version (deprecated, slated for removal "
         "in v0.4). Overrides the FLOWFORGE_DEEP_AGENTS env var and the "
         "persisted ~/.flowforge/config.json setting for this run only.",
)
def run(
    prompt: str,
    repo: str | None,
    skip_github: bool,
    no_studio: bool,
    deep_agents: bool | None,
) -> None:
    """Generate code from a prompt.

    Runs the full pipeline: clarify → spec → plan → fan-out tasks →
    code review + security audit + test engineer (parallel) → triage →
    ship (commit, push, open PR).

    Always creates a feature branch `flowforge/run-<timestamp>` and
    opens a pull request — even when quality gates flag must-fix
    issues. Blocked runs still push code; the PR description calls
    out what needs review.

    \b
    Examples:
      swe-forge run "Build a REST API with FastAPI"
      swe-forge run "Build a CLI tool" --repo my-tool
      swe-forge run "Prototype a parser" --skip-github
      swe-forge run "Build a web app" --no-studio

    PROMPT is the natural-language description of what to build.
    """
    # Ensure setup has been run
    if not FlowForgeConfig.exists():
        click.echo("⚠️  FlowForge not configured yet. Running setup first...\n")
        _do_setup()
        click.echo()

    # Surface the per-run flag to the langgraph dev subprocess via env var
    # so build_live_graph() can pick it up. Resolution priority:
    # CLI > env > config > default (False) — see flowforge.config.deep_agents.
    if deep_agents is not None:
        import os as _os

        from flowforge.config.deep_agents import DEEP_AGENTS_ENV_VAR

        _os.environ[DEEP_AGENTS_ENV_VAR] = "1" if deep_agents else "0"

    _run_pipeline(prompt, repo=repo, skip_github=skip_github, no_studio=no_studio)


@cli.command(context_settings={"help_option_names": ["-h", "--help"]})
def setup() -> None:
    """Interactive setup wizard — configure provider, model, and preferences.

    Prompts you to choose:

    \b
      • LLM provider (GitHub Copilot, OpenAI Codex, or Claude Code)
      • Model (e.g. gpt-4o-mini, gpt-4o, o1-mini)
      • Default repo visibility (private / public)
      • LangGraph Studio port (default 8123)

    Writes config to ~/.flowforge/config.json (mode 0600). Re-run
    any time to switch provider or model.
    """
    _do_setup()


@cli.command("copilot-login", context_settings={"help_option_names": ["-h", "--help"]})
def copilot_login() -> None:
    """Authorize FlowForge against your GitHub Copilot subscription.

    Runs the GitHub device-flow against the Copilot OAuth client.
    You'll be shown a code and a URL — open it in any browser, paste
    the code, and authorize. The resulting token is cached at
    ``~/.flowforge/copilot-oauth.json`` (mode 0600) and used by every
    subsequent run.
    """
    from flowforge.auth.copilot import CopilotAuthError, device_login

    try:
        device_login()
    except CopilotAuthError as exc:
        click.echo(f"❌ Login failed: {exc}")
        raise SystemExit(1) from exc


def _do_setup() -> None:
    """Setup wizard implementation."""
    click.echo("━━━ FlowForge Setup ━━━\n")

    config = FlowForgeConfig.load() if FlowForgeConfig.exists() else FlowForgeConfig()

    # Provider selection
    click.echo("Which AI assistant integration do you use?")
    click.echo("  1) GitHub Copilot (uses GitHub Models API)")
    click.echo("  2) OpenAI Codex (uses OpenAI API)")
    click.echo("  3) Claude Code (uses Anthropic API)")
    choice = click.prompt("Select provider", type=click.IntRange(1, 3), default=1)

    provider_map = {1: "copilot", 2: "codex", 3: "claude_code"}
    config.provider = provider_map[choice]

    # Model selection based on provider
    if config.provider == "copilot":
        config.api_base = "https://api.githubcopilot.com"
        click.echo(f"\n  Using GitHub Copilot API at {config.api_base}")
        click.echo("  Authentication: device-flow OAuth (one-time browser login)")
        model = click.prompt(
            "  Model",
            default="gpt-4o",
            type=click.Choice(
                ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "o1-mini", "claude-3.5-sonnet"]
            ),
        )
        config.model = model
        from flowforge.auth.copilot import (
            CopilotAuthError,
            device_login,
            load_oauth_token,
        )

        if load_oauth_token() is None:
            try:
                device_login()
            except CopilotAuthError as exc:
                click.echo(f"\n❌ Copilot login failed: {exc}")
                raise SystemExit(1) from exc
        else:
            click.echo("  Existing Copilot OAuth token found (re-run `swe-forge copilot-login` to refresh).")
    elif config.provider == "codex":
        config.api_base = "https://api.openai.com/v1"
        model = click.prompt(
            "  Model",
            default="gpt-4o-mini",
            type=click.Choice(["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "o1-mini"]),
        )
        config.model = model
        click.echo("  Set OPENAI_API_KEY env var for authentication")
    elif config.provider == "claude_code":
        config.api_base = "https://api.anthropic.com"
        model = click.prompt(
            "  Model",
            default="claude-sonnet-4-20250514",
            type=click.Choice(
                ["claude-sonnet-4-20250514", "claude-opus-4-20250514", "claude-3-haiku-20240307"]
            ),
        )
        config.model = model
        click.echo("  Set ANTHROPIC_API_KEY env var for authentication")

    # Default repo visibility
    config.default_private = click.confirm("\n  Create repos as private by default?", default=True)

    # LangGraph Studio port
    config.langgraph_port = click.prompt(
        "  LangGraph Studio port", default=8123, type=int
    )

    config.save()
    click.echo(f"\n✅ Config saved to {CONFIG_DIR}/config.json")
    click.echo(f"   Provider: {config.provider}")
    click.echo(f"   Model: {config.model}")
    click.echo(f"   Studio port: {config.langgraph_port}")


def _run_pipeline(
    prompt: str,
    *,
    repo: str | None,
    skip_github: bool,
    no_studio: bool,
) -> None:
    """Execute the full pipeline via LangGraph API so Studio shows live execution."""
    import subprocess
    import sys
    import time

    from flowforge.cli.config import FlowForgeConfig

    config = FlowForgeConfig.load()

    # Prepare target workdir + GitHub repo (so generated artifacts land in their own repo)
    workdir, target_repo, repo_url = _prepare_workspace(
        prompt, repo=repo, skip_github=skip_github, private=config.default_private,
    )

    click.echo("=" * 70)
    click.echo("FlowForge — AI-Powered Code Generation Pipeline")
    click.echo("=" * 70)
    click.echo(f"\n  Prompt: {prompt}")
    click.echo(f"  Provider: {config.provider}")
    click.echo(f"  Model: {config.model}")
    click.echo(f"  Workdir: {workdir}")
    if target_repo:
        click.echo(f"  Target repo: {target_repo}")
    if repo_url:
        click.echo(f"  Repo URL: {repo_url}")
    click.echo()

    # Write .env for langgraph dev to pick up
    _write_env_file(config)

    # Start LangGraph dev server
    studio_process = _start_studio(config)
    if not studio_process:
        click.echo("❌ Cannot start LangGraph server. Install: pip install 'langgraph-cli[inmem]'")
        raise SystemExit(1)

    try:
        # Wait for server to be ready
        _wait_for_server(config.langgraph_port)

        studio_url = f"https://smith.langchain.com/studio/?baseUrl=http://localhost:{config.langgraph_port}"
        click.echo(f"📊 LangGraph Studio: {studio_url}")
        click.echo("   Watch your graph execute live in the browser!\n")

        # Invoke graph via LangGraph SDK
        click.echo("━━━ Running Pipeline via LangGraph API ━━━\n")
        final_state = _invoke_graph(
            prompt, config, workdir=workdir, target_repo=target_repo, repo_url=repo_url,
        )

        # Summary
        click.echo("\n" + "=" * 70)
        click.echo("PIPELINE COMPLETE")
        click.echo("=" * 70)

        run_status = final_state.get("run_status", "unknown")
        if run_status == "succeeded":
            click.echo("\n✅ Success!")
            shipping = final_state.get("shipping_result", {})
            if isinstance(shipping, dict) and shipping.get("repo_url"):
                click.echo(f"   Repo: {shipping['repo_url']}")
                click.echo(f"   Commit: {shipping.get('commit_sha', 'N/A')}")
            click.echo(f"   Local workdir: {workdir}")
        else:
            click.echo(f"\n⚠️  Pipeline ended with status: {run_status}")
            click.echo(f"   Local workdir: {workdir}")

        if not no_studio:
            click.echo(f"\n📊 Studio still running: {studio_url}")
            click.echo("   Press Ctrl+C to stop.")
            try:
                studio_process.wait()
            except KeyboardInterrupt:
                pass

    finally:
        if studio_process:
            studio_process.terminate()
            studio_process.wait()


def _prepare_workspace(
    prompt: str,
    *,
    repo: str | None,
    skip_github: bool,
    private: bool,
) -> tuple[str, str | None, str | None]:
    """Prepare a target workdir + optional GitHub repo for generated artifacts.

    Returns (workdir, target_repo, repo_url).

    Behavior:
    - Derive a slug from prompt if --repo not given.
    - Workdir is always ``~/flowforge-workspace/<name>/``.
    - If --skip-github, just initialize a local git repo (no remote).
    - Otherwise, attempt ``gh repo create --clone`` into the workdir. If the
      repo already exists or creation fails, fall back to local init.
    - Always check out a fresh feature branch ``flowforge/run-<timestamp>``
      so every run produces a PR rather than pushing directly to ``main``.
    """
    import subprocess
    from datetime import datetime
    from pathlib import Path

    from flowforge.nodes._workspace import slugify

    name = repo or slugify(prompt)
    workspace_root = Path.home() / "flowforge-workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    workdir = workspace_root / name
    branch_name = f"flowforge/run-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    target_repo: str | None = name
    repo_url: str | None = None

    # If workdir already exists and is a git repo, reuse it
    if workdir.exists() and (workdir / ".git").exists():
        click.echo(f"  ↻ Reusing existing workdir: {workdir}")
        # Detect remote URL if present
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=str(workdir), capture_output=True, text=True, check=True,
            )
            repo_url = result.stdout.strip() or None
        except subprocess.CalledProcessError:
            repo_url = None
        _checkout_run_branch(workdir, branch_name)
        return (str(workdir), target_repo, repo_url)

    if skip_github:
        # Local-only mode
        workdir.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                ["git", "init", "-b", "main"],
                cwd=str(workdir), capture_output=True, check=True,
            )
            click.echo(f"  ✓ Initialized local git repo at {workdir}")
        except (subprocess.CalledProcessError, FileNotFoundError):
            click.echo(f"  ⚠️  git init failed at {workdir}")
        _checkout_run_branch(workdir, branch_name)
        return (str(workdir), target_repo, None)

    # Try to create on GitHub and clone into workdir
    visibility = "--private" if private else "--public"
    try:
        result = subprocess.run(
            ["gh", "repo", "create", name, visibility, "--clone"],
            cwd=str(workspace_root), capture_output=True, text=True, check=True,
        )
        # gh prints the URL on stdout
        for line in (result.stdout + result.stderr).splitlines():
            line = line.strip()
            if line.startswith("https://github.com/"):
                repo_url = line
                break
        click.echo(f"  ✓ Created GitHub repo {name}" + (f" → {repo_url}" if repo_url else ""))
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        if "already exists" in stderr.lower() or "name already exists" in stderr.lower():
            # Repo already exists — try cloning instead
            try:
                subprocess.run(
                    ["gh", "repo", "clone", name, name],
                    cwd=str(workspace_root), capture_output=True, text=True, check=True,
                )
                # Get the URL
                result = subprocess.run(
                    ["git", "remote", "get-url", "origin"],
                    cwd=str(workdir), capture_output=True, text=True, check=True,
                )
                repo_url = result.stdout.strip() or None
                click.echo(f"  ↻ Cloned existing repo {name}")
            except subprocess.CalledProcessError as clone_exc:
                click.echo(f"  ⚠️  Failed to clone existing repo: {clone_exc.stderr}")
                workdir.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["git", "init", "-b", "main"],
                    cwd=str(workdir), capture_output=True, check=True,
                )
        else:
            click.echo(f"  ⚠️  gh repo create failed ({stderr[:100]}); falling back to local repo")
            workdir.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "init", "-b", "main"],
                cwd=str(workdir), capture_output=True, check=True,
            )
    except FileNotFoundError:
        click.echo("  ⚠️  gh CLI not found; creating local repo only")
        workdir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=str(workdir), capture_output=True, check=True,
        )

    _bootstrap_main_branch(workdir)
    _checkout_run_branch(workdir, branch_name)
    return (str(workdir), target_repo, repo_url)


def _bootstrap_main_branch(workdir: Path) -> None:
    """Ensure the remote has a ``main`` branch with at least one commit.

    Fresh ``gh repo create`` repos are completely empty, so a PR cannot be
    opened against ``main`` until that branch exists. This makes a single
    empty commit on ``main`` and pushes it (best-effort), so the feature
    branch we're about to create has a base to PR against.
    """
    import subprocess

    cwd = str(workdir)
    if not (workdir / ".git").exists():
        return
    # Skip if there's already a commit on this branch
    try:
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd, capture_output=True, check=True,
        )
        already_has_commits = True
    except subprocess.CalledProcessError:
        already_has_commits = False

    try:
        if not already_has_commits:
            subprocess.run(
                ["git", "commit", "--allow-empty", "-m", "chore: initial commit", "--quiet"],
                cwd=cwd, capture_output=True, check=True,
            )
        # Best-effort push of main so the remote default branch exists.
        subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=cwd, capture_output=True, check=False,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass


def _checkout_run_branch(workdir: Path, branch_name: str) -> None:
    """Create and check out a fresh feature branch for this run.

    All artifact / docs commits emitted by the pipeline land on this branch
    so ``ship_node`` can push it and open a PR instead of mutating ``main``.
    """
    import subprocess

    if not (workdir / ".git").exists():
        return
    try:
        subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=str(workdir), capture_output=True, check=True,
        )
        click.echo(f"  ✓ Created feature branch {branch_name}")
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Branch already exists or git unavailable — best effort
        pass


def _write_env_file(config: FlowForgeConfig) -> None:
    """Write .env with LLM credentials for langgraph dev to use."""
    import os
    from pathlib import Path

    env_dir = Path.home() / ".flowforge"
    env_dir.mkdir(parents=True, exist_ok=True)
    env_path = env_dir / ".env"
    env_vars: dict[str, str] = {}

    # Read existing .env if present
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, _, val = line.partition("=")
                env_vars[key.strip()] = val.strip()

    # Set model config
    env_vars["OPENAI_API_BASE"] = config.api_base
    env_vars["OPENAI_MODEL"] = config.model

    # Set API key
    if config.provider == "copilot":
        from flowforge.auth.copilot import CopilotAuthError, ensure_oauth_token

        try:
            env_vars["OPENAI_API_KEY"] = ensure_oauth_token(interactive=False)
        except CopilotAuthError:
            click.echo(
                "❌ No Copilot OAuth token found. Run: swe-forge copilot-login"
            )
            raise SystemExit(1) from None
    elif config.provider == "codex":
        key = os.environ.get("OPENAI_API_KEY", "")
        if not key:
            click.echo("❌ OPENAI_API_KEY not set")
            raise SystemExit(1)
        env_vars["OPENAI_API_KEY"] = key
    elif config.provider == "claude_code":
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            click.echo("❌ ANTHROPIC_API_KEY not set")
            raise SystemExit(1)
        env_vars["OPENAI_API_KEY"] = key

    # LangSmith tracing — propagate from environment if user has it set
    langsmith_key = os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY")
    if langsmith_key:
        env_vars["LANGSMITH_API_KEY"] = langsmith_key
        env_vars["LANGSMITH_TRACING"] = "true"
        env_vars.setdefault("LANGSMITH_PROJECT", "flowforge")
    elif "LANGSMITH_API_KEY" not in env_vars:
        click.echo(
            "ℹ️  LangSmith tracing disabled (set LANGSMITH_API_KEY env var to enable)",
        )

    # Write .env
    lines = [f"{k}={v}" for k, v in env_vars.items()]
    env_path.write_text("\n".join(lines) + "\n")
    os.chmod(env_path, 0o600)


def _wait_for_server(port: int, timeout: int = 30) -> None:
    """Wait for the LangGraph API server to become ready."""
    import time
    import urllib.request

    url = f"http://localhost:{port}/ok"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = urllib.request.urlopen(url, timeout=2)
            if resp.status == 200:
                click.echo("  ✓ LangGraph server ready\n")
                return
        except Exception:
            time.sleep(1)
    click.echo("  ⚠️  Server took too long to start, proceeding anyway...\n")


def _truncate(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _invoke_graph(
    prompt: str,
    config: FlowForgeConfig,
    *,
    workdir: str,
    target_repo: str | None,
    repo_url: str | None,
) -> dict:
    """Invoke the graph via LangGraph SDK client — this makes Studio show execution live."""
    from langgraph_sdk import get_sync_client

    client = get_sync_client(url=f"http://localhost:{config.langgraph_port}")

    # Build initial state input
    input_state = {
        "request": prompt,
        "run_status": "running",
        "auto_clarify": True,
        "workdir": workdir,
        "target_repo": target_repo,
        "repo_url": repo_url,
    }

    # Stream the graph execution so we can show progress
    click.echo("  Invoking graph... (watch Studio for live visualization)\n")

    import threading
    import time

    # Heartbeat: print "  ⏳ <node> running... Ns" every 5s while we wait
    current_node = {"name": None, "start": 0.0}
    stop_heartbeat = threading.Event()

    def _heartbeat() -> None:
        while not stop_heartbeat.wait(5.0):
            name = current_node["name"]
            if name:
                elapsed = int(time.monotonic() - current_node["start"])
                click.echo(f"     ⏳ {name} running... {elapsed}s")

    hb_thread = threading.Thread(target=_heartbeat, daemon=True)
    hb_thread.start()

    def _summarize(node_name: str, payload: dict) -> list[str]:
        """Multi-line summary of what a node produced. Returns lines (without leading indent)."""
        if not isinstance(payload, dict):
            return []
        lines: list[str] = []

        # --- clarification_node ---
        if node_name == "clarification_node":
            clarified = payload.get("clarified_request") or {}
            if isinstance(clarified, dict):
                text = clarified.get("clarified_text") or ""
                if text:
                    lines.append(f"clarified: {_truncate(text, 100)}")
            transcript = payload.get("clarification_transcript") or {}
            qa = transcript.get("qa_pairs") if isinstance(transcript, dict) else None
            if qa:
                lines.append(f"resolved {len(qa)} dimensions in one shot")
            ambig = payload.get("ambiguity_status") or {}
            if isinstance(ambig, dict) and ambig.get("unresolved_dimensions"):
                lines.append(f"unresolved: {', '.join(ambig['unresolved_dimensions'][:3])}")

        # --- spec_node ---
        if node_name == "spec_node":
            spec = payload.get("spec") or {}
            if isinstance(spec, dict):
                if spec.get("summary"):
                    lines.append(f"summary: {_truncate(spec['summary'], 100)}")
                ac = spec.get("acceptance_criteria") or []
                tech = spec.get("tech_stack") or []
                meta = []
                if ac:
                    meta.append(f"{len(ac)} acceptance criteria")
                if tech:
                    meta.append(f"stack: {', '.join(tech[:4])}")
                if meta:
                    lines.append(" | ".join(meta))
                if spec.get("artifact_path"):
                    lines.append(f"wrote: {spec['artifact_path']}")

        # --- plan_node ---
        if node_name == "plan_node":
            plan = payload.get("implementation_plan") or {}
            if isinstance(plan, dict):
                phases = plan.get("phases") or []
                dag = plan.get("dag") or {}
                tasks = dag.get("tasks") if isinstance(dag, dict) else []
                edges = dag.get("edges") if isinstance(dag, dict) else []
                bits = []
                if phases:
                    bits.append(f"{len(phases)} phases: {', '.join(phases[:3])}")
                if tasks is not None:
                    bits.append(f"{len(tasks)} tasks")
                if edges:
                    bits.append(f"{len(edges)} deps")
                if bits:
                    lines.append(" | ".join(bits))

        # --- task fanout / task ---
        if node_name in ("task_fanout_router", "task_node"):
            tasks = payload.get("tasks") or []
            if isinstance(tasks, list) and tasks:
                statuses: dict[str, int] = {}
                artifact_count = 0
                for t in tasks:
                    if isinstance(t, dict):
                        s = t.get("status", "?")
                        statuses[s] = statuses.get(s, 0) + 1
                        arts = t.get("artifacts") or []
                        if isinstance(arts, list):
                            artifact_count += len(arts)
                lines.append(
                    f"{len(tasks)} tasks: "
                    + ", ".join(f"{k}={v}" for k, v in sorted(statuses.items()))
                    + (f" | {artifact_count} artifacts" if artifact_count else "")
                )

        # --- quality gate nodes ---
        for findings_key, label in (
            ("review_findings", "review"),
            ("security_findings", "security"),
            ("test_findings", "test"),
        ):
            if findings_key in payload:
                findings = payload[findings_key] or []
                if isinstance(findings, list):
                    by_sev: dict[str, int] = {}
                    for f in findings:
                        if isinstance(f, dict):
                            sev = f.get("severity", "?")
                            by_sev[sev] = by_sev.get(sev, 0) + 1
                    if findings:
                        sev_str = ", ".join(f"{k}={v}" for k, v in sorted(by_sev.items()))
                        lines.append(f"{label}: {len(findings)} findings ({sev_str})")
                    else:
                        lines.append(f"{label}: 0 findings")

        # --- issue_orchestrator_node ---
        if node_name == "issue_orchestrator_node":
            issues = payload.get("triaged_issues") or []
            if isinstance(issues, list):
                by_disp: dict[str, int] = {}
                for i in issues:
                    if isinstance(i, dict):
                        d = i.get("disposition", "?")
                        by_disp[d] = by_disp.get(d, 0) + 1
                lines.append(
                    f"{len(issues)} triaged: "
                    + (", ".join(f"{k}={v}" for k, v in sorted(by_disp.items())) or "—")
                )

        # --- ship_node ---
        if node_name == "ship_node":
            result = payload.get("shipping_result") or {}
            if isinstance(result, dict):
                bits = [f"shipped={result.get('shipped', False)}"]
                if result.get("commit_sha"):
                    bits.append(f"commit={result['commit_sha'][:8]}")
                if result.get("release_url"):
                    bits.append(f"release={result['release_url']}")
                provenance = result.get("provenance_chain") or []
                push_status = next(
                    (p.split(":", 1)[1] for p in provenance if isinstance(p, str) and p.startswith("push:")),
                    None,
                )
                if push_status:
                    bits.append(f"push={push_status}")
                lines.append(" | ".join(bits))

        # --- generic fallback: surface run_status changes ---
        if "run_status" in payload and not lines:
            lines.append(f"status={payload['run_status']}")

        return lines

    def _finish_node() -> None:
        name = current_node["name"]
        if name:
            elapsed = time.monotonic() - current_node["start"]
            click.echo(f"     ✓ {name} done in {elapsed:.1f}s")
        current_node["name"] = None

    def _snapshot_files() -> dict[str, float]:
        """Map of relative file path → mtime for everything under workdir (excluding .git)."""
        snap: dict[str, float] = {}
        root = Path(workdir)
        if not root.exists():
            return snap
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            try:
                rel = p.relative_to(root)
            except ValueError:
                continue
            parts = rel.parts
            if parts and (parts[0] == ".git" or parts[0].startswith(".langgraph")):
                continue
            try:
                snap[str(rel)] = p.stat().st_mtime
            except OSError:
                pass
        return snap

    def _diff_files(before: dict[str, float], after: dict[str, float]) -> tuple[list[str], list[str]]:
        added = sorted(p for p in after if p not in before)
        modified = sorted(p for p in after if p in before and after[p] != before[p])
        return added, modified

    file_snapshot = _snapshot_files()

    # Use stream to get node-by-node updates (thread_id=None creates a new thread)
    events = []
    final_state: dict = {}
    error_seen = False
    try:
        for chunk in client.runs.stream(
            None,
            assistant_id="flowforge",
            input=input_state,
            stream_mode="updates",
        ):
            event = getattr(chunk, "event", None)
            data = getattr(chunk, "data", None)
            if event == "updates" and data:
                # Diff filesystem against pre-node snapshot BEFORE finishing the node line
                new_snapshot = _snapshot_files()
                added, modified = _diff_files(file_snapshot, new_snapshot)
                for f in added:
                    click.echo(f"     + {f}")
                for f in modified:
                    click.echo(f"     ~ {f}")
                file_snapshot = new_snapshot

                _finish_node()
                node_name = list(data.keys())[0]
                node_payload = data[node_name]
                click.echo(f"  ━━━ Node: {node_name} ━━━")
                if isinstance(node_payload, dict):
                    for summary_line in _summarize(node_name, node_payload):
                        click.echo(f"     · {summary_line}")
                current_node["name"] = node_name
                current_node["start"] = time.monotonic()
                events.append(data)
                # Accumulate node outputs into final_state
                for payload in data.values():
                    if isinstance(payload, dict):
                        final_state.update(payload)
            elif event == "values" and isinstance(data, dict):
                final_state.update(data)
            elif event in ("error", "messages/partial"):
                # Surface errors instead of swallowing them
                click.echo(f"  ⚠️  {event}: {data}")
                error_seen = True
            elif event == "end":
                break
    finally:
        # Final diff after the last node
        new_snapshot = _snapshot_files()
        added, modified = _diff_files(file_snapshot, new_snapshot)
        for f in added:
            click.echo(f"     + {f}")
        for f in modified:
            click.echo(f"     ~ {f}")
        _finish_node()
        stop_heartbeat.set()

    if error_seen:
        click.echo("\n  (graph stream reported an error — pipeline may be incomplete)")

    return final_state


def _start_studio(config: FlowForgeConfig) -> subprocess.Popen | None:  # type: ignore[type-arg]
    """Start LangGraph dev server in background."""
    import shutil
    import subprocess
    import sys

    # Prefer the langgraph binary that ships next to the active
    # interpreter (covers `python -m`, editable installs, and venvs
    # whose bin/ isn't on PATH); fall back to PATH lookup. Don't
    # resolve symlinks — venv pythons symlink to the system interpreter
    # but the venv's bin/ is what we want.
    candidate = Path(sys.executable).parent / "langgraph"
    langgraph_bin = str(candidate) if candidate.exists() else shutil.which("langgraph")
    if not langgraph_bin:
        return None

    click.echo(f"🚀 Starting LangGraph server on port {config.langgraph_port}...")

    flowforge_dir = Path.home() / ".flowforge"
    flowforge_dir.mkdir(parents=True, exist_ok=True)
    config_path = flowforge_dir / "langgraph.json"
    config_path.write_text(json.dumps({
        "dependencies": ["swe-forge"],
        "graphs": {
            "flowforge": "flowforge.graph.builder:build_live_graph",
        },
        "env": str(flowforge_dir / ".env"),
    }, indent=2))

    log_path = flowforge_dir / "langgraph-dev.log"
    log_handle = log_path.open("w")
    click.echo(f"   (server logs → {log_path})")

    # Ensure the venv's bin/ is on PATH for the dev server and every
    # subprocess it spawns. Without this, agentic tools that shell out
    # to `pytest` / `ruff` / `mypy` raise FileNotFoundError when the CLI
    # was launched via the venv binary directly (no `activate`).
    venv_bin = str(Path(sys.executable).parent)
    child_env = {**os.environ}
    existing_path = child_env.get("PATH", "")
    if venv_bin not in existing_path.split(os.pathsep):
        child_env["PATH"] = venv_bin + (os.pathsep + existing_path if existing_path else "")

    process = subprocess.Popen(
        [
            langgraph_bin, "dev",
            "--config", str(config_path),
            "--port", str(config.langgraph_port),
            "--no-browser",
            "--no-reload",
            "--allow-blocking",
        ],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        cwd=str(flowforge_dir),
        env=child_env,
    )

    import time

    # Poll the port for up to 15s; the in-memory server typically binds
    # within ~1s but cold-import of the graph module can take longer.
    deadline = time.monotonic() + 15.0
    import socket as _socket

    while time.monotonic() < deadline:
        if process.poll() is not None:
            click.echo("⚠️  LangGraph server failed to start.\n")
            click.echo(f"   Check log: {log_path}\n")
            return None
        with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
            s.settimeout(0.25)
            if s.connect_ex(("127.0.0.1", config.langgraph_port)) == 0:
                return process
        time.sleep(0.25)

    click.echo("⚠️  LangGraph server did not become ready within 15s.\n")
    click.echo(f"   Check log: {log_path}\n")
    return None


if __name__ == "__main__":
    cli()
