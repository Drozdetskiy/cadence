from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from cadence.git import ExternalBackend

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


class TestExternalBackendRepoValidation:
    def test_real_repo(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path)
        be = ExternalBackend(path)
        assert os.path.realpath(path) == be.root()

    def test_not_a_repo_raises(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError):
            ExternalBackend(str(tmp_path))


class TestExternalBackendHeadHash:
    def test_returns_hash(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path)
        _commit(path)
        h = ExternalBackend(path).head_hash()
        assert len(h) == 40
        assert all(c in "0123456789abcdef" for c in h)

    def test_no_commits_returns_empty(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path)
        assert ExternalBackend(path).head_hash() == ""


class TestExternalBackendGetDefaultBranch:
    def test_with_main_branch(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _commit(path)
        assert ExternalBackend(path).get_default_branch() == "main"

    def test_with_master_branch(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="master")
        _commit(path)
        assert ExternalBackend(path).get_default_branch() == "master"

    def test_fallback_to_master(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="feature")
        assert ExternalBackend(path).get_default_branch() == "master"


class TestExternalBackendRunErrors:
    def test_run_raises_with_message_format_on_nonzero_exit(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path)
        be = ExternalBackend(path)
        with pytest.raises(RuntimeError) as excinfo:
            be._run("checkout", "no-such-branch")
        msg = str(excinfo.value)
        assert msg.startswith("git checkout no-such-branch failed (exit ")
        assert "): " in msg


def _git(path: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", path, *args],
        capture_output=True,
        text=True,
        check=True,
        env=_git_env(),
    )


class TestExternalBackendMergeBase:
    def test_returns_base_sha(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _commit(path, filename="a.txt", content="a")
        be = ExternalBackend(path)
        base_hash = be.head_hash()
        _git(path, "checkout", "-b", "feature")
        (tmp_path / "b.txt").write_text("b")
        _git(path, "add", "b.txt")
        _git(path, "commit", "-m", "b")
        assert be.merge_base("main") == base_hash

    def test_missing_ref_returns_empty(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path)
        _commit(path)
        be = ExternalBackend(path)
        assert be.merge_base("nope-missing") == ""


class TestExternalBackendCommitsAhead:
    def test_zero_when_same(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _commit(path)
        be = ExternalBackend(path)
        assert be.commits_ahead("main") == 0

    def test_one_when_one_ahead(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _commit(path, filename="a.txt", content="a")
        _git(path, "checkout", "-b", "feature")
        (tmp_path / "b.txt").write_text("b")
        _git(path, "add", "b.txt")
        _git(path, "commit", "-m", "b")
        be = ExternalBackend(path)
        assert be.commits_ahead("main") == 1

    def test_n_when_n_ahead(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _commit(path, filename="a.txt", content="a")
        _git(path, "checkout", "-b", "feature")
        for i, name in enumerate(("b.txt", "c.txt", "d.txt")):
            (tmp_path / name).write_text(str(i))
            _git(path, "add", name)
            _git(path, "commit", "-m", name)
        be = ExternalBackend(path)
        assert be.commits_ahead("main") == 3

    def test_unknown_ref_returns_zero(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path)
        _commit(path)
        be = ExternalBackend(path)
        assert be.commits_ahead("nope-missing") == 0


class TestExternalBackendDiffAgainst:
    def test_returns_diff(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _commit(path, filename="a.txt", content="a\n")
        _git(path, "checkout", "-b", "feature")
        (tmp_path / "a.txt").write_text("a\nb\n")
        _git(path, "add", "a.txt")
        _git(path, "commit", "-m", "more")
        be = ExternalBackend(path)
        diff = be.diff_against("main")
        assert "+b" in diff
        assert "a.txt" in diff

    def test_unknown_ref_returns_empty(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path)
        _commit(path)
        be = ExternalBackend(path)
        assert be.diff_against("nope-missing") == ""


class TestExternalBackendResetSoftAndCommit:
    def test_reset_soft_keeps_changes_staged(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _commit(path, filename="a.txt", content="a")
        _git(path, "checkout", "-b", "feature")
        (tmp_path / "b.txt").write_text("b")
        _git(path, "add", "b.txt")
        _git(path, "commit", "-m", "b")
        (tmp_path / "c.txt").write_text("c")
        _git(path, "add", "c.txt")
        _git(path, "commit", "-m", "c")
        be = ExternalBackend(path)
        base = be.merge_base("main")
        be.reset_soft(base)
        assert be.head_hash() == base
        staged = _git(path, "diff", "--cached", "--name-only").stdout.strip().splitlines()
        assert "b.txt" in staged
        assert "c.txt" in staged

    def test_commit_with_message_writes_one_commit(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _commit(path, filename="a.txt", content="a")
        _git(path, "checkout", "-b", "feature")
        (tmp_path / "b.txt").write_text("b")
        _git(path, "add", "b.txt")
        _git(path, "commit", "-m", "b")
        (tmp_path / "c.txt").write_text("c")
        _git(path, "add", "c.txt")
        _git(path, "commit", "-m", "c")
        be = ExternalBackend(path)
        base = be.merge_base("main")
        be.reset_soft(base)
        be.commit_with_message("squashed body")
        log = _git(path, "log", "--format=%s", f"{base}..HEAD").stdout.strip().splitlines()
        assert log == ["squashed body"]


class TestExternalBackendDiffFingerprint:
    def test_clean(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path)
        _commit(path)
        be = ExternalBackend(path)
        assert be.diff_fingerprint() == be.diff_fingerprint()

    def test_changes_when_modified(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path)
        _commit(path)
        be = ExternalBackend(path)
        before = be.diff_fingerprint()
        with open(os.path.join(path, "test.txt"), "w") as f:
            f.write("modified")
        assert be.diff_fingerprint() != before
