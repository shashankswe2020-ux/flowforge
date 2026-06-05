"""DAG acyclicity validator using topological sort (Kahn's algorithm)."""

from __future__ import annotations

from collections import deque

from flowforge.state.models import TaskDAG


class CyclicDAGError(Exception):
    """Raised when a task DAG contains a cycle."""

    def __init__(self, cycle_nodes: list[str] | None = None) -> None:
        self.cycle_nodes = cycle_nodes or []
        nodes_info = f" involving nodes: {self.cycle_nodes}" if self.cycle_nodes else ""
        super().__init__(
            f"Task DAG contains a cycle{nodes_info}. "
            "All task dependencies must form an acyclic graph.",
        )


def validate_dag(dag: TaskDAG) -> None:
    """Validate that a TaskDAG is acyclic.

    Uses Kahn's algorithm (BFS topological sort) to detect cycles.

    Raises:
        CyclicDAGError: If the DAG contains any cycle.
    """
    task_ids = {t.task_id for t in dag.tasks}

    # Build adjacency list and in-degree map
    in_degree: dict[str, int] = dict.fromkeys(task_ids, 0)
    adjacency: dict[str, list[str]] = {tid: [] for tid in task_ids}

    for edge in dag.edges:
        adjacency[edge.from_task_id].append(edge.to_task_id)
        in_degree[edge.to_task_id] = in_degree.get(edge.to_task_id, 0) + 1

    # Kahn's algorithm
    queue: deque[str] = deque(tid for tid, deg in in_degree.items() if deg == 0)
    visited_count = 0

    while queue:
        node = queue.popleft()
        visited_count += 1
        for neighbor in adjacency[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if visited_count != len(task_ids):
        # Nodes remaining with in_degree > 0 are in cycles
        cycle_nodes = [tid for tid, deg in in_degree.items() if deg > 0]
        raise CyclicDAGError(cycle_nodes=cycle_nodes)
