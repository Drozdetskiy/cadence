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
        _git(path, "config", "user.email", "t@t")
        _git(path, "config", "user.name", "test")
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


class TestExternalBackendCreateBranchFrom:
    def test_creates_from_existing_local_base(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _commit(path, filename="a.txt", content="a")
        _git(path, "checkout", "-b", "side")
        (tmp_path / "b.txt").write_text("b")
        _git(path, "add", "b.txt")
        _git(path, "commit", "-m", "b")
        _git(path, "checkout", "main")
        be = ExternalBackend(path)
        be.create_branch_from("feature", "main")
        assert be.current_branch() == "feature"
        assert be.branch_exists("feature")

    def test_creates_from_origin_remote_base(self, tmp_path: Path) -> None:
        upstream = tmp_path / "upstream"
        upstream.mkdir()
        upstream_path = str(upstream)
        _init_repo(upstream_path, branch="main")
        _commit(upstream_path, filename="a.txt", content="a")

        clone = tmp_path / "clone"
        subprocess.run(
            ["git", "clone", upstream_path, str(clone)],
            capture_output=True,
            check=True,
        )
        clone_path = str(clone)
        _git(clone_path, "config", "user.email", "t@t")
        _git(clone_path, "config", "user.name", "test")

        be = ExternalBackend(clone_path)
        be.create_branch_from("feature", "origin/main")
        assert be.current_branch() == "feature"
        assert be.branch_exists("feature")

    def test_missing_base_raises(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _commit(path)
        be = ExternalBackend(path)
        with pytest.raises(RuntimeError) as excinfo:
            be.create_branch_from("feature", "no-such-base")
        assert "no-such-base" in str(excinfo.value)


class TestExternalBackendWorktrees:
    def test_worktree_add_creates_branch_and_directory(self, tmp_path: Path) -> None:
        path = str(tmp_path / "repo")
        os.makedirs(path)
        _init_repo(path, branch="main")
        _commit(path, filename="a.txt", content="a")
        be = ExternalBackend(path)
        wt = tmp_path / "wt-feature"
        be.worktree_add(str(wt), "feature", "main")
        assert wt.is_dir()
        assert (wt / "a.txt").exists()
        assert be.branch_exists("feature")

    def test_worktree_remove_keeps_branch(self, tmp_path: Path) -> None:
        path = str(tmp_path / "repo")
        os.makedirs(path)
        _init_repo(path, branch="main")
        _commit(path, filename="a.txt", content="a")
        be = ExternalBackend(path)
        wt = tmp_path / "wt-x"
        be.worktree_add(str(wt), "feature-x", "main")
        assert wt.is_dir()
        be.worktree_remove(str(wt))
        assert not wt.exists()
        assert be.branch_exists("feature-x")

    def test_worktree_exists_detects_entry(self, tmp_path: Path) -> None:
        path = str(tmp_path / "repo")
        os.makedirs(path)
        _init_repo(path, branch="main")
        _commit(path, filename="a.txt", content="a")
        be = ExternalBackend(path)
        wt = tmp_path / "wt-here"
        assert be.worktree_exists(str(wt)) is False
        be.worktree_add(str(wt), "feature-here", "main")
        assert be.worktree_exists(str(wt)) is True
        unrelated = tmp_path / "wt-other"
        assert be.worktree_exists(str(unrelated)) is False

    def test_worktree_add_fails_when_branch_exists(self, tmp_path: Path) -> None:
        path = str(tmp_path / "repo")
        os.makedirs(path)
        _init_repo(path, branch="main")
        _commit(path, filename="a.txt", content="a")
        _git(path, "branch", "feature-dup")
        be = ExternalBackend(path)
        wt = tmp_path / "wt-dup"
        with pytest.raises(RuntimeError):
            be.worktree_add(str(wt), "feature-dup", "main")

    def test_worktree_add_unknown_base_raises(self, tmp_path: Path) -> None:
        path = str(tmp_path / "repo")
        os.makedirs(path)
        _init_repo(path, branch="main")
        _commit(path, filename="a.txt", content="a")
        be = ExternalBackend(path)
        wt = tmp_path / "wt-bad"
        with pytest.raises(RuntimeError) as excinfo:
            be.worktree_add(str(wt), "feature", "no-such-base")
        assert "no-such-base" in str(excinfo.value)


class TestExternalBackendGitCommonDir:
    def test_normal_repo_returns_dot_git(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path)
        be = ExternalBackend(path)
        common = be.git_common_dir()
        assert os.path.isabs(common)
        assert os.path.realpath(common) == os.path.realpath(os.path.join(path, ".git"))

    def test_worktree_returns_main_git_dir(self, tmp_path: Path) -> None:
        main = tmp_path / "repo"
        main.mkdir()
        main_path = str(main)
        _init_repo(main_path, branch="main")
        _commit(main_path, filename="a.txt", content="a")

        wt = tmp_path / "wt"
        subprocess.run(
            ["git", "-C", main_path, "worktree", "add", str(wt), "-b", "feature"],
            capture_output=True,
            check=True,
            env=_git_env(),
        )

        be = ExternalBackend(str(wt))
        common = be.git_common_dir()
        assert os.path.realpath(common) == os.path.realpath(os.path.join(main_path, ".git"))


class TestExternalBackendWriteManagedExclude:
    def _read_exclude(self, path: str) -> str:
        with open(os.path.join(path, ".git", "info", "exclude")) as f:
            return f.read()

    def test_creates_info_dir_and_file_when_missing(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path)
        info_dir = tmp_path / ".git" / "info"
        if info_dir.exists():
            for child in info_dir.iterdir():
                child.unlink()
            info_dir.rmdir()
        assert not info_dir.exists()
        be = ExternalBackend(path)
        be.write_managed_exclude(["cdc-tasks/**/plan"])
        content = self._read_exclude(path)
        assert "# >>> cadence (managed) >>>" in content
        assert "cdc-tasks/**/plan" in content
        assert "# <<< cadence (managed) <<<" in content

    def test_appends_block_when_file_exists_without_block(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path)
        target = tmp_path / ".git" / "info" / "exclude"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# user wrote this\nuser-ignore-me\n")
        be = ExternalBackend(path)
        be.write_managed_exclude(["a/**", "b/**"])
        content = target.read_text()
        assert content.startswith("# user wrote this\nuser-ignore-me\n")
        assert "# >>> cadence (managed) >>>" in content
        assert "a/**" in content
        assert "b/**" in content

    def test_replaces_block_in_place_without_duplicating(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path)
        target = tmp_path / ".git" / "info" / "exclude"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# above\nabove-ignore\n")
        be = ExternalBackend(path)
        be.write_managed_exclude(["v1/**"])
        with open(target, "a") as f:
            f.write("\n# below\nbelow-ignore\n")
        be.write_managed_exclude(["v2/**"])
        content = target.read_text()
        assert content.count("# >>> cadence (managed) >>>") == 1
        assert content.count("# <<< cadence (managed) <<<") == 1
        assert "v2/**" in content
        assert "v1/**" not in content
        assert "# above\nabove-ignore\n" in content
        assert "# below\nbelow-ignore\n" in content


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
