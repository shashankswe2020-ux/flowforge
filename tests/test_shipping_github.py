"""Tests for GitHub shipping integration."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from flowforge.shipping.github import (
    GitHubResult,
    GitHubShipError,
    check_gh_auth,
    compute_file_fingerprint,
    create_repo,
    get_repo_url,
    init_and_commit,
    repo_exists,
    ship_to_github,
    write_files,
)


class TestWriteFiles:
    def test_writes_files_to_disk(self, tmp_path: Path) -> None:
        files = {
            "main.py": "print('hello')",
            "lib/utils.py": "def helper(): pass",
        }
        written = write_files(tmp_path, files)
        assert set(written) == {"main.py", "lib/utils.py"}
        assert (tmp_path / "main.py").read_text() == "print('hello')"
        assert (tmp_path / "lib/utils.py").read_text() == "def helper(): pass"

    def test_creates_nested_directories(self, tmp_path: Path) -> None:
        files = {"a/b/c/deep.txt": "content"}
        write_files(tmp_path, files)
        assert (tmp_path / "a" / "b" / "c" / "deep.txt").exists()


class TestComputeFileFingerprint:
    def test_deterministic(self) -> None:
        fp1 = compute_file_fingerprint("hello")
        fp2 = compute_file_fingerprint("hello")
        assert fp1 == fp2

    def test_different_content_different_fingerprint(self) -> None:
        fp1 = compute_file_fingerprint("hello")
        fp2 = compute_file_fingerprint("world")
        assert fp1 != fp2

    def test_returns_16_char_hex(self) -> None:
        fp = compute_file_fingerprint("test")
        assert len(fp) == 16
        assert all(c in "0123456789abcdef" for c in fp)


class TestCheckGhAuth:
    @patch("flowforge.shipping.github._run")
    def test_returns_true_when_authenticated(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        assert check_gh_auth() is True

    @patch("flowforge.shipping.github._run")
    def test_returns_false_on_error(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = GitHubShipError("not logged in")
        assert check_gh_auth() is False


class TestRepoExists:
    @patch("subprocess.run")
    def test_returns_true_for_existing_repo(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        assert repo_exists("owner/repo") is True

    @patch("subprocess.run")
    def test_returns_false_for_missing_repo(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1)
        assert repo_exists("owner/repo") is False


class TestCreateRepo:
    @patch("flowforge.shipping.github._run")
    def test_creates_private_repo(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            stdout=json.dumps({"url": "https://github.com/owner/repo"})
        )
        url = create_repo("owner/repo", private=True)
        assert url == "https://github.com/owner/repo"
        # create_repo invokes _run twice: `gh repo create ... --private`
        # and then `gh repo view ... --json url`. Assert the create call
        # carried the --private flag.
        argv_history = [call.args[0] for call in mock_run.call_args_list]
        create_argv = next(a for a in argv_history if a[:3] == ["gh", "repo", "create"])
        assert "--private" in create_argv


class TestGetRepoUrl:
    @patch("flowforge.shipping.github._run")
    def test_returns_url(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            stdout=json.dumps({"url": "https://github.com/owner/repo"})
        )
        url = get_repo_url("owner/repo")
        assert url == "https://github.com/owner/repo"


class TestInitAndCommit:
    @patch("flowforge.shipping.github._run")
    def test_returns_commit_sha(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout="abc123def456\n")
        sha = init_and_commit(Path("/tmp/test"))
        assert sha == "abc123def456"
        # Should have called git init, add, commit, rev-parse
        assert mock_run.call_count == 4


class TestShipToGithub:
    @patch("flowforge.shipping.github.push_to_remote")
    @patch("flowforge.shipping.github.init_and_commit")
    @patch("flowforge.shipping.github.repo_exists")
    @patch("flowforge.shipping.github.create_repo")
    @patch("flowforge.shipping.github.check_gh_auth")
    def test_creates_repo_and_ships(
        self,
        mock_auth: MagicMock,
        mock_create: MagicMock,
        mock_exists: MagicMock,
        mock_commit: MagicMock,
        mock_push: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_auth.return_value = True
        mock_exists.return_value = False
        mock_create.return_value = "https://github.com/user/test-repo"
        mock_commit.return_value = "abc123"

        # Create a test file
        (tmp_path / "main.py").write_text("print('hello')")

        result = ship_to_github(tmp_path, repo_name="test-repo")
        assert result.repo_url == "https://github.com/user/test-repo"
        assert result.commit_sha == "abc123"
        assert "main.py" in result.files_committed

    @patch("flowforge.shipping.github.check_gh_auth")
    def test_fails_without_auth(self, mock_auth: MagicMock, tmp_path: Path) -> None:
        mock_auth.return_value = False
        with pytest.raises(GitHubShipError, match="not authenticated"):
            ship_to_github(tmp_path, repo_name="test")
