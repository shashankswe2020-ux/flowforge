"""Mock external services for testing (file system, git, network)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MockFileSystem:
    """In-memory file system mock for tool testing."""

    files: dict[str, str] = field(default_factory=dict)
    write_log: list[tuple[str, str]] = field(default_factory=list)

    def read(self, path: str) -> str | None:
        """Read file content. Returns None if not found."""
        return self.files.get(path)

    def write(self, path: str, content: str) -> None:
        """Write file content."""
        self.files[path] = content
        self.write_log.append((path, content))

    def exists(self, path: str) -> bool:
        """Check if file exists."""
        return path in self.files

    def list_dir(self, prefix: str) -> list[str]:
        """List files with given prefix."""
        return [p for p in self.files if p.startswith(prefix)]


@dataclass
class MockGitClient:
    """Mock git operations for testing."""

    commits: list[dict[str, str]] = field(default_factory=list)
    current_branch: str = "main"
    diff_output: str = ""

    def commit(self, message: str, files: list[str]) -> str:
        """Record a mock commit. Returns fake SHA."""
        sha = f"fake-sha-{len(self.commits):04d}"
        self.commits.append({"sha": sha, "message": message, "files": ",".join(files)})
        return sha

    def diff(self) -> str:
        """Return mock diff output."""
        return self.diff_output

    def get_branch(self) -> str:
        """Return current branch name."""
        return self.current_branch
