"""FlowForge CLI — main entry point.

Usage:
    flowforge setup              # Configure provider and model
    flowforge "Build a web app"  # Run pipeline with prompt
    flowforge "idea" --repo name # Run and push to GitHub repo
"""

from __future__ import annotations

from pathlib import Path

import click

from src.cli.config import CONFIG_DIR, FlowForgeConfig


class FlowForgeGroup(click.Group):
    """Custom group that resolves subcommands before consuming the prompt argument.

    This allows `flowforge "prompt"` to work as a shortcut for `flowforge run "prompt"`,
    while `flowforge setup` correctly routes to the setup subcommand.
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


@click.group(cls=FlowForgeGroup)
def cli() -> None:
    """FlowForge — AI-powered code generation pipeline.

    Run with a prompt to generate code:

        flowforge "Build a REST API with FastAPI"

    Or use subcommands:

        flowforge setup    Configure provider and model
    """


@cli.command()
@click.argument("prompt")
@click.option("--repo", help="GitHub repo name (creates private repo if missing)")
@click.option("--skip-github", is_flag=True, help="Generate files locally only")
@click.option("--no-studio", is_flag=True, help="Skip LangGraph Studio visualization")
def run(prompt: str, repo: str | None, skip_github: bool, no_studio: bool) -> None:
    """Generate code from a prompt.

    Examples:

        flowforge "Build a REST API with FastAPI"

        flowforge run "Build a CLI tool" --repo my-tool
    """
    # Ensure setup has been run
    if not FlowForgeConfig.exists():
        click.echo("⚠️  FlowForge not configured yet. Running setup first...\n")
        _do_setup()
        click.echo()

    _run_pipeline(prompt, repo=repo, skip_github=skip_github, no_studio=no_studio)


@cli.command()
def setup() -> None:
    """Interactive setup — configure provider, model, and preferences."""
    _do_setup()


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
        config.api_base = "https://models.inference.ai.azure.com"
        click.echo(f"\n  Using GitHub Models API at {config.api_base}")
        click.echo("  Authentication: `gh auth token` (ensure `gh` CLI is logged in)")
        model = click.prompt(
            "  Model",
            default="gpt-4o-mini",
            type=click.Choice(["gpt-4o-mini", "gpt-4o", "o1-mini", "o1-preview"]),
        )
        config.model = model
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

    from src.cli.config import FlowForgeConfig

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
    """
    import subprocess
    from pathlib import Path

    from src.nodes._workspace import slugify

    name = repo or slugify(prompt)
    workspace_root = Path.home() / "flowforge-workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    workdir = workspace_root / name

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

    return (str(workdir), target_repo, repo_url)


def _write_env_file(config: FlowForgeConfig) -> None:
    """Write .env with LLM credentials for langgraph dev to use."""
    import os
    import subprocess
    from pathlib import Path

    env_path = Path.cwd() / ".env"
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
        try:
            result = subprocess.run(
                ["gh", "auth", "token"], capture_output=True, text=True, check=True
            )
            env_vars["OPENAI_API_KEY"] = result.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            click.echo("❌ Failed to get GitHub token. Run: gh auth login")
            raise SystemExit(1)
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
                for t in tasks:
                    if isinstance(t, dict):
                        s = t.get("status", "?")
                        statuses[s] = statuses.get(s, 0) + 1
                lines.append(
                    f"{len(tasks)} tasks: "
                    + ", ".join(f"{k}={v}" for k, v in sorted(statuses.items()))
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
            ready = payload.get("shipping_readiness") or {}
            if isinstance(ready, dict):
                blockers = ready.get("blockers") or []
                lines.append(f"shipping_ready={ready.get('is_ready', False)} ({len(blockers)} blockers)")

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

    if not shutil.which("langgraph"):
        return None

    click.echo(f"🚀 Starting LangGraph server on port {config.langgraph_port}...")

    log_path = Path.home() / ".flowforge" / "langgraph-dev.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("w")
    click.echo(f"   (server logs → {log_path})")

    process = subprocess.Popen(
        [
            "langgraph", "dev",
            "--port", str(config.langgraph_port),
            "--no-browser",
            "--no-reload",
        ],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )

    import time
    time.sleep(2)

    if process.poll() is not None:
        click.echo("⚠️  LangGraph server failed to start.\n")
        return None

    return process


if __name__ == "__main__":
    cli()
