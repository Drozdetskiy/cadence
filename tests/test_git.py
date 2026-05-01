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
