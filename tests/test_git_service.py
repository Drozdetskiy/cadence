from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from rlx.git import DiffStats, Service
from rlx.git.backend import ExternalBackend

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

    def test_file_has_changes_true(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        (tmp_path / "README.md").write_text("changed")
        be = ExternalBackend(str(tmp_path))
        assert be.file_has_changes("README.md") is True

    def test_file_has_changes_false(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        be = ExternalBackend(str(tmp_path))
        assert be.file_has_changes("README.md") is False

    def test_has_changes_other_than(self, tmp_path: Path) -> None:
        _init_repo(str(tmp_path))
        _make_commit(str(tmp_path))
        (tmp_path / "plan.md").write_text("plan")
        (tmp_path / "other.txt").write_text("other")
        be = ExternalBackend(str(tmp_path))
        others = be.has_changes_other_than("plan.md")
        assert "other.txt" in others
        assert "plan.md" not in others

    def test_has_changes_other_than_case_insensitive(
        self, tmp_path: Path
    ) -> None:
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
    def test_creates_branch_and_commits(self, tmp_path: Path) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path)
        plan = tmp_path / "2024-01-01-feature-login.md"
        plan.write_text("# Plan")

        svc = Service(path, _Log())
        svc.create_branch_for_plan(str(plan), "main")

        be = ExternalBackend(path)
        assert be.current_branch() == "feature-login"
        result = _git(path, "log", "--format=%s", "-n", "1")
        assert "add plan: feature-login" in result.stdout

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

    def test_no_commit_when_plan_already_committed(
        self, tmp_path: Path
    ) -> None:
        path = str(tmp_path)
        _init_repo(path, branch="main")
        _make_commit(path)
        plan = tmp_path / "feature.md"
        plan.write_text("# Plan")
        _git(path, "add", "feature.md")
        _git(path, "commit", "-m", "pre-add plan")

        svc = Service(path, _Log())
        svc.create_branch_for_plan(str(plan), "main")
        be = ExternalBackend(path)
        assert be.current_branch() == "feature"
        result = _git(path, "log", "--format=%s", "-n", "1")
        assert result.stdout.strip() == "pre-add plan"


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
