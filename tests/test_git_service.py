from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cadence.git import DiffStats, Service
from cadence.git.backend import ExternalBackend

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def _git_env() -> dict[str, str]:
    return {**os.environ, **_GIT_ENV}


def _git(path: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", path, *args],
        capture_output=True,
        text=True,
        check=True,
        env=_git_env(),
    )


def _init_repo(path: str, branch: str = "main") -> None:
    subprocess.run(
        ["git", "init", "-b", branch, path],
        capture_output=True,
        check=True,
    )
    _git(path, "config", "user.email", _GIT_ENV["GIT_COMMITTER_EMAIL"])
    _git(path, "config", "user.name", _GIT_ENV["GIT_COMMITTER_NAME"])


def _make_commit(path: str, filename: str = "README.md", content: str = "hi") -> None:
    p = Path(path) / filename
    p.write_text(content)
    _git(path, "add", filename)
    _git(path, "commit", "-m", "init")


class _Log:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def print(self, fmt: str, *args: object) -> None:
        self.messages.append(fmt % args if args else fmt)

    def warn(self, fmt: str, *args: object) -> None:
        self.messages.append("WARN " + (fmt % args if args else fmt))

    def error(self, fmt: str, *args: object) -> None:
        self.messages.append("ERR " + (fmt % args if args else fmt))


