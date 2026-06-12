"""Manual smoke test for T14 acceptance §13.10.

Invokes the live ``flowforge`` graph on a running ``langgraph dev``
server and asserts every agentic node emits a ``DeepAgentTrace``.

Usage:
    python scripts/smoke_deep_agents.py [--prompt "..."] [--port 8124]

Requires ``langgraph dev`` already serving the ``flowforge`` graph.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from langgraph_sdk import get_sync_client

_AGENTIC_NODES = (
    "clarification_node",
    "spec_node",
    "plan_node",
    "task_node",
    "code_review_node",
    "security_audit_node",
    "test_engineer_node",
    "issue_orchestrator_node",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", default="build tic-tac-toe web app")
    parser.add_argument("--port", type=int, default=8124)
    parser.add_argument("--graph", default="flowforge")
    args = parser.parse_args()

    base_url = f"http://127.0.0.1:{args.port}"
    print(f"→ connecting to {base_url}")
    client = get_sync_client(url=base_url)

    thread = client.threads.create()
    thread_id = thread["thread_id"]
    print(f"→ thread {thread_id}")
    print(f"→ invoking graph={args.graph!r} prompt={args.prompt!r}")

    run = client.runs.create(
        thread_id,
        args.graph,
        input={"request": args.prompt, "auto_clarify": True},
    )
    print(f"→ run {run['run_id']}; waiting...")
    client.runs.join(thread_id, run["run_id"])

    state = client.threads.get_state(thread_id)
    values: dict[str, Any] = state.get("values") or {}
    print(f"\n→ raw state keys: {sorted(state.keys())}")
    print(f"→ values keys: {sorted(values.keys()) if isinstance(values, dict) else type(values)}")
    if not isinstance(values, dict):
        # Pydantic schema sometimes keeps state as nested dict or list of msgs
        import json as _json
        print(_json.dumps(state, default=str, indent=2)[:2000])
        return 3
    traces = values.get("deep_agent_traces") or {}
    print(f"\n→ run_status: {values.get('run_status')}")
    print(f"→ deep_agent_traces keys: {sorted(traces)}")

    missing = [n for n in _AGENTIC_NODES if n not in traces]
    if missing:
        print(f"\n✗ missing DeepAgentTrace for: {missing}", file=sys.stderr)
        return 1

    print("\n✓ DeepAgentTrace recorded for all 8 agentic nodes")
    print(f"  Studio: https://smith.langchain.com/studio/?baseUrl={base_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
