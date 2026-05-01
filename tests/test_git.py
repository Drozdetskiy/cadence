from __future__ import annotations

import os
import subprocess
from pathlib import Path

from cadence.git import GitChecker, get_default_branch, head_hash, is_git_repo

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def _git_env() -> dict[str, str]:
    return {**os.environ, **_GIT_ENV}


def _init_repo(path: str, branch: str = "main") -> None:
    subprocess.run(
        ["git", "init", "-b", branch, path],
        capture_output=True,
        check=True,
    )


def _commit(path: str, filename: str = "test.txt", content: str = "hello") -> None:
    test_file = os.path.join(path, filename)
    with open(test_file, "w") as f:
        f.write(content)
    subprocess.run(
        ["git", "-C", path, "add", "."],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", path, "commit", "-m", "init"],
        capture_output=True,
        check=True,
        env=_git_env(),
    )


class TestIsGitRepo:
    def test_real_repo(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path)
        assert is_git_repo(path) is True

    def test_not_a_repo(self, tmp_path: Path) -> None:
        assert is_git_repo(str(tmp_path)) is False


class TestHeadHash:
    def test_returns_hash(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path)
        _commit(path)
        h = head_hash(path)
        assert len(h) == 40
        assert all(c in "0123456789abcdef" for c in h)

    def test_no_commits_returns_empty(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path)
        assert head_hash(path) == ""


class TestGetDefaultBranch:
    def test_with_main_branch(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _commit(path)
        assert get_default_branch(path) == "main"

    def test_with_master_branch(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="master")
        _commit(path)
        assert get_default_branch(path) == "master"

    def test_fallback_to_master(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="feature")
        assert get_default_branch(path) == "master"


class TestGitChecker:
    def test_head_hash(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path)
        _commit(path)
        checker = GitChecker(path)
        assert len(checker.head_hash()) == 40

    def test_diff_fingerprint_clean(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path)
        _commit(path)
        checker = GitChecker(path)
        assert checker.diff_fingerprint() == ""

    def test_diff_fingerprint_dirty(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path)
        _commit(path)
        with open(os.path.join(path, "test.txt"), "w") as f:
            f.write("modified")
        checker = GitChecker(path)
        assert checker.diff_fingerprint() != ""