class TestExternalBackendInit:
    def test_valid_repo(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        be = ExternalBackend(str(tmp_path))
        assert os.path.realpath(str(tmp_path)) == be.root()

    def test_not_a_repo(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError):
            ExternalBackend(str(tmp_path))


class TestExternalBackendState:
    def test_head_hash_with_commit(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        be = ExternalBackend(str(tmp_path))
        h = be.head_hash()
        assert len(h) == 40

    def test_head_hash_empty(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        be = ExternalBackend(str(tmp_path))
        assert be.head_hash() == ""

    def test_has_commits_true(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        be = ExternalBackend(str(tmp_path))
        assert be.has_commits() is True

    def test_has_commits_false(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        be = ExternalBackend(str(tmp_path))
        assert be.has_commits() is False

    def test_current_branch(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path), branch="feature-x")
        _make_commit(str(tmp_path))
        be = ExternalBackend(str(tmp_path))
        assert be.current_branch() == "feature-x"

    def test_current_branch_detached(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        _git(str(tmp_path), "checkout", "--detach", "HEAD")
        be = ExternalBackend(str(tmp_path))
        assert be.current_branch() == ""

    def test_get_default_branch_main(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path), branch="main")
        _make_commit(str(tmp_path))
        be = ExternalBackend(str(tmp_path))
        assert be.get_default_branch() == "main"

    def test_get_default_branch_fallback_master(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path), branch="feature")
        be = ExternalBackend(str(tmp_path))
        assert be.get_default_branch() == "master"


class TestExternalBackendBranches:
    def test_create_and_checkout(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        be = ExternalBackend(str(tmp_path))
        assert be.branch_exists("feature") is False
        be.create_branch("feature")
        assert be.branch_exists("feature") is True
        assert be.current_branch() == "feature"
        be.checkout_branch("main")
        assert be.current_branch() == "main"


class TestExternalBackendDirty:
    def test_is_dirty_clean(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        be = ExternalBackend(str(tmp_path))
        assert be.is_dirty() is False

    def test_is_dirty_untracked_not_dirty(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        (tmp_path / "new.txt").write_text("x")
        be = ExternalBackend(str(tmp_path))
        assert be.is_dirty() is False

    def test_is_dirty_modified(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        (tmp_path / "README.md").write_text("changed")
        be = ExternalBackend(str(tmp_path))
        assert be.is_dirty() is True

    def test_has_changes_other_than(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        (tmp_path / "plan.md").write_text("plan")
        (tmp_path / "other.txt").write_text("other")
        be = ExternalBackend(str(tmp_path))
        others = be.has_changes_other_than("plan.md")
        assert "other.txt" in others
        assert "plan.md" not in others

    def test_has_changes_other_than_case_insensitive(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        (tmp_path / "Plan.md").write_text("plan")
        be = ExternalBackend(str(tmp_path))
        others = be.has_changes_other_than("plan.md")
        assert others == []


class TestExtractPathFromPorcelain:
    def test_modified(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        be = ExternalBackend(str(tmp_path))
        assert be._extract_path_from_porcelain(" M foo.txt") == "foo.txt"

    def test_untracked(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        be = ExternalBackend(str(tmp_path))
        assert be._extract_path_from_porcelain("?? new.md") == "new.md"

    def test_rename(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        be = ExternalBackend(str(tmp_path))
        line = "R  old.txt -> new.txt"
        assert be._extract_path_from_porcelain(line) == "new.txt"


class TestDiffFingerprint:
    def test_clean_is_stable(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        be = ExternalBackend(str(tmp_path))
        fp1 = be.diff_fingerprint()
        fp2 = be.diff_fingerprint()
        assert fp1 == fp2

    def test_changes_when_modified(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        be = ExternalBackend(str(tmp_path))
        fp1 = be.diff_fingerprint()
        (tmp_path / "README.md").write_text("changed content")
        fp2 = be.diff_fingerprint()
        assert fp1 != fp2

    def test_changes_when_untracked_added(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        be = ExternalBackend(str(tmp_path))
        fp1 = be.diff_fingerprint()
        (tmp_path / "new.txt").write_text("hello")
        fp2 = be.diff_fingerprint()
        assert fp1 != fp2


class TestDiffStats:
    def test_zero_when_same(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        be = ExternalBackend(str(tmp_path))
        stats = be.diff_stats("main")
        assert stats == DiffStats()

    def test_counts_changes(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path)
        _make_commit(path)
        _git(path, "checkout", "-b", "feature")
        (tmp_path / "README.md").write_text("line1\nline2\nline3\n")
        _git(path, "add", "README.md")
        _git(path, "commit", "-m", "more")
        (tmp_path / "new.txt").write_text("a\nb\n")
        _git(path, "add", "new.txt")
        _git(path, "commit", "-m", "new")
        be = ExternalBackend(path)
        stats = be.diff_stats("main")
        assert stats.files == 2
        assert stats.additions >= 4
        assert stats.deletions >= 1

    def test_missing_base_returns_zero(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        be = ExternalBackend(str(tmp_path))
        stats = be.diff_stats("nonexistent-ref")
        assert stats == DiffStats()


class TestResolveRef:
    def test_local_branch(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        be = ExternalBackend(str(tmp_path))
        assert be._resolve_ref("main") == "refs/heads/main"

    def test_missing(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        be = ExternalBackend(str(tmp_path))
        assert be._resolve_ref("zzz-missing") == ""


class TestCommitTrailer:
    def test_trailer_appended(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path)
        _make_commit(path)
        svc = Service(path, _Log())
        svc.set_commit_trailer("Co-Authored-By: Bot <bot@example.com>")
        msg = svc._append_trailer("add plan: feature")
        assert msg == "add plan: feature\n\nCo-Authored-By: Bot <bot@example.com>"

    def test_no_trailer(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        svc = Service(str(tmp_path), _Log())
        assert svc._append_trailer("foo") == "foo"


class TestResolveFilesystemCase:
    def test_exact_match(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        svc = Service(str(tmp_path), _Log())
        (tmp_path / "plan.md").write_text("x")
        result = svc._resolve_filesystem_case(str(tmp_path / "plan.md"))
        assert result == str(tmp_path / "plan.md")

    def test_case_insensitive_match(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        svc = Service(str(tmp_path), _Log())
        (tmp_path / "Plan.md").write_text("x")
        result = svc._resolve_filesystem_case(str(tmp_path / "plan.md"))
        assert result == str(tmp_path / "Plan.md")


class TestCreateBranchForPlan:
    def test_creates_branch_no_commit(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path)
        plan = tmp_path / "2024-01-01-feature-login.md"
        plan.write_text("# Plan")

        be = ExternalBackend(path)
        head_before = be.head_hash()

        svc = Service(path, _Log())
        svc.create_branch_for_plan(str(plan), "main")

        assert be.current_branch() == "feature-login"
        assert be.head_hash() == head_before
        assert plan.exists()
        status = _git(path, "status", "--porcelain", "-uall").stdout
        assert "2024-01-01-feature-login.md" in status

    def test_does_not_commit_uncommitted_plan(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path)
        plan = tmp_path / "feature-x.md"
        plan.write_text("# Plan")

        be = ExternalBackend(path)
        head_before = be.head_hash()

        svc = Service(path, _Log())
        svc.create_branch_for_plan(str(plan), "main")

        assert be.current_branch() == "feature-x"
        assert be.head_hash() == head_before
        status = _git(path, "status", "--porcelain", "-uall").stdout
        assert "feature-x.md" in status

    def test_skip_when_on_feature_branch(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path)
        _git(path, "checkout", "-b", "feature-x")
        plan = tmp_path / "feature-y.md"
        plan.write_text("# Plan")

        svc = Service(path, _Log())
        svc.create_branch_for_plan(str(plan), "main")

        be = ExternalBackend(path)
        assert be.current_branch() == "feature-x"

    def test_checkout_existing_branch(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path)
        _git(path, "branch", "my-plan")
        plan = tmp_path / "my-plan.md"
        plan.write_text("# Plan")

        svc = Service(path, _Log())
        svc.create_branch_for_plan(str(plan), "main")
        be = ExternalBackend(path)
        assert be.current_branch() == "my-plan"

    def test_rejects_other_dirty_files(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path)
        (tmp_path / "other.txt").write_text("dirty")
        plan = tmp_path / "feature.md"
        plan.write_text("# Plan")
        svc = Service(path, _Log())
        with pytest.raises(RuntimeError):
            svc.create_branch_for_plan(str(plan), "main")

    def test_creates_branch_when_plan_already_tracked_and_clean(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path)
        plan = tmp_path / "feature.md"
        plan.write_text("# Plan")
        _git(path, "add", "feature.md")
        _git(path, "commit", "-m", "pre-add plan")

        be = ExternalBackend(path)
        head_before = be.head_hash()

        svc = Service(path, _Log())
        svc.create_branch_for_plan(str(plan), "main")

        assert be.current_branch() == "feature"
        assert be.head_hash() == head_before


class TestMarkPlanCompleted:
    def test_renames_in_place_no_commit(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path)
        plans_dir = tmp_path / "plans"
        plans_dir.mkdir()
        plan = plans_dir / "2024-01-feature.md"
        plan.write_text("# Plan")
        _git(path, "add", str(plan.relative_to(tmp_path)))
        _git(path, "commit", "-m", "add plan")

        be = ExternalBackend(path)
        head_before = be.head_hash()

        svc = Service(path, _Log())
        svc.mark_plan_completed(str(plan))

        assert not plan.exists()
        assert (plans_dir / "2024-01-feature-completed.md").exists()
        assert be.head_hash() == head_before

    def test_idempotent_if_already_marked(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path)
        plans_dir = tmp_path / "plans"
        plans_dir.mkdir()
        dest = plans_dir / "feature-completed.md"
        dest.write_text("# Plan")
        plan = plans_dir / "feature.md"

        log = _Log()
        svc = Service(path, log)
        svc.mark_plan_completed(str(plan))
        assert dest.read_text() == "# Plan"
        assert not plan.exists()
        assert any("plan already marked completed" in m for m in log.messages)

    def test_collision_when_both_exist_raises(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path)
        plans_dir = tmp_path / "plans"
        plans_dir.mkdir()
        plan = plans_dir / "feature.md"
        plan.write_text("# new")
        dest = plans_dir / "feature-completed.md"
        dest.write_text("# old")

        svc = Service(path, _Log())
        with pytest.raises(FileExistsError):
            svc.mark_plan_completed(str(plan))

        assert plan.read_text() == "# new"
        assert dest.read_text() == "# old"

    def test_preserves_extension(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path)
        plan = tmp_path / "plan.md"
        plan.write_text("# Plan")

        svc = Service(path, _Log())
        svc.mark_plan_completed(str(plan))

        assert not plan.exists()
        assert (tmp_path / "plan-completed.md").exists()

    def test_no_extension(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path)
        plan = tmp_path / "preprompt"
        plan.write_text("# Plan")

        svc = Service(path, _Log())
        svc.mark_plan_completed(str(plan))

        assert not plan.exists()
        assert (tmp_path / "preprompt-completed").exists()

    def test_missing_source_and_target_raises(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path)
        plan = tmp_path / "missing.md"

        svc = Service(path, _Log())
        with pytest.raises(FileNotFoundError):
            svc.mark_plan_completed(str(plan))


class TestEnsureHasCommits:
    def test_noop_when_commits_exist(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path)
        _make_commit(path)

        called = False

        def prompt() -> bool:
            nonlocal called
            called = True
            return True

        svc = Service(path, _Log())
        svc.ensure_has_commits(prompt)
        assert called is False

    def test_creates_initial_commit(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path)
        (tmp_path / "README.md").write_text("hi")

        svc = Service(path, _Log())
        svc.ensure_has_commits(lambda: True)
        assert svc.has_commits() is True

    def test_abort_when_prompt_declines(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path)
        svc = Service(path, _Log())
        with pytest.raises(RuntimeError):
            svc.ensure_has_commits(lambda: False)


class TestServiceSquashCommits:
    def test_squash_collapses_commits(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path, filename="a.txt", content="a")
        _git(path, "checkout", "-b", "feature")
        for name in ("b.txt", "c.txt", "d.txt"):
            (tmp_path / name).write_text(name)
            _git(path, "add", name)
            _git(path, "commit", "-m", name)

        be = ExternalBackend(path)
        base_before = be.merge_base("main")
        diff_before = be.diff_against("main")

        svc = Service(path, _Log())
        svc.squash_commits("main", "squashed message")

        assert be.commits_ahead("main") == 1
        log_subjects = _git(path, "log", "--format=%s", f"{base_before}..HEAD").stdout.strip()
        assert log_subjects == "squashed message"
        assert be.diff_against("main") == diff_before

    def test_squash_appends_trailer(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path, filename="a.txt", content="a")
        _git(path, "checkout", "-b", "feature")
        for name in ("b.txt", "c.txt"):
            (tmp_path / name).write_text(name)
            _git(path, "add", name)
            _git(path, "commit", "-m", name)

        svc = Service(path, _Log())
        svc.set_commit_trailer("Co-Authored-By: Bot <bot@example.com>")
        svc.squash_commits("main", "squashed body")

        body = _git(path, "log", "-1", "--format=%B").stdout
        assert "squashed body" in body
        assert "Co-Authored-By: Bot <bot@example.com>" in body

    def test_squash_refuses_when_no_commits_ahead(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path)
        svc = Service(path, _Log())
        with pytest.raises(RuntimeError):
            svc.squash_commits("main", "msg")

    def test_squash_refuses_with_one_commit_ahead(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path, filename="a.txt", content="a")
        _git(path, "checkout", "-b", "feature")
        (tmp_path / "b.txt").write_text("b")
        _git(path, "add", "b.txt")
        _git(path, "commit", "-m", "b")
        svc = Service(path, _Log())
        with pytest.raises(RuntimeError):
            svc.squash_commits("main", "msg")

    def test_squash_refuses_when_merge_base_missing(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path)
        svc = Service(path, _Log())
        with pytest.raises(RuntimeError):
            svc.squash_commits("nope-missing", "msg")

    def test_squash_rolls_back_when_commit_fails(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path, filename="a.txt", content="a")
        _git(path, "checkout", "-b", "feature")
        for name in ("b.txt", "c.txt", "d.txt"):
            (tmp_path / name).write_text(name)
            _git(path, "add", name)
            _git(path, "commit", "-m", name)

        be = ExternalBackend(path)
        head_before = be.head_hash()

        hooks_dir = Path(_git(path, "rev-parse", "--git-path", "hooks").stdout.strip())
        if not hooks_dir.is_absolute():
            hooks_dir = tmp_path / hooks_dir
        hooks_dir.mkdir(parents=True, exist_ok=True)
        pre_commit = hooks_dir / "pre-commit"
        pre_commit.write_text("#!/bin/sh\nexit 1\n")
        pre_commit.chmod(0o755)

        svc = Service(path, _Log())
        with pytest.raises(RuntimeError):
            svc.squash_commits("main", "msg")

        assert be.head_hash() == head_before
        assert be.commits_ahead("main") == 3

    def test_commits_ahead_wrapper(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path, filename="a.txt", content="a")
        _git(path, "checkout", "-b", "feature")
        for name in ("b.txt", "c.txt"):
            (tmp_path / name).write_text(name)
            _git(path, "add", name)
            _git(path, "commit", "-m", name)
        svc = Service(path, _Log())
        assert svc.commits_ahead("main") == 2


class TestServiceCheckoutBranch:
    def test_switches_to_existing_branch(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path)
        _git(path, "checkout", "-b", "side")
        (tmp_path / "x.txt").write_text("x")
        _git(path, "add", "x.txt")
        _git(path, "commit", "-m", "x")
        _git(path, "checkout", "main")

        log = _Log()
        svc = Service(path, log)
        svc.checkout_branch("side")

        be = ExternalBackend(path)
        assert be.current_branch() == "side"
        assert any("switched to branch side" in m for m in log.messages)

    def test_unknown_branch_raises(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path)
        svc = Service(path, _Log())
        with pytest.raises(RuntimeError):
            svc.checkout_branch("no-such-branch")


class TestServiceCreateBranchFrom:
    def test_creates_branch_from_base(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path, filename="a.txt", content="a")

        log = _Log()
        svc = Service(path, log)
        svc.create_branch_from("feature", "main")

        be = ExternalBackend(path)
        assert be.current_branch() == "feature"
        assert any("created branch feature from main" in m for m in log.messages)

    def test_unknown_base_raises(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path)
        svc = Service(path, _Log())
        with pytest.raises(RuntimeError):
            svc.create_branch_from("feature", "no-such-base")


class TestServiceDelegation:
    def test_is_default_branch_true(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path)
        svc = Service(path, _Log())
        assert svc.is_default_branch("main") is True

    def test_is_default_branch_origin_prefix(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path)
        svc = Service(path, _Log())
        assert svc.is_default_branch("origin/main") is True

    def test_is_default_branch_false(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="feature")
        _make_commit(path)
        svc = Service(path, _Log())
        assert svc.is_default_branch("main") is False

    def test_is_default_branch_empty_matches_main(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path)
        svc = Service(path, _Log())
        assert svc.is_default_branch("") is True

    def test_branch_exists(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path)
        svc = Service(path, _Log())
        assert svc.branch_exists("main") is True
        assert svc.branch_exists("nope") is False


class TestDiffAgainstPaths:
    @staticmethod
    def _diff_calls(captured: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
        return [args for args in captured if args and args[0] == "diff"]

    def test_invokes_git_with_pathspecs(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        be = ExternalBackend(str(tmp_path))
        captured: list[tuple[str, ...]] = []
        original = be._run_with_status

        def fake_run_with_status(*args: str, **kwargs: object) -> tuple[int, str, str]:
            captured.append(args)
            if args and args[0] == "diff":
                return 0, "fake-diff", ""
            return original(*args, **kwargs)  # type: ignore[arg-type]

        be._run_with_status = fake_run_with_status  # type: ignore[method-assign]
        out = be.diff_against("main", paths=["src/api", "internal/api"])
        assert out == "fake-diff"
        diff_calls = self._diff_calls(captured)
        assert len(diff_calls) == 1
        argv = diff_calls[0]
        assert argv[1] == "refs/heads/main...HEAD"
        assert "--" in argv
        sep = argv.index("--")
        assert list(argv[sep + 1 :]) == ["src/api", "internal/api"]

    def test_no_separator_when_paths_omitted(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        be = ExternalBackend(str(tmp_path))
        captured: list[tuple[str, ...]] = []
        original = be._run_with_status

        def fake_run_with_status(*args: str, **kwargs: object) -> tuple[int, str, str]:
            captured.append(args)
            if args and args[0] == "diff":
                return 0, "", ""
            return original(*args, **kwargs)  # type: ignore[arg-type]

        be._run_with_status = fake_run_with_status  # type: ignore[method-assign]
        be.diff_against("main")
        diff_calls = self._diff_calls(captured)
        assert len(diff_calls) == 1
        assert "--" not in diff_calls[0]

    def test_no_separator_when_paths_empty(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        be = ExternalBackend(str(tmp_path))
        captured: list[tuple[str, ...]] = []
        original = be._run_with_status

        def fake_run_with_status(*args: str, **kwargs: object) -> tuple[int, str, str]:
            captured.append(args)
            if args and args[0] == "diff":
                return 0, "", ""
            return original(*args, **kwargs)  # type: ignore[arg-type]

        be._run_with_status = fake_run_with_status  # type: ignore[method-assign]
        be.diff_against("main", paths=[])
        diff_calls = self._diff_calls(captured)
        assert len(diff_calls) == 1
        assert "--" not in diff_calls[0]

    def test_real_diff_filters_to_pathspec(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path, filename="a.txt", content="a\n")
        _git(path, "checkout", "-b", "feature")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "api.py").write_text("def hi(): ...\n")
        (tmp_path / "other.txt").write_text("other\n")
        _git(path, "add", "src/api.py", "other.txt")
        _git(path, "commit", "-m", "feature")

        be = ExternalBackend(path)
        scoped = be.diff_against("main", paths=["src"])
        assert "src/api.py" in scoped
        assert "other.txt" not in scoped

        unscoped = be.diff_against("main")
        assert "src/api.py" in unscoped
        assert "other.txt" in unscoped


class TestServiceDiffAgainst:
    def test_forwards_paths_kwarg(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        svc = Service(str(tmp_path), _Log())
        mock = MagicMock()
        mock.diff_against.return_value = "result-diff"
        svc._repo = mock

        out = svc.diff_against("main", paths=["src/api"])
        assert out == "result-diff"
        mock.diff_against.assert_called_once_with("main", paths=["src/api"])

    def test_default_paths_is_none(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        svc = Service(str(tmp_path), _Log())
        mock = MagicMock()
        mock.diff_against.return_value = ""
        svc._repo = mock

        svc.diff_against("main")
        mock.diff_against.assert_called_once_with("main", paths=None)


class TestServiceWorktreePassThroughs:
    def _service_with_mock_backend(self, tmp_path: Path) -> tuple[Service, MagicMock]:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        svc = Service(str(tmp_path), _Log())
        mock = MagicMock()
        svc._repo = mock
        return svc, mock

    def test_worktree_add_forwards(self, tmp_path: Path) -> None:
        svc, mock = self._service_with_mock_backend(tmp_path)
        svc.worktree_add("/some/path", "feature", "main")
        mock.worktree_add.assert_called_once_with("/some/path", "feature", "main")

    def test_worktree_add_propagates_error(self, tmp_path: Path) -> None:
        svc, mock = self._service_with_mock_backend(tmp_path)
        mock.worktree_add.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError, match="boom"):
            svc.worktree_add("/p", "b", "main")

    def test_worktree_remove_forwards(self, tmp_path: Path) -> None:
        svc, mock = self._service_with_mock_backend(tmp_path)
        svc.worktree_remove("/some/path")
        mock.worktree_remove.assert_called_once_with("/some/path")

    def test_worktree_remove_propagates_error(self, tmp_path: Path) -> None:
        svc, mock = self._service_with_mock_backend(tmp_path)
        mock.worktree_remove.side_effect = RuntimeError("rm failed")
        with pytest.raises(RuntimeError, match="rm failed"):
            svc.worktree_remove("/p")

    def test_worktree_exists_forwards_true(self, tmp_path: Path) -> None:
        svc, mock = self._service_with_mock_backend(tmp_path)
        mock.worktree_exists.return_value = True
        assert svc.worktree_exists("/p") is True
        mock.worktree_exists.assert_called_once_with("/p")

    def test_worktree_exists_forwards_false(self, tmp_path: Path) -> None:
        svc, mock = self._service_with_mock_backend(tmp_path)
        mock.worktree_exists.return_value = False
        assert svc.worktree_exists("/p") is False
