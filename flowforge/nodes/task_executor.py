"""Task executor — implements tasks using TDD cycle from the build agent.

Follows the build agent workflow:
1. Pick the task and load context
2. TDD cycle: RED (failing test) → GREEN (minimal code) → REFACTOR
3. Verify (all acceptance checks pass)
4. Produce artifacts (source + test files)

Routes to capability-specific executors based on task type,
enforces schema validation, and ensures sole-writer semantics.
"""

from __future__ import annotations

from src.nodes.capability import (
    AgentOnlyExecutor,
    AgentWithToolsExecutor,
    DirectToolExecutor,
    LLMProtocol,
    TaskExecutionResult,
)
from src.state.models import CapabilityType, Task


def execute_task(
    task: Task,
    *,
    llm: LLMProtocol | None = None,
) -> TaskExecutionResult:
    """Execute a task using the appropriate capability executor.

    Dispatches based on task.definition.capability_type:
    - AGENT_ONLY → AgentOnlyExecutor (requires LLM)
    - AGENT_WITH_TOOLS → AgentWithToolsExecutor (requires LLM)
    - DIRECT_TOOL → DirectToolExecutor (no LLM needed)

    Returns:
        TaskExecutionResult with status, artifacts, evidence, and idempotency key.
    """
    capability = task.definition.capability_type

    if capability == CapabilityType.AGENT_ONLY:
        executor = AgentOnlyExecutor()
        return executor.execute(task, llm=llm)
    if capability == CapabilityType.AGENT_WITH_TOOLS:
        executor_tools = AgentWithToolsExecutor()
        return executor_tools.execute(task, llm=llm)
    # DIRECT_TOOL
    direct_executor = DirectToolExecutor()
    return direct_executor.execute(task, llm=None)
