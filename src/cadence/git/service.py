from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from cadence.git.backend import DiffStats, ExternalBackend
from cadence.plan import extract_branch_name


class Logger(Protocol):
    def print(self, fmt: str, *args: object) -> None: ...
    def warn(self, fmt: str, *args: object) -> None: ...
    def error(self, fmt: str, *args: object) -> None: ...


def completed_plan_path(plan_file: str) -> Path:
    p = Path(plan_file)
    return p.with_name(p.stem + "-completed" + p.suffix)


class Service:
    def __init__(self, path: str, log: Logger) -> None:
        self._repo = ExternalBackend(path)
        self._log = log
        self._trailer: str = ""

    def set_log(self, log: Logger) -> None:
        self._log = log

    def set_commit_trailer(self, trailer: str) -> None:
        self._trailer = trailer or ""

    def _append_trailer(self, msg: str) -> str:
        if not self._trailer:
            return msg
        return msg + "\n\n" + self._trailer

    def root(self) -> str:
        return self._repo.root()

    def head_hash(self) -> str:
        return self._repo.head_hash()

    def diff_fingerprint(self) -> str:
        return self._repo.diff_fingerprint()

    def current_branch(self) -> str:
        return self._repo.current_branch()

    def get_default_branch(self) -> str:
        return self._repo.get_default_branch()

    def has_commits(self) -> bool:
        return self._repo.has_commits()

    def is_dirty(self) -> bool:
        return self._repo.is_dirty()

    def dirty_status_lines(self) -> list[str]:
        return self._repo.dirty_status_lines()

    def diff_stats(self, base_branch: str) -> DiffStats:
        return self._repo.diff_stats(base_branch)

    def diff_against(self, base: str, *, paths: list[str] | None = None) -> str:
        return self._repo.diff_against(base, paths=paths)

    def is_default_branch(self, default_branch: str) -> bool:
        current = self._repo.current_branch()
        if not current:
            return False
        if default_branch:
            trimmed = default_branch
            if trimmed.startswith("origin/"):
                trimmed = trimmed[len("origin/") :]
            return current == trimmed
        return current in ("main", "master")

    def create_branch(self, name: str) -> None:
        self._repo.create_branch(name)

    def checkout_branch(self, name: str) -> None:
        self._repo.checkout_branch(name)
        self._log.print("switched to branch %s", name)

    def create_branch_from(self, name: str, base: str) -> None:
        self._repo.create_branch_from(name, base)
        self._log.print("created branch %s from %s", name, base)

    def branch_exists(self, name: str) -> bool:
        return self._repo.branch_exists(name)

    def remote_branch_exists(self, name: str) -> bool:
        return self._repo._ref_exists(f"refs/remotes/origin/{name}")

    def worktree_add(self, path: str, branch: str, base: str) -> None:
        self._repo.worktree_add(path, branch, base)

    def worktree_remove(self, path: str) -> None:
        self._repo.worktree_remove(path)

    def worktree_exists(self, path: str) -> bool:
        return self._repo.worktree_exists(path)

    def ensure_local_ignore(self, tasks_root: str) -> None:
        root = (tasks_root or "").strip().replace("\\", "/").rstrip("/")
        if not root:
            return
        patterns = [
            f"{root}/**/plan",
            f"{root}/**/plan-completed",
            f"{root}/**/progress-*.txt",
            f"{root}/**/progress-*.jsonl",
            f"{root}/**/config.yaml",
            f"{root}/**/report-*.md",
        ]
        try:
            self._repo.write_managed_exclude(patterns)
        except (RuntimeError, OSError) as exc:
            self._log.warn("could not update .git/info/exclude: %s", exc)

    def create_branch_for_plan(self, plan_file: str, default_branch: str) -> None:
        resolved_plan = self._resolve_filesystem_case(plan_file)
        branch = self._prepare_plan_branch(resolved_plan, default_branch)
        if not branch:
            return

        if self._repo.branch_exists(branch):
            self._repo.checkout_branch(branch)
            self._log.print("switched to existing branch %s", branch)
        else:
            self._repo.create_branch(branch)
            self._log.print("created branch %s", branch)

    def _prepare_plan_branch(self, plan_file: str, default_branch: str) -> str:
        if not self.is_default_branch(default_branch):
            return ""

        branch = extract_branch_name(plan_file)
        if not branch:
            raise RuntimeError(f"cannot extract branch name from plan: {plan_file}")

        other = self._repo.has_changes_other_than(plan_file)
        if other:
            raise RuntimeError(
                "repository has uncommitted changes other than the plan file: " + ", ".join(other)
            )

        return branch

    def mark_plan_completed(self, plan_file: str) -> None:
        resolved = self._resolve_filesystem_case(plan_file)
        src = Path(resolved)
        dst = completed_plan_path(resolved)

        if not src.exists():
            if dst.exists():
                self._log.print("plan already marked completed: %s", str(dst))
                return
            raise FileNotFoundError(f"plan file not found: {plan_file}")

        if dst.exists():
            raise FileExistsError(f"completed plan already exists, refusing to overwrite: {dst}")

        os.rename(str(src), str(dst))
        self._log.print("marked plan completed: %s", str(dst))

    def commits_ahead(self, default_branch: str) -> int:
        return self._repo.commits_ahead(default_branch)

    def squash_commits(self, default_branch: str, message: str) -> None:
        base = self._repo.merge_base(default_branch)
        if not base:
            raise RuntimeError(f"merge-base not found for {default_branch}")
        if self._repo.commits_ahead(default_branch) < 2:
            raise RuntimeError("nothing to squash beyond base")
        pre_head = self._repo.head_hash()
        self._repo.reset_soft(base)
        try:
            self._repo.commit_with_message(self._append_trailer(message))
        except RuntimeError:
            if pre_head:
                self._repo.reset_hard(pre_head)
            raise

    def ensure_has_commits(self, prompt_fn: Callable[[], bool]) -> None:
        if self._repo.has_commits():
            return
        if not prompt_fn():
            raise RuntimeError("repository has no commits; aborted by user")
        self._repo.create_initial_commit(self._append_trailer("initial commit"))
        self._log.print("created initial commit")

    def _resolve_filesystem_case(self, path: str) -> str:
        parent = os.path.dirname(path) or "."
        name = os.path.basename(path)
        try:
            entries = os.listdir(parent)
        except OSError:
            return path
        if name in entries:
            return path
        lowered = name.casefold()
        for entry in entries:
            if entry.casefold() == lowered:
                return os.path.join(parent, entry) if os.path.dirname(path) else entry
        return path
